# Retrieval quiz

125 questions about the knowledge base (35 taxonomy entries and 19 kept findings), each with the
chunks that count as right answers. Written by Claude for Aryaan to review: the first 100 on
2026-10-25, then 25 code fragments on 2026-10-26 (see "Code fragments" below).

| File | Holds |
|---|---|
| `retrieval_questions.jsonl` | `id`, `text`, `style`, `kind`, `case`, `filters`, `source` (only the 25 fragments) |
| `retrieval_labels_dev.jsonl` | `id`, `relevant` (chunk IDs) for the 87 dev questions |
| `retrieval_labels_heldout.jsonl` | the same for the 38 held-out questions. Read only at milestones, with `--held-out` |

**style** is how the query is phrased: `question` (the model's own question), `tool_words` (the raw
signal strings the tools emit), `code` (a fresh Solidity fragment), `description` (one sentence on
what the code does). **kind** is what the question is built to stress: `paraphrase` (few shared
words), `identifier` (exact code tokens), `twin` (a close neighbour chunk exists), `multi` (two or
three chunks are needed), `filtered` (a filter narrows the search), `paired` (one target asked in
several styles; the questions share a `case`), `snippet` (a real fragment quoted from an audit
report whose finding is in the index) and `unseen` (a real fragment from a report that is not in
the index, so only the pattern can match).

**filters** use the keys `record_type` (`taxonomy` or `finding`), `severity`, `group`, `protocol`,
`date_from` (`YYYY-MM`) and `kind` (matches an entry's `applies_to`; a filter on a field a chunk
does not have drops that chunk). The right answers always lie inside their own filter.

## The split

Fixed by hash alone, before any search ran. Within each `kind`, the units (a whole `case` for
paired questions, otherwise one question) are sorted by the SHA-256 of their key, and the first 30%
(rounded) are held out. Every kind has some held-out questions, and the styles of one case are never
split across dev and held-out. The two label files are the record of the split; no code recomputes
it, so adding questions later cannot move an old one. A first attempt at the plain rule
`sha256(id) % 10 < 3` gave 19 held-out questions and none of the identifier kind, so it was replaced
before anything was measured.

## Code fragments (added 2026-10-26)

The first 100 questions had 13 code-style ones, all invented. Because that was too few to say
anything about code queries, 25 more were cut from real audit listings. Each is a stretch of lines
from one report figure (or a few figures of one finding), chosen from the report's text alone;
**the labels were written and the file saved before any search was run on them.**

- `snippet` (14): from a finding that is in the index. The right answers are that finding, plus a
  taxonomy entry only when the fragment itself shows the pattern (q101 signature replay, q107
  reentrancy, q110 unchecked result, q111 rounding, q112 single key). The code listings are not in
  the chunk text (the parser leaves a marker), so a fragment cannot match by copying; the finding's
  prose may still share function names with it.
- `unseen` (11): from a report that is not in the index. Only a taxonomy entry can be right, so
  these ask whether the pattern in unfamiliar code is recognised. The label is a reader's judgement
  of the class, not the auditors'; q124 (stale or unchecked price feed) and q121 (an owner address
  that is never checked) are the least clear-cut.
- What was done to every fragment: whitespace normalised and re-indented by brace depth, full-line
  comments dropped, line numbers removed, PDF spacing such as `msg . sender` repaired. Nothing else
  was edited. `source` names the report, finding, figure and PDF page.
- The same rule as before decided dev or held-out: within each kind the first 30% (rounded) by the
  SHA-256 of the question id, so 4 of the 14 snippets and 3 of the 11 unseen ones are held out. No
  earlier question moved. The held-out total is now 38, so held-out rows before and after this
  date are not on the same questions.

Sources, all Trail of Bits public reviews, CC BY-SA 4.0
(<https://github.com/trailofbits/publications>, commit `89940dfbec07ad03b0989e67d78e84250765366b`,
folder `reviews/`; each file was downloaded from there and checked against git's own hash):

| In the index (`snippet`) | Not in the index (`unseen`) |
|---|---|
| Aave v3 (2021-11), Aave v4 (2026-02), Balancer v3, Maple v2, Morpho v1, Reserve Protocol (2022-08), Treehouse tETH | `OriginDollar.pdf`, `UniswapV3Core.pdf`, `NFTX.pdf`, `Opyn-Gamma-Protocol.pdf`, `2024-06-aladdinfx-oracle-securityreview.pdf` |

Eleven reports were downloaded to look for fragments; two would not parse (`PerpetualProtocolV2`,
Smardex), two had nothing usable and are not used (`2021-04-balancer-balancerv2`, `2022-03-maplefinance`),
and `CurveDAO` and `YearnV2Vaults` are Vyper. The fragment rows therefore carry the same licence as the
reports: CC BY-SA 4.0, with the changes listed above. The original reports remain authoritative.

## Rules

- Held-out labels are read at milestones only, each one declared in `evals/results.md`.
- Nobody edits chunk text or labels in response to a quiz result. A label fix is
  allowed only if logged in `evals/results.md` and applied to every variant.
- Tool-word queries are ambiguous on purpose: several entries share a signal, but only the target
  is marked right.

## Limits

One author, so one writing style. The `question` and `description` styles are stand-ins for what
the model would write, to be rerun with real model output. Six questions use filters that leave 7
to 26 eligible chunks, so they test the filter plumbing more than ranking.

# Detection set (contracts)

The labelled contracts for the tool-use, detection and answer-quality layers. Built by
`python -m evals.collect` (see "Collecting the detection set" in `../README.md`); the numbers of what
was scanned, kept and left out are in `collection_log.md`.

| File | Holds |
|---|---|
| `label_rules.toml` | the written rules and every threshold; `label_rules.sha256` is its hash. The scripts read their numbers from it, and a test fails if the file no longer matches the hash |
| `contracts.toml` | the DEV cards (cards format of `evals/cards.py`); what dev tooling loads |
| `contracts_heldout.toml` | the HELD-OUT cards. Only the builder that writes it (`evals/collect/`) names this file; the scorer reads it only when it is passed as `--cards` together with `--held-out --declare "<why>"`, and a test checks that nothing else in `agent/`, `chain/`, `references/`, `knowledge/`, `retrieval/` or `evals/` names it. No per-contract held-out detail goes into `collection_log.md`, results files or notes |
| `disputed.toml` | 13 tokens that honeypot.is called honeypots while our own sell rehearsal found them sellable or without an ERC-20 interface. Dev, grey, reported apart; neither opinion is the label |
| `incidents.toml` | the ten beyond-reach incidents, from the DeFiHackLabs incident list (Apache-2.0; `licenses/DeFiHackLabs-LICENSE`) |
| `collection_log.md` | counts only: what was scanned, kept and left out, and why |

## What a label means

A label is **a script's reading of chain history under the written rules**, or a written rule applied
to a named contract (the grey ones). It is **not** "agrees with Aryaan" and not "is truly safe or
unsafe". Every card has `reviewed_by = ""`, and every results file says "N of M labels unreviewed".
Honeypot.is was not used to label anything here and is never queried; our own sell rehearsal is not a
label either (the eight existing cards keep the two sources they were chosen with, written on the
card).

| Class | Meaning | Scored |
|---|---|---|
| `flagged` | warn-worthy for a reason the tools can see: holders could not get out (many buyers, almost no sellers after the pinned block), or a power in the bytecode was used (supply grew 10% or more through a mint function, or a blacklist event) | precision, recall, F1 |
| `benign` | traded normally for 30 days: many buyers, a healthy share of them sold, sells still happening in the last 10 days, the pool not drained by whoever funded it, no supply growth, no blacklist event. Famous tokens are reported apart (`group = "famous"`) | precision, recall, F1 |
| `grey` | legitimate but powerful or unfamiliar (USDC, USDT, stETH, DAI, Seaport, Permit2) and the disputed tokens | citations, the not-checked list, what it must not say; not the verdict |
| `beyond_reach` | a real incident caused by a bug in a protocol's logic, which the tools cannot see | reported, not scored: did the report say what it did not check |

Anything the rules cannot settle is left out and counted. In particular a token whose pool was
drained by its first liquidity provider is neither flagged nor benign, and a trap that springs after
the early trading cannot be seen in Transfer events (groklet and meowe traded normally for about 36
hours first): such a token is left out, not flagged.

## Flagged is five contracts

The outcome rules found no new flagged token in 555 measured, so `flagged` is the five existing
cards, all dev only (decision 2026-10-28: keep them for now). Flagged recall is therefore a dev-only
figure on five contracts, and for the four honeypots the verdict is forced to `high_risk` by the
validator, so that part measures the tool and the rule, not the model. Every scoring result carries
a note saying this while a split has fewer than ten flagged contracts.

## The split and the tool-use set

Fixed by rule, reproduced by a test from the cards alone: within each class, units (a code family,
all its members together) are ordered by the SHA-256 of `chain:address`; the first third, rounded to
the nearest unit, is held out. The eight contracts of `agent/contracts.toml` and the disputed tokens
are dev only, and a family containing one of them is dev as a whole. One contract per code family is
kept (the one with the smallest key); `family_size` says how many were seen. The tool-use set is the
20 dev contracts chosen by a fixed rule so that every class and trajectory (proxy, official
protocol, not a token, honeypot, token with powers, plain token, beyond reach) is present
(`tool_use = true`).

## Sources and limits

- Candidate tokens: pairs with WETH created on Uniswap V2 in five 20,000-block windows between July
  and September 2026, read from Etherscan's log endpoint (the node plan allows `eth_getLogs` over
  only 10 blocks). Pinned an hour after the pair first held reserves; 30 days of outcome after that.
- Dates: flagged and beyond-reach cases are from July to September 2026, after the agent models'
  training cutoffs as far as known. Famous benign tokens and the grey ones may be remembered.
- Wallet counts are of distinct addresses in Transfer logs; a router missing from
  `evals/collect/rules.py` can hide a crowd. Only the earliest 5,000 transfers after the pinned block
  are read per token. Very high sell taxes with working sells are not found by the outcome rule.
- Etherscan's "verified" flag is today's fact, not the state at the pinned block.
