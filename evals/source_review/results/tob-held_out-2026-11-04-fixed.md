# Trail of Bits items: plain anthropic/claude-opus-5.5 against our source review (held_out, 2026-11-04)

**DRAFT LABELS and a judge that is a model's opinion: the labels were made by rule from ToB's own Target line, and a pair counts as found only if the judges agree. One run per stage at temperature 0. Read the intervals: they resample reports, and there are few.**

**POST-HOC: pipeline run again after the reply-reading fix (3982f7a): one fenced reply read, one retry; the pre-registered result is the original report (b33e224)**

22 labelled findings; plain's screening run found 2 and left 20 hard (missed or mislabelled) in 6 reports. Judges: z-ai/glm-5.3-flash, qwen/qwen3.7-flash, deepseek/deepseek-v4.1-flash-1017 (two or more independent judges (different companies)). Rubric bed5932ecd85 (NOT approved by Aryaan).

## Headline: the hard findings, pipeline against a fresh plain run

20 hard findings in 6 reports (intervals resample reports).

| | found | recall |
|---|---|---|
| fresh plain (P2) | 1/20 | 0.05 |
| pipeline (A) | 1/20 | 0.05 |

Difference (A - P2), points: 0.00 [-0.17–0.20]. Relative improvement (A - P2) / P2: 0.00 [-1.00–2.00]. **No improvement shown** (the interval includes 0 or is undefined).

Paired: both 0, pipeline only 1, plain only 1, neither 18 (exact sign test p = 1.0; findings of one report are not independent).
Unanimous reading (every judge says `same`): plain 1, pipeline 1 of 20; difference 0.00 [-0.17–0.20].
Lenient reading (any judge says `same`): plain 1, pipeline 1 of 20; difference 0.00 [-0.17–0.20].

## Descriptive

- All labelled findings in the scored reports, found: screening plain 1/21, fresh plain 1/21, pipeline 2/21.
- Repeat noise (screening against fresh plain, same findings): found then found 0, found then missed 1, missed then found 1, missed then missed 19.
- Mislabelled (right place, wrong bug): screening 4; among the hard findings, fresh plain 4, pipeline 6.
- Hard findings by difficulty: High: 10 hard, plain 0, pipeline 1, Low: 9 hard, plain 1, pipeline 0, Medium: 1 hard, plain 0, pipeline 0.
- Hard findings by year: 2023: 3 hard, plain 1, pipeline 0, 2025: 17 hard, plain 0, pipeline 1.
- Hard findings by severity: high: 9 hard, plain 0, pipeline 0, medium: 8 hard, plain 1, pipeline 1, undetermined: 3 hard, plain 0, pipeline 0.
- Flags matching no labelled finding (a lower bound on precision: real bugs the auditors did not list count here): screen 14 of 21; fresh 12 of 19; pipeline 45 of 54.
- Cost and time per report: screen $3.67 (46 s, 7 calls); fresh $3.11 (41 s, 6 calls); pipeline $4.62 (75 s, 24 calls); agents $11.40 (of which $0.00 for replies cut off at the output limit and run again); judges $0.24.
- Unreadable replies: screen none; fresh none; pipeline none. Pairs the judge could not answer in screening: 0.
- Requests sent a second time because the first reply was not the JSON asked for: pipeline 2 (their cost is in the totals above).

## Do the judges differ?

302 judged pairs: all judges agree on 278; exactly one judge is in the minority on 24; other splits 0.

| judge | minority | share of pairs | share of minority cases | more generous | stricter | said same / same_place / unrelated |
|---|---|---|---|---|---|---|
| z-ai/glm-5.3-flash | 10 | 0.0331 | 0.4167 | 5 | 5 | 7 / 18 / 277 |
| qwen/qwen3.7-flash | 9 | 0.0298 | 0.375 | 9 | 0 | 7 / 27 / 268 |
| deepseek/deepseek-v4.1-flash-1017 | 5 | 0.0166 | 0.2083 | 3 | 2 | 7 / 19 / 276 |

Exact agreement between each two judges: z-ai/glm-5.3-flash | qwen/qwen3.7-flash: 0.9371; z-ai/glm-5.3-flash | deepseek/deepseek-v4.1-flash-1017: 0.9503; qwen/qwen3.7-flash | deepseek/deepseek-v4.1-flash-1017: 0.9536.

## Per finding

| finding | screening | fresh plain | pipeline |
|---|---|---|---|
| 2023-04-tempus-raft:TOB-RAFT-4 | found | - | - |
| 2023-04-tempus-raft:TOB-RAFT-7 | missed | missed | missed |
| 2023-04-tempus-raft:TOB-RAFT-8 | missed | found | mislabelled |
| 2023-07-sandclock:TOB-SANDCLOCK-1 | mislabelled | mislabelled | mislabelled |
| 2024-05-lisksmartcontracts:TOB-LSK2-9 | found | - | - |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-11 | mislabelled | missed | mislabelled |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-15 | missed | missed | missed |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-16 | missed | missed | missed |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-18 | missed | missed | missed |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-4 | missed | missed | missed |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-6 | missed | missed | mislabelled |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-7 | missed | missed | mislabelled |
| 2025-01-bacon-labs-bunniv2:TOB-BUNNI-9 | missed | missed | mislabelled |
| 2025-02-automata-dcap-attestation-onchain-pccs:TOB-AUT-13 | missed | missed | missed |
| 2025-02-automata-dcap-attestation-onchain-pccs:TOB-AUT-5 | missed | mislabelled | missed |
| 2025-02-automata-dcap-attestation-onchain-pccs:TOB-AUT-6 | missed | missed | missed |
| 2025-02-automata-dcap-attestation-onchain-pccs:TOB-AUT-7 | mislabelled | mislabelled | missed |
| 2025-02-chainlink-customsendersreceivers:TOB-CHAINLINK-CSR-5 | missed | missed | found |
| 2025-04-reserve-folio-solidity:TOB-FOLIO-5 | missed | missed | missed |
| 2025-04-reserve-folio-solidity:TOB-FOLIO-6 | mislabelled | mislabelled | missed |
| 2025-04-reserve-folio-solidity:TOB-FOLIO-7 | missed | missed | missed |
| 2025-04-reserve-folio-solidity:TOB-FOLIO-8 | missed | missed | missed |

## What this can and cannot show

- The labels are drafts made by rule from ToB's Target line; the judge is a model checked against Aryaan's reading of a sample, not against truth.
- ToB reports and their code are public and the model may have seen them; older reports are likelier to have been seen (see the year table).
- A flag that matches no listed finding may still be a real bug.
- Findings in one report share code and are not independent; the intervals resample reports.
