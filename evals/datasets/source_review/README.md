# Source-review dataset

Cards, a fixed split, hostile fixtures and planted variants for evaluating source review
(`review/`), made 2026-10-29 by session R5. Everything here is free to use and **no experiment has
run**: no model has read any of it, so `evals/results.md` has no row for it.

| Path | Holds |
|---|---|
| `cards.toml` | 105 cards: labels and references only. Format and rules: `evals/source_review/cards.py` |
| `split.toml` | the dev / held-out split, written once before any run; `python -m evals.source_review split` checks it |
| `planted/P01..P12_*/` | `vulnerable.sol`, `safe.sol`, `renamed.sol`: new contracts, one labelled issue each |
| `hostile/H05b, H17, H18, H21, H22` | the hostile cases that are ours (the rest are R2's, in `tests/fixtures/review/hostile/`) |
| `t1_criteria.toml` | the pass criteria for the first real-model run; `approved = false` until Aryaan approves them |

Run the kit: `uv run python -m evals.source_review dry-run` (free, scripted reviewer, no network).

## What is NOT stored: third-party source

A card for a contract on a chain is a reference (chain, address, block) and labels. The runner
fetches the source at run time through `review.bundle.fetch_source_bundle`. No Solidity from any
third party is in this repository; a test lists every `.sol` file under this folder and fails if one
is outside `planted/` and `hostile/`, which are ours. Write-ups are cited by URL; no text is copied.

## Cards

| Origin | Cards | What a label is |
|---|---|---|
| `incident` | 10 (the DeFiHackLabs list of `evals/datasets/incidents.toml`, pinned one block before the attack) | the functions a public write-up names, checked to exist in the verified source, with the checklist entry that fits |
| `planted` | 36 (12 variants x vulnerable, safe twin, renamed copy) | exact: we wrote the issue |
| `benign`, `grey` | 12 + 5 (the dev cards of `contracts.toml`; the class is Component 6's) | no labelled function. A drafted label is a note on powers by design, from the source and a public page |
| `hostile` | 42 (21 cases, most with a clean twin) | none; the expected behaviour is in `evals/source_review/hostile_cases.py` |

**Every label is a draft: `reviewed_by` is empty on all 89 drafted labels** (105 cards). Any results
file says so, and Aryaan reviews ten before any number is quoted (decision 16).

### Incident labels, 2026-10-29

Source read through the existing explorer client (one fetch per address, plus one for Arrakis's
implementation), write-ups read through web search and fetch. A label needs both.

| Card | Labelled function(s) | Entry | Write-up |
|---|---|---|---|
| balancerv1bpool | BPool.joinswapPoolAmountOut, BMath.calcSingleInGivenPoolOut | arithmetic-and-rounding | news.bitcoin.com (relays SlowMist) |
| arrakisguni | ArrakisVaultV1.mint, .burn (implementation behind the card's proxy) | oracle-manipulation | Nominis August 2026 report |
| usm | USM.ethFromDefund | arithmetic-and-rounding | Nominis August 2026 report |
| etherfiatomicqueue | AtomicQueue.solve | missing-access-control, unlimited-approval-exposure | phemex (relays SlowMist) |
| reflexergeb | GebProxyActions.quitSystem | missing-access-control | coinalertnews (one source only) |
| floatprotocol | Hypervisor.currentTick, .getTotalAmounts | oracle-manipulation | crypto.news (relays SlowMist) |
| notionalfinance | Portfolios.mintfCashPair (the named entry point) | arithmetic-and-rounding | Merkle Science |
| ajnafinance | **unlabelled**: reports name no function | | |
| termfinance | **unlabelled**: the verified source is Vyper (not supported in v1) | | |
| ensofinance | **unlabelled**: source not verified | | |

Most write-ups are news pieces that relay an alert; none is a protocol post-mortem. Two pages
returned 403 and were not read. The Arrakis implementation address is the explorer's hint, not read
from the chain. The Notional bundle is the proxy's verification record; that Portfolios is the code
behind that address is not checked.

### Benign and grey

Labelled from source plus a public page that names the address or describes the contract: USDC
(Circle's address page), Uniswap V2 pair (the pools page, which does not list this pair), Seaport
(its README) and Permit2 (its docs page). The other 13 are unlabelled with the reason on the card:
no public page was read (DAI, WETH, WBTC, stETH), no write-up exists for an obscure token (8), or
the source is not verified (one).

## The split (note 6.5)

Within each origin, units (a family, whole) are ordered by SHA-256 of the item key and the first
third, rounded half up, is held out. `planted` and `hostile` are dev only (written after the
checklist); a family holding one of Component 6's `existing` contracts is dev as a whole. Held out
today: incidents ajnafinance, arrakisguni, floatprotocol; benign famous-weth, tok-e2f2f46b,
tok-f2b36ba4, tok-f7755634; grey permit2. Eight items is a sanity check, not a measuring
instrument; the look budget is three, each declared in `evals/results.md`. The held-out cards'
SOURCE and write-ups were read to draft labels (no model has run, so nothing could be tuned on
them); their RESULTS must not be looked at during development.

## Planted variants

12 contracts over 12 different checklist entries, each with a safe twin (the fix, a few lines) and
`renamed.sol`, the memorisation control of note 6.4 made by `evals/source_review/planted.py`. They are
new code: tests bound their overlap with the taxonomy's own `example_vulnerable` text, which the
reviewer sees in its prompt. They carry no comment that points at the issue and no exploit steps.
They are dev material only.

## Limits

Planted, hostile and renamed items are ours and prove plumbing and robustness, not accuracy on real
code. Seven function-level incident labels are too few to quote a rate. A scripted reviewer that is
handed the labels scores perfectly; the dry run says so on its first line.
