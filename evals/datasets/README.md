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
