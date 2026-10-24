"""chain/kinds.py: which standard interfaces a selector set adds up to."""

import pytest

from chain.kinds import KINDS, match_kinds, selector_of
from tests.references_support import load_fixture

ERC20 = next(k for k in KINDS if k.name == "erc20")
ERC721 = next(k for k in KINDS if k.name == "erc721")
V2_PAIR = next(k for k in KINDS if k.name == "uniswap_v2_pair")


def selectors_of(spec, *, without=()):
    return {selector_of(s) for s in spec.signatures if s not in without}


def names(matches):
    return {m.kind for m in matches}


def test_selectors_are_the_well_known_ones():
    assert selector_of("transfer(address,uint256)") == "0xa9059cbb"
    assert selector_of("balanceOf(address)") == "0x70a08231"
    assert selector_of("approve(address,uint256)") == "0x095ea7b3"
    assert selector_of("totalSupply()") == "0x18160ddd"
    assert selector_of("transferFrom(address,address,uint256)") == "0x23b872dd"
    assert selector_of("allowance(address,address)") == "0xdd62ed3e"


def test_a_real_erc20_is_an_erc20_and_nothing_else():
    complete, near = match_kinds(load_fixture("FixtureERC20Plain").selectors)
    assert names(complete) == {"erc20"} and near == ()


def test_an_erc20_with_extra_functions_still_matches():
    complete, _ = match_kinds(load_fixture("FixtureERC20MintBlacklist").selectors)
    assert "erc20" in names(complete)


def test_an_nft_is_not_mistaken_for_a_token():
    complete, near = match_kinds(selectors_of(ERC721))
    assert names(complete) == {"erc721"}
    assert "erc20" not in names(near)  # it shares only 3 of the 6 functions


def test_a_v2_pair_is_a_pair_and_its_lp_token_is_an_erc20():
    both = selectors_of(V2_PAIR) | selectors_of(ERC20)
    complete, _ = match_kinds(both)
    assert names(complete) == {"uniswap_v2_pair", "erc20"}


def test_a_token_missing_one_function_is_a_near_miss_naming_it():
    complete, near = match_kinds(selectors_of(ERC20, without=("allowance(address,address)",)))
    assert complete == ()
    (miss,) = near
    assert (miss.kind, miss.matched, miss.total) == ("erc20", 5, 6)
    assert miss.missing == ("allowance(address,address)",)


def test_a_token_missing_two_functions_is_not_worth_a_near_miss():
    spec_without = ("allowance(address,address)", "approve(address,uint256)")
    complete, near = match_kinds(selectors_of(ERC20, without=spec_without))
    assert complete == () and near == ()


def test_case_does_not_matter_and_unrelated_selectors_are_ignored():
    shouty = {s.upper().replace("0X", "0x") for s in selectors_of(ERC20)} | {"0xdeadbeef"}
    complete, _ = match_kinds(shouty)
    assert names(complete) == {"erc20"}


def test_no_selectors_match_nothing():
    assert match_kinds(set()) == ((), ())


def test_every_kind_is_well_formed():
    seen = set()
    for spec in KINDS:
        assert spec.name not in seen
        seen.add(spec.name)
        assert len(spec.signatures) >= 4 and len(set(spec.signatures)) == len(spec.signatures)
        for signature in spec.signatures:
            assert signature.endswith(")") and " " not in signature


@pytest.mark.parametrize("spec", KINDS, ids=lambda s: s.name)
def test_each_kind_matches_its_own_full_list(spec):
    complete, _ = match_kinds(selectors_of(spec))
    assert spec.name in names(complete)
