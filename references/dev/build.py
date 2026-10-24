"""Rebuild the compiled references and the test fixtures: `python -m references.dev.build`.

Needs Foundry (`forge`) and a checkout of OpenZeppelin at the pinned release in
references/.cache/ (fetched here if missing, and checked against the pinned commit). Developer
tool only: tests and the library at runtime never need Foundry or OpenZeppelin.

The compiler settings live in references/dev/forge/foundry.toml and are copied into every record.
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from references.abi import keccak_hex
from references.library import DATA_DIR, Library
from references.models import CompiledOrigin, Function, Reference, SpecOrigin

ROOT = Path(__file__).resolve().parents[2]
FORGE_DIR = ROOT / "references" / "dev" / "forge"
OZ_DIR = ROOT / "references" / ".cache" / "openzeppelin-contracts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "references"

OZ_REPOSITORY = "OpenZeppelin/openzeppelin-contracts"
OZ_TAG = "v5.7.0"
OZ_COMMIT = "cab19933c33c2ad1d4c7a84864a3601dddfd16f3"
OZ_BLOB = f"https://github.com/{OZ_REPOSITORY}/blob/{OZ_TAG}/contracts"


@dataclass(frozen=True)
class Spec:
    id: str
    artifact: str  # contract name in references/dev/forge/src/refs/
    name: str
    family: str
    kind: str
    path: str  # file inside OpenZeppelin's contracts/
    notes: str = ""


SPECS = (
    Spec(
        "oz-erc20",
        "RefERC20",
        "OpenZeppelin ERC20",
        "erc20",
        "contract",
        "token/ERC20/ERC20.sol",
        "ERC20 as is: no mint, burn, owner or pause.",
    ),
    Spec(
        "oz-erc721",
        "RefERC721",
        "OpenZeppelin ERC721",
        "erc721",
        "contract",
        "token/ERC721/ERC721.sol",
    ),
    Spec(
        "oz-ownable",
        "RefOwnable",
        "OpenZeppelin Ownable",
        "ownable",
        "module",
        "access/Ownable.sol",
    ),
    Spec(
        "oz-pausable",
        "RefPausable",
        "OpenZeppelin Pausable",
        "pausable",
        "module",
        "utils/Pausable.sol",
        "OpenZeppelin only exposes paused(). pause() and unpause() are the conventional public "
        "wrappers, added in references/dev/forge/src/refs/RefPausable.sol.",
    ),
    Spec(
        "oz-erc1967-proxy",
        "RefERC1967Proxy",
        "OpenZeppelin ERC1967Proxy",
        "proxy",
        "contract",
        "proxy/ERC1967/ERC1967Proxy.sol",
        "No functions of its own: every call is forwarded.",
    ),
    Spec(
        "oz-transparent-proxy",
        "RefTransparentUpgradeableProxy",
        "OpenZeppelin TransparentUpgradeableProxy",
        "proxy",
        "contract",
        "proxy/transparent/TransparentUpgradeableProxy.sol",
        "The admin is an immutable, so its bytes vary between copies.",
    ),
    Spec(
        "oz-beacon-proxy",
        "RefBeaconProxy",
        "OpenZeppelin BeaconProxy",
        "proxy",
        "contract",
        "proxy/beacon/BeaconProxy.sol",
        "The beacon address is an immutable.",
    ),
    Spec(
        "oz-upgradeable-beacon",
        "RefUpgradeableBeacon",
        "OpenZeppelin UpgradeableBeacon",
        "proxy",
        "contract",
        "proxy/beacon/UpgradeableBeacon.sol",
    ),
)

FIXTURES = (
    ("FixtureERC20Plain", "A standard ERC20: the reference's code with a different name."),
    ("FixtureERC20MintBlacklist", "A standard ERC20 plus an admin-only mint and blacklist."),
    ("FixtureERC20Wizard", "ERC20 + Ownable + Pausable + owner-only mint."),
    ("FixtureERC20TransferCap", "Same function list as ERC20; a per-transfer cap in the code."),
)

# EIP-1167 minimal proxy, as written in the standard: 10 bytes, the 20-byte implementation address
# (zeros here), then 15 bytes.
EIP1167_CODE = "0x363d3d373d3d3d363d73" + "00" * 20 + "5af43d82803e903d91602b57fd5bf3"


def forge_binary() -> str:
    found = shutil.which("forge") or str(Path.home() / ".foundry" / "bin" / "forge")
    if not Path(found).exists():
        sys.exit("forge not found. Install Foundry (https://getfoundry.sh).")
    return found


def ensure_openzeppelin() -> None:
    if not OZ_DIR.exists():
        OZ_DIR.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{OZ_REPOSITORY}"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", OZ_TAG, "--quiet", url, str(OZ_DIR)],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(OZ_DIR), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != OZ_COMMIT:
        sys.exit(f"{OZ_DIR} is at {head}, expected {OZ_COMMIT} ({OZ_TAG}). Delete it and rerun.")


def forge(profile: str) -> str:
    env = {**os.environ, "FOUNDRY_PROFILE": profile}
    subprocess.run([forge_binary(), "build", "--offline"], cwd=FORGE_DIR, env=env, check=True)
    version = subprocess.run(
        [forge_binary(), "--version"], capture_output=True, text=True, check=True
    ).stdout.splitlines()[0]
    return version.removeprefix("forge ").strip()


def artifact(profile: str, name: str) -> dict:
    path = FORGE_DIR / "out" / profile / f"{name}.sol" / f"{name}.json"
    return json.loads(path.read_text())


def functions_of(built: dict) -> tuple[Function, ...]:
    pairs = {"0x" + sel: sig for sig, sel in built["methodIdentifiers"].items()}
    return tuple(Function(selector=s, signature=pairs[s]) for s in sorted(pairs))


def ranges_of(built: dict) -> tuple[tuple[int, int], ...]:
    found = built["deployedBytecode"].get("immutableReferences", {})
    return tuple(sorted({(r["start"], r["length"]) for refs in found.values() for r in refs}))


def compiled_reference(spec: Spec, forge_version: str) -> Reference:
    built = artifact("refs", spec.artifact)
    meta = built["metadata"]
    code = built["deployedBytecode"]["object"]
    is_contract = spec.kind == "contract"
    return Reference(
        id=spec.id,
        name=spec.name,
        family=spec.family,
        kind=spec.kind,
        origin=CompiledOrigin(
            repository=OZ_REPOSITORY,
            tag=OZ_TAG,
            commit=OZ_COMMIT,
            wrapper=f"references/dev/forge/src/refs/{spec.artifact}.sol",
            solc=meta["compiler"]["version"],
            optimizer_runs=meta["settings"]["optimizer"]["runs"],
            evm_version=meta["settings"]["evmVersion"],
            forge=forge_version,
        ),
        source_url=f"{OZ_BLOB}/{spec.path}",
        functions=functions_of(built),
        code=code if is_contract else None,
        code_hash=keccak_hex(bytes.fromhex(code[2:])) if is_contract else None,
        variable_ranges=ranges_of(built) if is_contract else (),
        notes=spec.notes,
    )


def eip1167_reference() -> Reference:
    return Reference(
        id="eip1167-minimal-proxy",
        name="EIP-1167 minimal proxy (clone)",
        family="proxy",
        kind="contract",
        origin=SpecOrigin(standard="EIP-1167", url="https://eips.ethereum.org/EIPS/eip-1167"),
        source_url="https://eips.ethereum.org/EIPS/eip-1167",
        functions=(),
        code=EIP1167_CODE,
        code_hash=keccak_hex(bytes.fromhex(EIP1167_CODE[2:])),
        variable_ranges=((10, 20),),
        notes="Bytes as written in the standard. The embedded implementation address (zeros here) "
        "differs in every clone, hence the variable range.",
    )


def write_fixtures() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, description in FIXTURES:
        built = artifact("fixtures", name)
        functions = functions_of(built)
        record = {
            "name": name,
            "description": description,
            "solc": built["metadata"]["compiler"]["version"],
            "code": built["deployedBytecode"]["object"],
            "selectors": [f.selector for f in functions],
            "signatures": {f.selector: f.signature for f in functions},
        }
        (FIXTURE_DIR / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"fixture {name}: {len(functions)} functions")


def main() -> None:
    ensure_openzeppelin()
    version = forge("default")
    forge("fixtures")
    references = [compiled_reference(spec, version) for spec in SPECS] + [eip1167_reference()]
    Library(references)  # validates the new records on their own before writing anything
    DATA_DIR.mkdir(exist_ok=True)
    for reference in references:
        (DATA_DIR / f"{reference.id}.json").write_text(reference.model_dump_json(indent=2) + "\n")
        print(f"reference {reference.id}: {len(reference.functions)} functions")
    write_fixtures()


if __name__ == "__main__":
    main()
