# Trail of Bits items: plain deepseek/deepseek-v4.1-flash-1017 against our source review (dev, 2026-11-05)

**DRAFT LABELS and a judge that is a model's opinion: the labels were made by rule from ToB's own Target line, and a pair counts as found only if the judges agree. One run per stage at temperature 0. Read the intervals: they resample reports, and there are few.**

**WIDE KEY (post-hoc, dev only, not the headline): the Low and Informational findings that name a file of the code are added, each judged only against the flags in its own files, so found counts are lower bounds. Skipped for lack of a file: 2023-07-arcade:TOB-ARCADE-6, 2023-05-fraxgov:TOB-FRAXGOV-4, 2023-04-ajnalabs:TOB-AJNA-5.**

**POST-HOC: DeepSeek (thinking off), two-pass pipeline, earlier prompt: wide-key view, development run, exploratory**

**JUDGE WAIVER (dev only, not quotable): dev run: DeepSeek is the model under test and also one of the three judges (Aryaan, 2026-11-04: keep it for now)**

Models by stage: screen deepseek/deepseek-v4.1-flash-1017 [reasoning off]; fresh deepseek/deepseek-v4.1-flash-1017 [reasoning off]; pipeline deepseek/deepseek-v4.1-flash-1017 [reasoning off] [two_pass]

62 labelled findings; plain's screening run found 16 and left 46 hard (missed or mislabelled) in 5 reports. Judges: z-ai/glm-5.3-flash, qwen/qwen3.7-flash, deepseek/deepseek-v4.1-flash-1017 (two or more independent judges (different companies); deepseek/deepseek-v4.1-flash-1017 is the model's own company). Rubric bed5932ecd85 (NOT approved by Aryaan).

## Headline: the hard findings, pipeline against a fresh plain run

46 hard findings in 5 reports (intervals resample reports).

| | found | recall |
|---|---|---|
| fresh plain (P2) | 1/46 | 0.02 |
| pipeline (A) | 5/46 | 0.11 |

Difference (A - P2), points: 0.09 [-0.09–0.20]. Relative improvement (A - P2) / P2: 4.00 [-1.00–14.00]. **No improvement shown** (the interval includes 0 or is undefined).

Paired: both 0, pipeline only 5, plain only 1, neither 40 (exact sign test p = 0.2188; findings of one report are not independent).
Unanimous reading (every judge says `same`): plain 1, pipeline 3 of 46; difference 0.04 [-0.09–0.12].
Lenient reading (any judge says `same`): plain 5, pipeline 6 of 46; difference 0.02 [-0.15–0.15].

## Descriptive

- All labelled findings in the scored reports, found: screening plain 16/62, fresh plain 11/62, pipeline 13/62.
- Repeat noise (screening against fresh plain, same findings): found then found 10, found then missed 6, missed then found 1, missed then missed 45.
- Mislabelled (right place, wrong bug): screening 27; among the hard findings, fresh plain 29, pipeline 17.
- Hard findings by difficulty: High: 19 hard, plain 1, pipeline 4, Low: 13 hard, plain 0, pipeline 0, Medium: 14 hard, plain 0, pipeline 1.
- Hard findings by year: 2023: 23 hard, plain 1, pipeline 0, 2025: 23 hard, plain 0, pipeline 5.
- Hard findings by severity: high: 8 hard, plain 1, pipeline 0, informational: 14 hard, plain 0, pipeline 0, low: 13 hard, plain 0, pipeline 2, medium: 11 hard, plain 0, pipeline 3.
- Flags matching no labelled finding (a lower bound on precision: real bugs the auditors did not list count here): screen 72 of 116; fresh 107 of 156; pipeline 65 of 126.
- Cost and time per report: screen $0.05 (19 s, 5 calls); fresh $0.04 (33 s, 5 calls); pipeline $0.20 (128 s, 36 calls); agents $0.29 (of which $0.00 for replies cut off at the output limit and run again); judges $2.96.
- Unreadable replies: screen none; fresh none; pipeline none. Pairs the judge could not answer in screening: 0.

## Do the judges differ?

3558 judged pairs: all judges agree on 3364; exactly one judge is in the minority on 183; other splits 11.

| judge | minority | share of pairs | share of minority cases | more generous | stricter | said same / same_place / unrelated |
|---|---|---|---|---|---|---|
| z-ai/glm-5.3-flash | 63 | 0.0177 | 0.3443 | 48 | 15 | 49 / 236 / 3273 |
| qwen/qwen3.7-flash | 63 | 0.0177 | 0.3443 | 45 | 18 | 67 / 207 / 3284 |
| deepseek/deepseek-v4.1-flash-1017 | 57 | 0.016 | 0.3115 | 7 | 50 | 40 / 169 / 3349 |

Exact agreement between each two judges: z-ai/glm-5.3-flash | qwen/qwen3.7-flash: 0.9615; z-ai/glm-5.3-flash | deepseek/deepseek-v4.1-flash-1017: 0.9632; qwen/qwen3.7-flash | deepseek/deepseek-v4.1-flash-1017: 0.9632.

## Per finding

| finding | screening | fresh plain | pipeline |
|---|---|---|---|
| 2023-04-ajnalabs:TOB-AJNA-2 | mislabelled | mislabelled | missed |
| 2023-04-ajnalabs:TOB-AJNA-3 | missed | missed | missed |
| 2023-04-ajnalabs:TOB-AJNA-4 | missed | mislabelled | missed |
| 2023-04-ajnalabs:TOB-AJNA-6 | mislabelled | mislabelled | missed |
| 2023-04-ajnalabs:TOB-AJNA-7 | missed | missed | missed |
| 2023-04-ajnalabs:TOB-AJNA-8 | missed | missed | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-1 | mislabelled | found | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-3 | missed | missed | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-5 | missed | mislabelled | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-6 | missed | missed | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-7 | missed | mislabelled | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-8 | mislabelled | missed | missed |
| 2023-07-arcade:TOB-ARCADE-1 | missed | missed | mislabelled |
| 2023-07-arcade:TOB-ARCADE-10 | mislabelled | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-11 | mislabelled | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-12 | mislabelled | mislabelled | missed |
| 2023-07-arcade:TOB-ARCADE-13 | mislabelled | mislabelled | missed |
| 2023-07-arcade:TOB-ARCADE-14 | missed | missed | missed |
| 2023-07-arcade:TOB-ARCADE-15 | mislabelled | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-16 | mislabelled | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-3 | missed | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-4 | missed | missed | missed |
| 2023-07-arcade:TOB-ARCADE-5 | found | - | - |
| 2023-07-arcade:TOB-ARCADE-7 | found | - | - |
| 2023-07-arcade:TOB-ARCADE-8 | mislabelled | mislabelled | mislabelled |
| 2023-07-arcade:TOB-ARCADE-9 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-1 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-10 | mislabelled | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-11 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-12 | mislabelled | mislabelled | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-13 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-14 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-15 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-16 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-17 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-18 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-19 | mislabelled | mislabelled | found |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-2 | mislabelled | mislabelled | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-20 | missed | mislabelled | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-21 | missed | missed | found |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-22 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-23 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-24 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-25 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-26 | mislabelled | mislabelled | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-27 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-28 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-29 | mislabelled | mislabelled | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-3 | missed | missed | found |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-4 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-5 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-6 | missed | mislabelled | found |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-7 | missed | missed | found |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-8 | mislabelled | missed | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-9 | mislabelled | mislabelled | mislabelled |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-1 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-2 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-3 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-4 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-5 | mislabelled | mislabelled | mislabelled |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-6 | missed | missed | missed |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-7 | missed | missed | missed |

## What this can and cannot show

- The labels are drafts made by rule from ToB's Target line; the judge is a model checked against Aryaan's reading of a sample, not against truth.
- ToB reports and their code are public and the model may have seen them; older reports are likelier to have been seen (see the year table).
- A flag that matches no listed finding may still be a real bug.
- Findings in one report share code and are not independent; the intervals resample reports.
