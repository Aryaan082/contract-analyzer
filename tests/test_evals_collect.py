import dataclasses
import hashlib

import pytest

from evals.cards import load_cards
from evals.collect.build import choose
from evals.collect.chaindata import address_topic
from evals.collect.existing import DISPUTED, EXISTING, FAMOUS, GREY, fixed_addresses
from evals.collect.rules import (
    BLACKLIST_TOPICS,
    BURN_TOPIC,
    HASH_PATH,
    MINT_TOPIC,
    PAIR_CREATED_TOPIC,
    SYNC_TOPIC,
    TRANSFER_TOPIC,
    UNISWAP_V2_FACTORY,
    WETH,
    ZERO,
    load_rules,
    rules_hash,
)
from evals.collect.scan import (
    Candidate,
    Inspection,
    Measurement,
    count_trading,
    decide,
    discover,
    family_key,
    inspect,
    measure,
    shape_of,
    split_key,
)
from evals.collect.split import Item, assign, tool_use_set, trajectory_of
from evals.collect.tomlout import cards_file
from tests.evals_collect_support import (
    BLACKLIST_CODE,
    CREATOR,
    ERC20_CODE,
    LP,
    MINT_CODE,
    PAIR,
    TOKEN,
    FakeChain,
    creator_mint,
    raw,
    trading,
    transfer,
    wallet,
)

RULES = load_rules()


# -- the rules file ------------------------------------------------------------------------------


def test_the_rules_file_hash_is_the_recorded_one():
    assert rules_hash() == HASH_PATH.read_text().strip(), (
        "label_rules.toml changed after its hash was recorded"
    )


def test_every_threshold_is_read_from_the_file():
    assert RULES.min_buyers == 20 and RULES.window == 216_000
    assert 0 < RULES.stuck_max_share < RULES.healthy_min_share < 1


def test_a_missing_rule_is_reported_by_name():
    from evals.collect.rules import Rules

    with pytest.raises(ValueError, match="missing"):
        Rules.from_toml({"version": 1})


# -- counting wallets ----------------------------------------------------------------------------


def test_buyers_and_sellers_are_distinct_wallets_and_leave_out_the_obvious():
    logs = [
        transfer(1, PAIR, wallet(1)),
        transfer(2, PAIR, wallet(1)),  # the same buyer twice
        transfer(3, PAIR, wallet(2)),
        transfer(4, PAIR, CREATOR),  # the creator buying back is not a crowd
        transfer(5, PAIR, "0x7a250d5630b4cf539739dd2c5dacb4c659f2488d".replace("dd2c", "dd2c")),
        transfer(6, wallet(1), PAIR),
        transfer(7, TOKEN, PAIR),  # the token's own fee swap
        transfer(8, ZERO, wallet(3)),  # a mint is neither
    ]
    assert count_trading(logs, PAIR, TOKEN, [CREATOR]) == (3, 1)


def test_a_router_is_not_a_wallet():
    router = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
    logs = [transfer(1, PAIR, router), transfer(2, router, PAIR), transfer(3, PAIR, wallet(1))]
    assert count_trading(logs, PAIR, TOKEN, []) == (1, 0)


# -- the label -----------------------------------------------------------------------------------


def measurement(**changes) -> Measurement:
    base = dict(
        token=TOKEN,
        pair=PAIR,
        pinned=1_000,
        code_hash="h",
        has_mint=False,
        has_blacklist=False,
        buyers=100,
        sellers=50,
        late_sellers=10,
        removed_share=0.0,
    )
    return Measurement(**{**base, **changes})


def test_many_buyers_and_almost_no_sellers_is_flagged_as_a_blocked_sell():
    result = decide(RULES, measurement(buyers=100, sellers=5))
    assert (result.label, result.reasons) == ("flagged", ("honeypot-sell-blocked",))


def test_the_ten_percent_line_is_inclusive():
    assert decide(RULES, measurement(buyers=100, sellers=10)).label == "flagged"
    assert decide(RULES, measurement(buyers=100, sellers=11)).why == "unclear_seller_share"


def test_a_healthy_crowd_is_benign_and_the_gap_between_the_two_is_left_out():
    assert decide(RULES, measurement(buyers=100, sellers=25)).label == "benign"
    assert decide(RULES, measurement(buyers=100, sellers=24)).label == "excluded"
    assert decide(RULES, measurement(buyers=20, sellers=9)).label == "excluded"  # under 10 sellers


def test_a_token_that_traded_and_then_went_quiet_is_not_benign():
    assert decide(RULES, measurement(late_sellers=4)).why == "not_trading_late"
    assert decide(RULES, measurement(late_sellers=5)).label == "benign"
    assert decide(RULES, measurement(late_sellers=None)).label == "excluded"
    # a few late sellers do not save a token whose early sells were already too few
    assert decide(RULES, measurement(sellers=3, late_sellers=9)).label == "flagged"


def test_too_little_trading_says_nothing():
    assert decide(RULES, measurement(buyers=19, sellers=0)).why == "too_little_trading"


def test_a_drained_pool_is_neither_flagged_nor_benign():
    assert decide(RULES, measurement(sellers=1, removed_share=0.9)).why == "pool_drained_by_funder"
    assert decide(RULES, measurement(removed_share=0.9)).label == "excluded"


def test_a_power_that_was_used_flags_an_otherwise_healthy_token():
    minted = decide(RULES, measurement(has_mint=True, minted_share=0.10))
    assert (minted.label, minted.reasons) == ("flagged", ("privileged-mint",))
    banned = decide(RULES, measurement(has_blacklist=True, blacklist_events=1))
    assert banned.reasons == ("address-blacklist",)
    both = decide(RULES, measurement(minted_share=0.5, blacklist_events=2))
    assert both.reasons == ("privileged-mint", "address-blacklist")
    assert decide(RULES, measurement(has_mint=True, minted_share=0.099)).label == "benign"


# -- discover and inspect ------------------------------------------------------------------------


def pair_created(block: int, token0: str, token1: str, pair: str) -> tuple[str, tuple]:
    log = raw(
        block,
        [PAIR_CREATED_TOPIC, address_topic(token0), address_topic(token1)],
        "0x" + pair[2:].rjust(64, "0") + "00" * 32,
    )
    return UNISWAP_V2_FACTORY, log


def test_discover_keeps_only_weth_pairs_and_names_the_other_side():
    chain = FakeChain()
    small = dataclasses.replace(RULES, windows=((100, 200),))
    for block, a, b, pair in (
        (110, TOKEN, WETH, PAIR),
        (120, WETH, "0x" + "5a" * 20, "0x" + "5b" * 20),
        (130, "0x" + "6a" * 20, "0x" + "6b" * 20, "0x" + "6c" * 20),  # not WETH
        (999, TOKEN, WETH, PAIR),  # outside the window
    ):
        address, log = pair_created(block, a, b, pair)
        chain.add(address, PAIR_CREATED_TOPIC, log)
    found = discover(chain, small)
    assert [(c.token, c.token_is_token0, c.pair) for c in found] == [
        (TOKEN, True, PAIR),
        ("0x" + "5a" * 20, False, "0x" + "5b" * 20),
    ]


def candidate(created: int = 100) -> Candidate:
    return Candidate(TOKEN, PAIR, created, True, "0x01")


def pool_with_liquidity(chain: FakeChain, block: int = 150) -> None:
    chain.add(PAIR, SYNC_TOPIC, raw(block, [SYNC_TOPIC]))


def test_inspect_gives_the_pinned_block_an_hour_after_the_first_liquidity():
    chain = FakeChain()
    chain.codes[TOKEN] = ERC20_CODE
    pool_with_liquidity(chain, 150)
    result = inspect(chain, RULES, candidate())
    assert (result.status, result.first_liquidity, result.pinned) == ("ok", 150, 450)
    assert result.code_hash == family_key(ERC20_CODE)


@pytest.mark.parametrize(
    "code, status",
    [
        (b"", "no_code"),
        (bytes.fromhex("6001600101"), "not_erc20"),
        (bytes.fromhex("363d3d37" + "00" * 41), "not_erc20"),
        (ERC20_CODE + bytes.fromhex("363d3d37"), "ok"),
    ],
)
def test_inspect_statuses_for_code(code, status):
    chain = FakeChain()
    chain.codes[TOKEN] = code
    pool_with_liquidity(chain)
    assert inspect(chain, RULES, candidate()).status == status


def test_a_clone_and_an_eip1967_proxy_are_left_out():
    chain = FakeChain()
    pool_with_liquidity(chain)
    clone = bytes.fromhex("363d3d37") + ERC20_CODE
    chain.codes[TOKEN] = clone
    assert inspect(chain, RULES, candidate()).status == "proxy_or_clone"
    slot = bytes.fromhex("360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc")
    chain.codes[TOKEN] = ERC20_CODE + slot
    assert inspect(chain, RULES, candidate()).status == "proxy_or_clone"


def test_inspect_leaves_out_a_pool_that_never_had_liquidity_and_an_unfinished_window():
    chain = FakeChain()
    chain.codes[TOKEN] = ERC20_CODE
    assert inspect(chain, RULES, candidate()).status == "no_liquidity"
    late = candidate(RULES.latest_end - 1_000)
    pool_with_liquidity(chain, RULES.latest_end - 900)
    assert inspect(chain, RULES, late).status == "window_incomplete"


def test_inspect_leaves_out_the_family_of_a_fixed_contract():
    chain = FakeChain()
    chain.codes[TOKEN] = ERC20_CODE
    pool_with_liquidity(chain)
    again = inspect(chain, RULES, candidate(), existing_hashes=[family_key(ERC20_CODE)])
    assert again.status == "family_of_existing_card"


def test_family_key_ignores_the_compiler_metadata_tail():
    metadata = bytes.fromhex("a164736f6c6343000819000a")  # {"solc": 0.8.25}, 10 bytes + length
    assert family_key(ERC20_CODE + metadata) == family_key(ERC20_CODE)
    assert family_key(ERC20_CODE) != family_key(MINT_CODE)


def test_shape_of_reads_the_functions_from_the_bytecode():
    assert shape_of(ERC20_CODE).erc20 and not shape_of(ERC20_CODE).has_mint
    assert shape_of(MINT_CODE).has_mint
    assert shape_of(BLACKLIST_CODE).has_blacklist


# -- measure -------------------------------------------------------------------------------------


def inspection(**changes) -> Inspection:
    base = dict(
        token=TOKEN,
        pair=PAIR,
        created=100,
        token_is_token0=True,
        status="ok",
        first_liquidity=150,
        pinned=1_000,
        code_hash="h",
    )
    return Inspection(**{**base, **changes})


def test_a_honeypot_pattern_is_measured_and_labelled():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=40, sellers=1)
    chain.add(PAIR, TRANSFER_TOPIC, transfer(120, "0x" + "00" * 20, LP, 5))  # LP shares minted
    m = measure(chain, RULES, inspection())
    assert (m.buyers, m.sellers, m.creator, m.lp_owner) == (40, 1, CREATOR, LP)
    assert m.removed_share == 0.0
    assert decide(RULES, m).reasons == ("honeypot-sell-blocked",)


def test_late_sellers_are_counted_in_the_last_ten_days_only():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=30, sellers=30)  # early sells, near the pinned block
    late_start = 1_000 + RULES.window - RULES.late_blocks + 1
    for number in range(7):
        chain.add(TOKEN, TRANSFER_TOPIC, transfer(late_start + number, wallet(2000 + number), PAIR))
    m = measure(chain, RULES, inspection())
    assert m.sellers == 37 and m.late_sellers == 7
    quiet = FakeChain()
    creator_mint(quiet)
    trading(quiet, buyers=30, sellers=30)
    assert measure(quiet, RULES, inspection()).late_sellers == 0
    assert decide(RULES, measure(quiet, RULES, inspection())).why == "not_trading_late"


def test_little_trading_stops_early_and_asks_for_nothing_more():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=5, sellers=0)
    m = measure(chain, RULES, inspection())
    assert m.removed_share is None
    assert not any(address == PAIR and topic == MINT_TOPIC for address, topic in chain.calls)


def test_a_liquidity_provider_taking_the_weth_out_is_measured():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=30, sellers=30)
    chain.add(PAIR, TRANSFER_TOPIC, transfer(120, ZERO, LP, 5))
    chain.add(PAIR, TRANSFER_TOPIC, transfer(5_000, LP, PAIR, 5, tx="0xaaa"))
    weth_word = 1  # the token is token0, so WETH is the second amount
    chain.add(
        PAIR, MINT_TOPIC, raw(120, [MINT_TOPIC], "0x" + format(0, "064x") + format(10, "064x"))
    )
    chain.add(
        PAIR,
        BURN_TOPIC,
        raw(5_000, [BURN_TOPIC], "0x" + format(0, "064x") + format(9, "064x"), tx="0xaaa"),
    )
    m = measure(chain, RULES, inspection())
    assert weth_word == 1 and m.removed_share == pytest.approx(0.9)
    assert decide(RULES, m).why == "pool_drained_by_funder"


def test_a_burn_by_somebody_else_is_not_a_rug():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=30, sellers=30)
    chain.add(PAIR, TRANSFER_TOPIC, transfer(120, ZERO, LP, 5))
    chain.add(
        PAIR, MINT_TOPIC, raw(120, [MINT_TOPIC], "0x" + format(0, "064x") + format(10, "064x"))
    )
    chain.add(
        PAIR,
        BURN_TOPIC,
        raw(5_000, [BURN_TOPIC], "0x" + format(0, "064x") + format(9, "064x"), tx="0xbbb"),
    )
    assert measure(chain, RULES, inspection()).removed_share == 0.0


def test_supply_growth_and_blacklist_events_are_measured_only_when_the_code_has_the_function():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=30, sellers=30)
    chain.add(TOKEN, TRANSFER_TOPIC, transfer(2_000, ZERO, wallet(7), 25))
    chain.supplies[TOKEN, 1_000] = 100
    chain.add(TOKEN, BLACKLIST_TOPICS[0], raw(3_000, [BLACKLIST_TOPICS[0]]))
    plain = measure(chain, RULES, inspection())
    assert (plain.minted_share, plain.blacklist_events) == (None, None)
    full = measure(chain, RULES, inspection(has_mint=True, has_blacklist=True))
    assert full.minted_share == 0.25 and full.blacklist_events == 1
    assert decide(RULES, full).reasons == ("privileged-mint", "address-blacklist")


def test_transfers_past_the_cap_are_marked_as_lower_bounds():
    chain = FakeChain()
    creator_mint(chain)
    trading(chain, buyers=30, sellers=0)
    capped = dataclasses.replace(RULES, max_transfer_logs=10)
    m = measure(chain, capped, inspection())
    assert m.truncated and m.transfers_read == 10 and m.buyers == 10


# -- the split -----------------------------------------------------------------------------------


def items(klass: str, count: int, family=lambda n: "", dev_only=lambda n: False) -> list[Item]:
    return [
        Item(
            f"{klass}-{n}",
            klass,
            "ethereum",
            wallet(n + (900 if klass == "benign" else 0)),
            family(n),
            dev_only(n),
        )
        for n in range(count)
    ]


def test_the_first_third_by_hash_of_each_class_is_held_out():
    everything = items("flagged", 12) + items("benign", 9)
    sides = assign(everything, 0.3333)
    for klass, expected in (("flagged", 4), ("benign", 3)):
        members = sorted((i for i in everything if i.klass == klass), key=lambda i: i.key)
        assert [sides[i.id] for i in members] == ["heldout"] * expected + ["dev"] * (
            len(members) - expected
        )


def test_the_split_does_not_depend_on_the_order_the_items_are_listed_in():
    everything = items("flagged", 12)
    assert assign(everything, 0.3333) == assign(list(reversed(everything)), 0.3333)


def test_a_code_family_stays_on_one_side():
    everything = items("flagged", 12, family=lambda n: f"fam{n % 4}")
    sides = assign(everything, 0.3333)
    for fam in range(4):
        assert len({sides[i.id] for i in everything if i.family == f"fam{fam}"}) == 1


def test_a_family_with_a_dev_only_member_is_dev_everywhere():
    everything = items("flagged", 12, family=lambda n: f"fam{n % 4}", dev_only=lambda n: n == 0)
    sides = assign(everything, 0.9)
    assert {sides[i.id] for i in everything if i.family == "fam0"} == {"dev"}


def test_a_family_in_two_classes_keeps_the_side_it_got_first():
    everything = [
        Item("f", "flagged", "ethereum", wallet(1), "same"),
        Item("b", "benign", "ethereum", wallet(2), "same"),
    ] + items("flagged", 8, family=lambda n: f"x{n}")
    sides = assign(everything, 0.5)
    assert sides["f"] == sides["b"]


def test_split_key_is_the_documented_hash():
    expected = hashlib.sha256(f"ethereum:{wallet(5)}".encode()).hexdigest()
    assert split_key("ethereum", wallet(5).upper().replace("0X", "0x")) == expected


def test_the_tool_use_set_fills_each_trajectory_then_tops_up():
    rows = [(f"p{n}", "proxy", f"{n:02d}") for n in range(5)]
    rows += [(f"h{n}", "honeypot", f"{n:02d}") for n in range(6)]
    rows += [(f"b{n}", "benign_token", f"{n:02d}") for n in range(30)]
    chosen = tool_use_set(rows, 20)
    assert len(chosen) == 20
    quota_picks = {"p0", "p1", "p2", "h0", "h1", "h2", "h3", "b0", "b1", "b2", "b3"}
    assert quota_picks <= chosen
    assert tool_use_set(rows, 20) == chosen


def test_trajectory_names():
    assert trajectory_of("grey", ("proxy", "token"), ()) == "proxy"
    assert trajectory_of("flagged", ("token",), ("honeypot-sell-blocked",)) == "honeypot"
    assert trajectory_of("flagged", ("token",), ("privileged-mint",)) == "powers"
    assert trajectory_of("benign", ("official",), ()) == "official"
    assert trajectory_of("beyond_reach", ("not_a_token",), ()) == "not_a_token"
    assert trajectory_of("beyond_reach", ("token",), ()) == "beyond_reach"


# -- choosing and writing ------------------------------------------------------------------------


def test_one_token_per_family_and_a_family_that_disagrees_is_dropped():
    flagged = measurement(token=wallet(1), code_hash="a", buyers=100, sellers=1)
    twin = measurement(token=wallet(2), code_hash="a", buyers=100, sellers=2)
    healthy = measurement(token=wallet(3), code_hash="b", buyers=100, sellers=60)
    odd_flagged = measurement(token=wallet(4), code_hash="c", buyers=100, sellers=1)
    odd_benign = measurement(token=wallet(5), code_hash="c", buyers=100, sellers=60)
    quiet = measurement(token=wallet(6), code_hash="d", buyers=3)
    picked, left_out = choose(
        RULES, [flagged, twin, healthy, odd_flagged, odd_benign, quiet], {"a": 2}
    )
    tokens = {p.measurement.token for p in picked}
    assert len(tokens & {wallet(1), wallet(2)}) == 1 and wallet(3) in tokens
    assert not tokens & {wallet(4), wallet(5), wallet(6)}
    assert left_out["mixed_family"] == 2 and left_out["too_little_trading"] == 1
    assert left_out["same_family_as_a_kept_token"] == 1
    assert next(p for p in picked if p.measurement.code_hash == "a").family_size == 2


def test_targets_cap_how_many_are_kept_and_the_choice_is_by_the_salted_hash():
    many = [
        measurement(token=wallet(n), code_hash=f"h{n}", buyers=100, sellers=1) for n in range(40)
    ]
    capped = dataclasses.replace(RULES, target_new_flagged=5)
    picked, left_out = choose(capped, many, {})
    assert len(picked) == 5 and left_out["flagged_over_target"] == 35
    again, _ = choose(capped, list(reversed(many)), {})
    assert {p.measurement.token for p in again} == {p.measurement.token for p in picked}


def test_cards_written_as_toml_read_back_and_hostile_text_cannot_break_the_file(tmp_path):
    nasty = 'a "quote" \\ back\\slash\n[[contract]]\nid = "evil" é'
    card = {
        "id": "tok-1",
        "chain": "ethereum",
        "address": wallet(9),
        "block": 5,
        "klass": "flagged",
        "split": "dev",
        "group": "obscure",
        "reasons": ["honeypot-sell-blocked"],
        "facts": [r"sell.{0,40}(revert|block)"],
        "label_source": "script:outcome-v1",
        "reviewed_by": "",
        "source": nasty,
        "reason": nasty,
    }
    path = tmp_path / "cards.toml"
    path.write_text(cards_file("header\n\nwith a gap", [card]))
    cards = load_cards(path)
    assert len(cards) == 1
    loaded = cards.cards[0]
    assert loaded.id == "tok-1" and loaded.source == nasty and loaded.klass == "flagged"
    assert loaded.unreviewed and cards.unreviewed() == (1, 1)


# -- the fixed contracts -------------------------------------------------------------------------


def test_the_fixed_lists_have_the_sizes_the_plan_says():
    assert len(EXISTING) == 8 and len(GREY) == 4 and len(FAMOUS) == 7 and len(DISPUTED) == 13
    assert len(set(fixed_addresses())) == len(fixed_addresses()) == 32
    assert all(a == a.lower() for a in fixed_addresses())


def test_the_eight_existing_cards_come_from_the_agent_contract_file():
    import tomllib
    from pathlib import Path

    source = tomllib.loads((Path(__file__).parent.parent / "agent" / "contracts.toml").read_text())
    rows = source["contract"] + source["candidate"]
    assert {r["address"].lower() for r in rows} == {c["address"] for c in EXISTING}
    assert {c["block"] for c in EXISTING} == {r["block"] for r in rows}
