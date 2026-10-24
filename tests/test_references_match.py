import json

from references.abi import parse_hex
from references.library import get_library
from references.match import Target, match
from references.similarity import jaccard, selector_overlap
from tests.references_support import FIXTURES, load_fixture, sel

LIBRARY = get_library()


def run(selectors, code=None, **kwargs):
    return match(Target(frozenset(selectors), code), LIBRARY, **kwargs)


def test_jaccard_treats_two_empty_sets_as_no_evidence():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({1, 2}, {1, 2}) == 1.0
    assert jaccard({1, 2}, {2, 3}) == 1 / 3


def test_overlap_separates_what_jaccard_blends():
    reference = frozenset(f"0x{i:08x}" for i in range(9))
    target = reference | {"0xaaaaaaaa", "0xbbbbbbbb"}
    overlap = selector_overlap(target, reference)
    assert overlap.jaccard == round(9 / 11, 4)  # looks "82% similar"...
    assert overlap.reference_coverage == 1.0  # ...but it has ALL of the reference
    assert (overlap.extras, overlap.missing) == (2, 0)


def test_overlap_with_a_reference_that_has_no_functions_has_no_coverage():
    assert selector_overlap(frozenset({"0x00000001"}), frozenset()).reference_coverage is None


def test_done_criterion_erc20_with_mint_and_blacklist_matches_erc20_and_shows_both_extras():
    fixture = load_fixture("FixtureERC20MintBlacklist")
    result = run(fixture.selectors, fixture.code)
    top = result.candidates[0]
    assert top.reference_id == "oz-erc20"
    assert result.quality == "close"
    assert top.selectors.reference_coverage == 1.0  # implements 100% of ERC20...
    assert (top.selectors.extras, top.selectors.missing) == (2, 0)  # ...plus 2 extras
    assert top.code.relation == "different"
    assert top.code.reference_solc == top.code.target_solc == "0.8.28"
    assert any("not a copy" in note for note in result.notes)


def test_a_standard_erc20_with_another_name_is_an_exact_copy_ignoring_metadata():
    fixture = load_fixture("FixtureERC20Plain")
    result = run(fixture.selectors, fixture.code)
    assert result.quality == "exact"
    assert result.candidates[0].reference_id == "oz-erc20"
    assert result.candidates[0].code.relation == "identical_ignoring_metadata"
    assert result.target.metadata_found is True and result.target.solc == "0.8.28"


def test_the_blind_spot_same_function_list_different_code_is_reported_not_hidden():
    fixture = load_fixture("FixtureERC20TransferCap")
    result = run(fixture.selectors, fixture.code)
    top = result.candidates[0]
    assert top.reference_id == "oz-erc20" and top.selectors.jaccard == 1.0
    assert top.code.relation == "different"
    assert result.quality == "close"  # the interface matches...
    assert any("same interface, not that the functions behave the same" in n for n in result.notes)


def test_ownable_and_pausable_extras_still_leave_erc20_the_best_match():
    fixture = load_fixture("FixtureERC20Wizard")
    result = run(fixture.selectors, fixture.code)
    assert result.candidates[0].reference_id == "oz-erc20"
    assert result.candidates[0].selectors.extras == 7
    assert "oz-ownable" not in {c.reference_id for c in result.candidates}  # modules never match


def test_an_nft_matches_erc721_not_erc20():
    erc721 = LIBRARY.get("oz-erc721")
    result = run(erc721.selectors)
    assert result.candidates[0].reference_id == "oz-erc721"
    assert result.quality == "close"
    # ERC20 may appear further down: ERC721 happens to share 5 of its 9 functions (approve,
    # transferFrom, balanceOf, name, symbol). It is never "close".
    erc20 = next((c for c in result.candidates if c.reference_id == "oz-erc20"), None)
    assert erc20 is None or erc20.selectors.reference_coverage < 0.9


def test_weth_prefers_weth_over_the_erc20_it_also_implements():
    result = run(LIBRARY.get("weth9-ethereum").selectors)
    ids = [c.reference_id for c in result.candidates]
    assert ids[:2] == ["weth-base", "weth9-ethereum"] or ids[:2] == ["weth9-ethereum", "weth-base"]
    assert "oz-erc20" in ids and ids.index("oz-erc20") > 1


def test_same_source_other_compiler_shares_the_interface_but_not_the_bytes():
    base = LIBRARY.get("weth-base")
    result = run(LIBRARY.get("weth9-ethereum").selectors, base.code)
    by_id = {c.reference_id: c for c in result.candidates}
    assert result.candidates[0].reference_id == "weth-base"  # its own bytes: an exact copy
    assert result.candidates[0].code.relation == "identical"
    weth9 = by_id["weth9-ethereum"]
    assert weth9.selectors.jaccard == 1.0  # the same 11 functions,
    assert weth9.code.relation == "different"  # in different bytes,
    assert weth9.code.reference_solc is None and weth9.code.target_solc == "0.5.17"


def test_a_famous_deployed_contract_is_recognised_exactly():
    usdt = LIBRARY.get("usdt-ethereum")
    result = run(usdt.selectors, usdt.code)
    assert result.quality == "exact" and result.candidates[0].reference_id == "usdt-ethereum"
    assert result.candidates[0].code.relation == "identical"


def test_a_clone_with_another_implementation_address_matches_the_standard_by_code_alone():
    clone = json.loads((FIXTURES / "clone_ethereum.json").read_text())
    code = parse_hex(clone["code"])
    template = LIBRARY.get("eip1167-minimal-proxy").code
    assert code != template  # a different address is embedded
    result = run([], code)
    assert result.quality == "exact"
    assert result.candidates[0].reference_id == "eip1167-minimal-proxy"
    assert result.candidates[0].code.relation == "identical_ignoring_variable_bytes"
    assert any("embedded address" in note for note in result.notes)


def test_a_changed_clone_is_not_an_exact_match():
    code = bytearray(LIBRARY.get("eip1167-minimal-proxy").code)
    code[-1] ^= 0x01  # outside the variable range
    assert run([], bytes(code)).quality == "none"


def test_a_proxy_with_immutables_matches_ignoring_them():
    reference = LIBRARY.get("oz-transparent-proxy")
    code = bytearray(reference.code)
    start, length = reference.reference.variable_ranges[0]
    code[start : start + length] = b"\x11" * length  # another admin address
    result = run([], bytes(code))
    assert result.candidates[0].reference_id == "oz-transparent-proxy"
    assert result.candidates[0].code.relation == "identical_ignoring_variable_bytes"


def test_nothing_close_gives_no_match_and_says_so():
    result = run([sel("completelyCustomThing(uint256)"), sel("another(address)")])
    assert result.quality == "none" and result.candidates == ()
    assert "Insufficient evidence" in result.notes[0]


def test_no_input_at_all_says_there_was_nothing_to_compare():
    result = run([])
    assert result.quality == "none" and "Nothing to compare" in result.notes[0]


def test_without_code_only_function_lists_are_compared_and_the_result_says_so():
    result = run(LIBRARY.get("oz-erc20").selectors)
    assert result.candidates[0].code is None
    assert result.target.code_hash is None
    assert result.quality == "close"  # not "exact": that needs code
    assert any("No code was given" in n for n in result.notes)


def test_a_small_reference_cannot_make_a_close_match():
    # Three of Ownable's functions are not enough to call anything "close" (and modules never
    # match anyway); a 3-function contract is a "partial" match at best.
    from references.match import MIN_FUNCTIONS_FOR_CLOSE

    smallest_contract = min(
        (p for p in LIBRARY.contracts if p.selectors), key=lambda p: len(p.selectors)
    )
    assert len(smallest_contract.selectors) >= MIN_FUNCTIONS_FOR_CLOSE or (
        run(smallest_contract.selectors).quality == "partial"
    )


def test_the_limit_caps_the_candidates():
    result = run(LIBRARY.get("weth9-ethereum").selectors, limit=1)
    assert len(result.candidates) == 1


def test_candidates_carry_the_origin_of_their_reference():
    top = run(LIBRARY.get("uni-ethereum").selectors).candidates[0]
    assert top.origin.type == "deployed" and top.origin.chain == "ethereum"
