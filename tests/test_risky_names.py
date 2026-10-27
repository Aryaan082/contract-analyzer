"""chain/risky_names.py must agree with references/risk_patterns.py, which keeps an equal copy
(chain/ cannot import references/). If a rule changes in one place, this fails until the other
follows and LABELS_VERSION is bumped."""

import pytest

from chain.risky_names import LABELS_VERSION, kinds_for_name, words
from references import risk_patterns

NAMES = [
    "transfer(address,uint256)",
    "mint(address,uint256)",
    "mintTo(address)",
    "addToBlackList(address)",
    "setBlocklist(address,bool)",
    "setSellTax(uint256)",
    "updateFees(uint256,uint256)",
    "pause()",
    "setPaused(bool)",
    "togglePause()",
    "withdraw(uint256)",
    "ownerWithdraw(uint256)",
    "withdrawDev()",
    "rescueTokens(address,uint256)",
    "sweep(address)",
    "upgradeTo(address)",
    "setImplementation(address)",
    "changeAdmin(address)",
    "kill()",
    "selfDestruct()",
    "ownerOf(uint256)",
    "owner_of_everything(uint256)",
    "balanceOf(address)",
    "_____$_$__$___$$$___$$___$__$$(address,uint256)",
    "",
]


@pytest.mark.parametrize("name", NAMES)
def test_the_two_copies_agree_on_a_name(name):
    assert kinds_for_name(name) == risk_patterns.kinds_for_name(name)


def test_the_two_copies_agree_on_every_known_risky_signature():
    for signatures in risk_patterns.RISKY_SIGNATURES.values():
        for signature in signatures:
            assert kinds_for_name(signature) == risk_patterns.kinds_for_name(signature), signature


def test_words_agree():
    for name in NAMES:
        assert words(name) == risk_patterns.words(name)


def test_a_name_that_merely_contains_owner_proves_nothing():
    assert kinds_for_name("ownerOf(uint256)") == []
    assert kinds_for_name("withdraw(uint256)") == []  # how WETH works
    assert kinds_for_name("withdrawOwner(uint256)") == ["owner_withdraw"]


def test_the_rules_have_a_version():
    assert isinstance(LABELS_VERSION, int) and LABELS_VERSION >= 1
