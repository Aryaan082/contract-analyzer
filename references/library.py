"""The reference library: loads the JSON records and checks them before anything uses them.

Every record is cross-checked when loaded, so a hand-edited or half-written file fails loudly
instead of quietly producing wrong matches: selectors must be the hash of their signatures, the
code must hash to `code_hash`, and every listed function must appear as a PUSH4 in the code.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pydantic import ValidationError

from references.abi import keccak_hex, parse_hex, selector_of
from references.bytecode import Metadata, push4_values, push_constants, split_metadata
from references.models import LibraryStats, Reference

DATA_DIR = Path(__file__).resolve().parent / "data"


class LibraryError(Exception):
    """The library cannot be loaded or does not contain what was asked for."""


class UnknownReference(LibraryError):
    pass


@dataclass(frozen=True, slots=True)
class Prepared:
    """A reference plus everything derived from it once, at load time."""

    reference: Reference
    selectors: frozenset[str]
    code: bytes | None
    body: bytes | None  # code without the metadata tail
    metadata: Metadata | None
    constants: frozenset[bytes]  # see bytecode.push_constants


def _problems(reference: Reference) -> list[str]:
    found = []
    seen = set()
    for function in reference.functions:
        if selector_of(function.signature) != function.selector:
            found.append(f"{function.selector} is not the selector of {function.signature}")
        if function.selector in seen:
            found.append(f"{function.selector} is listed twice")
        seen.add(function.selector)
    if reference.code is None:
        return found
    try:
        code = parse_hex(reference.code)
    except ValueError:
        return [*found, "code is not valid hex"]
    if keccak_hex(code) != reference.code_hash:
        found.append("code_hash does not match the code")
    previous_end = 0
    for start, length in sorted(reference.variable_ranges):
        if start < previous_end or length <= 0 or start + length > len(code):
            found.append(f"variable range ({start}, {length}) is overlapping or outside the code")
        previous_end = start + length
    missing = seen - push4_values(code)
    if missing:
        found.append(f"functions not found as PUSH4 in the code: {sorted(missing)}")
    return found


def _prepare(reference: Reference) -> Prepared:
    selectors = frozenset(f.selector for f in reference.functions)
    if reference.code is None:
        return Prepared(reference, selectors, None, None, None, frozenset())
    code = parse_hex(reference.code)
    body, metadata = split_metadata(code)
    constants = push_constants(
        body,
        skip_ranges=reference.variable_ranges,
        exclude=frozenset(bytes.fromhex(s[2:]) for s in selectors),
    )
    return Prepared(reference, selectors, code, body, metadata, constants)


class Library:
    def __init__(self, references: Iterable[Reference]) -> None:
        ordered = sorted(references, key=lambda r: r.id)
        problems = []
        ids = [r.id for r in ordered]
        problems += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        for reference in ordered:
            problems += [f"{reference.id}: {p}" for p in _problems(reference)]
        if problems:
            raise LibraryError("Invalid reference library:\n  " + "\n  ".join(problems))
        self._prepared = {r.id: _prepare(r) for r in ordered}
        index = defaultdict(list)
        for prepared in self._prepared.values():
            for function in prepared.reference.functions:
                index[function.selector].append((prepared.reference.id, function.signature))
        self._index = {selector: tuple(entries) for selector, entries in index.items()}

    def __len__(self) -> int:
        return len(self._prepared)

    def get(self, reference_id: str) -> Prepared:
        try:
            return self._prepared[reference_id]
        except KeyError:
            known = ", ".join(self._prepared)
            raise UnknownReference(
                f"No reference with id {reference_id[:60]!r}. Known ids: {known}."
            ) from None

    @property
    def all(self) -> Sequence[Prepared]:
        return tuple(self._prepared.values())

    @property
    def contracts(self) -> Sequence[Prepared]:
        return tuple(p for p in self._prepared.values() if p.reference.kind == "contract")

    def names_for(self, selector: str) -> tuple[tuple[str, str], ...]:
        """(reference id, signature) for every library entry that has this selector."""
        return self._index.get(selector, ())

    @property
    def digest(self) -> str:
        """Changes whenever any reference's code or function list changes. A result that cites
        the digest can be reproduced against the same library."""
        lines = [
            f"{p.reference.id}|{p.reference.code_hash}|{','.join(sorted(p.selectors))}"
            for p in self._prepared.values()
        ]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def stats(self) -> LibraryStats:
        entries = [p.reference for p in self._prepared.values()]
        return LibraryStats(
            digest=self.digest,
            contracts=sum(r.kind == "contract" for r in entries),
            modules=sum(r.kind == "module" for r in entries),
            compiled=sum(r.origin.type == "compiled" for r in entries),
            deployed=sum(r.origin.type == "deployed" for r in entries),
            spec=sum(r.origin.type == "spec" for r in entries),
            functions=sum(len(r.functions) for r in entries),
            distinct_selectors=len(self._index),
        )


def load_library(directory: Path = DATA_DIR) -> Library:
    references = []
    problems = []
    for path in sorted(directory.glob("*.json")):
        try:
            reference = Reference.model_validate(json.loads(path.read_text()))
        except (ValueError, ValidationError) as err:
            problems.append(f"{path.name}: {str(err).splitlines()[0]}")
            continue
        if reference.id != path.stem:
            problems.append(f"{path.name}: id {reference.id!r} does not match the file name")
        references.append(reference)
    if problems:
        raise LibraryError("Invalid reference library:\n  " + "\n  ".join(problems))
    return Library(references)


@cache
def get_library() -> Library:
    return load_library()
