"""Read-only guardrail: our code must not sign or send anything.

web3 itself imports eth_account, so a runtime check of sys.modules would always fail. This scans
our own source instead.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_MODULES = ("eth_account", "eth_keys")
FORBIDDEN_NAMES = {
    "Account",
    "account",
    "private_key",
    "send_transaction",
    "send_raw_transaction",
    "sign_transaction",
    "sign_message",
    "sign_typed_data",
}
FORBIDDEN_RPC_METHODS = {
    "eth_sendTransaction",
    "eth_sendRawTransaction",
    "eth_sign",
    "eth_signTransaction",
    "eth_signTypedData",
    "personal_sign",
}


def violations(source: str, *, check_strings: bool) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                found.append(node.module)
            found += [a.name for a in node.names if a.name in FORBIDDEN_NAMES]
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.arg) and node.arg in FORBIDDEN_NAMES:
            found.append(node.arg)
        elif (
            check_strings and isinstance(node, ast.Constant) and node.value in FORBIDDEN_RPC_METHODS
        ):
            found.append(node.value)
    return found


@pytest.mark.parametrize(
    "snippet",
    [
        "import eth_account",
        "from eth_account.signers.local import LocalAccount",
        "import eth_keys",
        "from web3 import Account",
        "w3.eth.account.sign_transaction(tx, key)",
        "w3.eth.send_raw_transaction(raw)",
        "def f(private_key): pass",
        "provider.make_request('eth_sendRawTransaction', [raw])",
    ],
)
def test_the_scanner_catches_what_it_is_meant_to(snippet):
    assert violations(snippet, check_strings=True)


def test_the_scanner_accepts_ordinary_read_only_code():
    source = "from web3 import Web3\nWeb3.is_address(x)\nw3.eth.get_code(a, 5)\n"
    assert violations(source, check_strings=True) == []


def test_none_of_our_modules_sign_or_send():
    problems = {}
    for folder, check_strings in (
        ("chain", True),
        ("references", True),
        ("knowledge", True),
        ("retrieval", True),
        ("evals", True),
        ("tests", False),
    ):
        for path in sorted((ROOT / folder).rglob("*.py")):
            found = violations(path.read_text(), check_strings=check_strings)
            if found:
                problems[str(path.relative_to(ROOT))] = found
    assert problems == {}
