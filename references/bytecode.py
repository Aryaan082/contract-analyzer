"""Reading EVM runtime bytecode without running it.

Four things live here: walking instructions (skipping PUSH data), finding the compiler's metadata
tail, collecting the constants a program contains, and scanning for two dangerous opcodes.

The metadata tail: Solidity appends a CBOR blob (compiler version, sometimes a hash of the source
metadata) to the runtime code, and writes its length in the last 2 bytes. It is data, not code, so
every comparison here drops it first. The tail is only accepted when it parses as a small CBOR map
with a known key; anything else is left alone, so code without a tail (Vyper, hand-written) is
never cut short.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from references.models import OpcodeScan

PUSH1 = 0x60
PUSH4 = 0x63
PUSH32 = 0x7F
DELEGATECALL = 0xF4
SELFDESTRUCT = 0xFF

# Constants this short are loop counters and offsets, not fingerprints.
MIN_CONSTANT_BYTES = 4
MAX_OFFSETS_REPORTED = 20

_KNOWN_METADATA_KEYS = {"ipfs", "bzzr0", "bzzr1", "solc", "experimental"}


@dataclass(frozen=True, slots=True)
class Instruction:
    offset: int
    opcode: int
    operand: bytes  # PUSH only; shorter than the opcode asks for if the code ends mid-instruction


def disassemble(code: bytes) -> Iterator[Instruction]:
    position = 0
    while position < len(code):
        opcode = code[position]
        if PUSH1 <= opcode <= PUSH32:
            size = opcode - PUSH1 + 1
            yield Instruction(position, opcode, code[position + 1 : position + 1 + size])
            position += 1 + size
        else:
            yield Instruction(position, opcode, b"")
            position += 1


@dataclass(frozen=True, slots=True)
class Metadata:
    solc: str | None  # "0.8.28"; None if the tail has no (readable) compiler version
    keys: tuple[str, ...]
    length: int  # bytes the tail takes up, including its 2-byte length field


def _read_item(data: bytes, position: int, depth: int = 0) -> tuple[object, int]:
    """One CBOR item, limited to what solc writes: byte strings, text, booleans, small maps."""
    head = data[position]
    major, info = head >> 5, head & 0x1F
    position += 1
    if info < 24:
        argument = info
    elif info == 24:
        argument = data[position]
        position += 1
    elif info == 25:
        argument = int.from_bytes(data[position : position + 2], "big")
        position += 2
    else:
        raise ValueError("unsupported CBOR length")
    if major in (2, 3):
        end = position + argument
        if end > len(data):
            raise ValueError("CBOR string runs past the end")
        raw = data[position:end]
        return (raw if major == 2 else raw.decode("utf-8")), end
    if major == 5 and depth == 0 and argument <= 8:
        entries = {}
        for _ in range(argument):
            key, position = _read_item(data, position, depth + 1)
            value, position = _read_item(data, position, depth + 1)
            if not isinstance(key, str):
                raise ValueError("CBOR map key is not text")
            entries[key] = value
        return entries, position
    if major == 7 and info in (20, 21):
        return info == 21, position
    raise ValueError("unsupported CBOR item")


def split_metadata(code: bytes) -> tuple[bytes, Metadata | None]:
    """Return (code without its metadata tail, the tail's contents or None)."""
    if len(code) < 4:
        return code, None
    length = int.from_bytes(code[-2:], "big")
    if length == 0 or length + 2 > len(code):
        return code, None
    start = len(code) - 2 - length
    try:
        entries, end = _read_item(code[start:-2], 0)
    except (ValueError, IndexError, UnicodeDecodeError):
        return code, None
    if not isinstance(entries, dict) or end != length or not _KNOWN_METADATA_KEYS & entries.keys():
        return code, None
    solc = entries.get("solc")
    if isinstance(solc, bytes) and len(solc) == 3:
        version = ".".join(str(b) for b in solc)
    else:
        version = solc if isinstance(solc, str) else None
    return code[:start], Metadata(version, tuple(entries), length + 2)


def push_constants(
    body: bytes,
    *,
    skip_ranges: Iterable[tuple[int, int]] = (),
    exclude: frozenset[bytes] = frozenset(),
) -> frozenset[bytes]:
    """Every PUSH operand of at least 4 bytes: event topics, error selectors, revert strings,
    magic numbers. Unlike the instruction sequence these mostly survive a change of compiler
    version or optimizer, which is why they are the code fingerprint.

    `skip_ranges` are (start, length) spans of bytes known to vary between copies of the same
    program (immutables, embedded addresses); a PUSH whose operand touches one is ignored.
    `exclude` drops chosen values, used to keep function selectors from being counted twice.
    """
    spans = tuple(skip_ranges)
    found = set()
    for instruction in disassemble(body):
        operand = instruction.operand
        if len(operand) < MIN_CONSTANT_BYTES or operand in exclude:
            continue
        first = instruction.offset + 1
        if any(first < start + length and start < first + len(operand) for start, length in spans):
            continue
        found.add(operand)
    return frozenset(found)


def push4_values(code: bytes) -> frozenset[str]:
    """Every 4-byte PUSH operand as `0x` + 8 hex. A superset of the selectors a contract answers
    to; a function's selector is always among them."""
    return frozenset(
        "0x" + ins.operand.hex()
        for ins in disassemble(code)
        if ins.opcode == PUSH4 and len(ins.operand) == 4
    )


def scan_opcodes(code: bytes) -> OpcodeScan:
    """Find DELEGATECALL and SELFDESTRUCT instructions.

    Walks instructions, so PUSH data is skipped, and stops before the metadata tail. Even so it is
    a linear sweep: data placed after the code can decode as instructions, so a hit is evidence
    that the opcode may be reachable, not proof.
    """
    body, metadata = split_metadata(code)
    found: dict[int, list[int]] = {DELEGATECALL: [], SELFDESTRUCT: []}
    truncated = False
    for instruction in disassemble(body):
        if instruction.opcode in found:
            found[instruction.opcode].append(instruction.offset)
        elif PUSH1 <= instruction.opcode <= PUSH32:
            truncated = truncated or len(instruction.operand) < instruction.opcode - PUSH1 + 1
    return OpcodeScan(
        delegatecall_count=len(found[DELEGATECALL]),
        delegatecall_offsets=tuple(found[DELEGATECALL][:MAX_OFFSETS_REPORTED]),
        selfdestruct_count=len(found[SELFDESTRUCT]),
        selfdestruct_offsets=tuple(found[SELFDESTRUCT][:MAX_OFFSETS_REPORTED]),
        metadata_skipped=metadata is not None,
        truncated_push=truncated,
        bytes_scanned=len(body),
    )
