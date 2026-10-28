# Collection log (Component 6, part C)

Built 2026-10-28. Label rules sha256 `d415c2b370e85dcfcf9658f1c11742046c7f03e19c8f486c5cd622979f96a91d`. Counts only: there is deliberately no line per
held-out contract here. Every label is unreviewed (38 of 38 cards in the dev and held-out
files; the 13 disputed tokens are unreviewed as well).

## Scan

- Windows (block numbers, Ethereum): 25450000-25470000, 25585000-25605000, 25720000-25740000, 25855000-25875000, 25990000-26010000. Tokens paired with WETH on Uniswap V2 created in
  them: 1254.
- Inspection (could the token be a test case at all):
  family_of_existing_card 104, no_code 1, no_liquidity 21, not_erc20 6, ok 1122.
- Measured after the family cap of 8 per code family: 555.
- Of those, 57 had 20 or more different buyers in 30 days (the rest
  traded too little to be called anything).
- Decided and left out: benign_over_target 0, flagged_over_target 0, not_trading_late 32, pool_drained_by_funder 5, same_family_as_a_kept_token 2, too_little_trading 498, unclear_seller_share 4.
- Famous tokens that failed their own sample rule (200 senders in
  500 blocks) and were dropped: 5.

## What was kept

| class | dev | held-out | total |
|---|---|---|---|
| flagged | 5 | 0 | 5 |
| benign | 12 | 5 | 17 |
| grey | 5 | 1 | 6 |
| beyond_reach | 7 | 3 | 10 |

By group (dev and held-out together):
existing 8, famous 2, grey 4, incident 10, obscure 14.
Tool-use set: 20 contracts, all dev.

## Rules history

`label_rules.toml` has its version history at the top. v1 was in force while the scan measured; no
label had been computed. Before any label was computed, a check on four known honeypots showed that
two of them trade normally for about 36 hours before stopping, so v2 requires sells still happening
in the last 10 days for a benign label. The measurements (counts, not labels) did not change; the
one number v2 needs was added afterwards for the tokens with enough buyers.

## What the outcome rules could not find

Flagged tokens other than the five existing ones: none came out of this scan (see the table). A
honeypot whose trap springs after the early trading looks healthy in Transfer events, and a failed
sell leaves no log, so "holders could not get out" is mostly invisible from history alone. The
existing honeypots (groklet, meowe, asasar, brick) are labelled by the two sources written on their
cards, not by this method, and are dev only.

## Limits worth remembering

- Counts of buyers and sellers are of different wallets in Transfer logs; a wallet using a router
  that is not on the list in `evals/collect/rules.py` is counted as itself or, if the router is the
  recipient, as one wallet. That makes tokens look less crowded, which sends them to "unclear" (left
  out), not into a class.
- Only the earliest 5000 transfers after the pinned block were read for each
  token; counts are lower bounds when a token traded more than that.
- Very high sell taxes with working sells are not found by this outcome rule (brick is an example);
  they enter only as the existing contract.
- A token whose pool was drained by its first liquidity provider is left out, not labelled.
- Blacklist events outside the fixed list in `rules.py` are not seen.
- Etherscan's "verified source" is a current fact, not the state at the pinned block.
- Pinned blocks are an hour after the first liquidity; the 30-day windows all ended by
  26280500 (2026-10-27 00:37 UTC).
