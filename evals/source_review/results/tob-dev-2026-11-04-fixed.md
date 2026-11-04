# Trail of Bits items: plain anthropic/claude-opus-5.5 against our source review (dev, 2026-11-04)

**DRAFT LABELS and a judge that is a model's opinion: the labels were made by rule from ToB's own Target line, and a pair counts as found only if the judges agree. One run per stage at temperature 0. Read the intervals: they resample reports, and there are few.**

**POST-HOC: pipeline run again after the reply-reading fix (3982f7a): one fenced reply read, one retry; the pre-registered result is the original report (b33e224)**

29 labelled findings; plain's screening run found 8 and left 21 hard (missed or mislabelled) in 5 reports. Judges: z-ai/glm-5.3-flash, qwen/qwen3.7-flash, deepseek/deepseek-v4.1-flash-1017 (two or more independent judges (different companies)). Rubric bed5932ecd85 (NOT approved by Aryaan).

## Headline: the hard findings, pipeline against a fresh plain run

21 hard findings in 5 reports (intervals resample reports).

| | found | recall |
|---|---|---|
| fresh plain (P2) | 1/21 | 0.05 |
| pipeline (A) | 2/21 | 0.10 |

Difference (A - P2), points: 0.05 [0.00–0.07]. Relative improvement (A - P2) / P2: 1.00 [0.00–3.00]. **No improvement shown** (the interval includes 0 or is undefined).

Paired: both 1, pipeline only 1, plain only 0, neither 19 (exact sign test p = 1.0; findings of one report are not independent).
Unanimous reading (every judge says `same`): plain 1, pipeline 1 of 21; difference 0.00 [-0.27–0.07].
Lenient reading (any judge says `same`): plain 1, pipeline 2 of 21; difference 0.05 [0.00–0.07].

## Descriptive

- All labelled findings in the scored reports, found: screening plain 8/29, fresh plain 4/29, pipeline 10/29.
- Repeat noise (screening against fresh plain, same findings): found then found 3, found then missed 5, missed then found 1, missed then missed 20.
- Mislabelled (right place, wrong bug): screening 8; among the hard findings, fresh plain 6, pipeline 6.
- Hard findings by difficulty: High: 7 hard, plain 0, pipeline 1, Low: 9 hard, plain 1, pipeline 1, Medium: 5 hard, plain 0, pipeline 0.
- Hard findings by year: 2023: 7 hard, plain 0, pipeline 0, 2025: 14 hard, plain 1, pipeline 2.
- Hard findings by severity: high: 8 hard, plain 1, pipeline 1, medium: 13 hard, plain 0, pipeline 1.
- Flags matching no labelled finding (a lower bound on precision: real bugs the auditors did not list count here): screen 19 of 32; fresh 18 of 26; pipeline 39 of 57.
- Cost and time per report: screen $2.82 (51 s, 5 calls); fresh $2.81 (43 s, 5 calls); pipeline $3.59 (68 s, 18 calls); agents $11.06 (of which $1.84 for replies cut off at the output limit and run again); judges $0.61.
- Unreadable replies: screen none; fresh none; pipeline none. Pairs the judge could not answer in screening: 0.

## Do the judges differ?

894 judged pairs: all judges agree on 858; exactly one judge is in the minority on 36; other splits 0.

| judge | minority | share of pairs | share of minority cases | more generous | stricter | said same / same_place / unrelated |
|---|---|---|---|---|---|---|
| z-ai/glm-5.3-flash | 11 | 0.0123 | 0.3056 | 7 | 4 | 24 / 42 / 828 |
| qwen/qwen3.7-flash | 13 | 0.0145 | 0.3611 | 8 | 5 | 24 / 42 / 828 |
| deepseek/deepseek-v4.1-flash-1017 | 12 | 0.0134 | 0.3333 | 4 | 8 | 24 / 35 / 835 |

Exact agreement between each two judges: z-ai/glm-5.3-flash | qwen/qwen3.7-flash: 0.9732; z-ai/glm-5.3-flash | deepseek/deepseek-v4.1-flash-1017: 0.9743; qwen/qwen3.7-flash | deepseek/deepseek-v4.1-flash-1017: 0.972.

## Per finding

| finding | screening | fresh plain | pipeline |
|---|---|---|---|
| 2023-04-ajnalabs:TOB-AJNA-4 | missed | mislabelled | unjudged |
| 2023-04-ajnalabs:TOB-AJNA-6 | missed | mislabelled | missed |
| 2023-05-fraxgov:TOB-FRAXGOV-1 | mislabelled | missed | mislabelled |
| 2023-05-fraxgov:TOB-FRAXGOV-3 | found | - | - |
| 2023-05-fraxgov:TOB-FRAXGOV-5 | found | - | - |
| 2023-07-arcade:TOB-ARCADE-12 | missed | missed | unjudged |
| 2023-07-arcade:TOB-ARCADE-13 | missed | missed | missed |
| 2023-07-arcade:TOB-ARCADE-16 | mislabelled | missed | missed |
| 2023-07-arcade:TOB-ARCADE-8 | mislabelled | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-1 | mislabelled | missed | unjudged |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-12 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-13 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-14 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-15 | mislabelled | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-16 | missed | missed | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-17 | found | - | - |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-18 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-19 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-2 | missed | mislabelled | mislabelled |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-28 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-3 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-4 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-5 | missed | missed | missed |
| 2025-05-caplabs-coveredagentprotocol:TOB-CAP-6 | missed | missed | found |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-1 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-2 | found | - | - |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-3 | mislabelled | found | found |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-4 | mislabelled | mislabelled | mislabelled |
| 2025-10-radiustechnology-evmauth:TOB-RADIUS-5 | found | - | - |

## What this can and cannot show

- The labels are drafts made by rule from ToB's Target line; the judge is a model checked against Aryaan's reading of a sample, not against truth.
- ToB reports and their code are public and the model may have seen them; older reports are likelier to have been seen (see the year table).
- A flag that matches no listed finding may still be a real bug.
- Findings in one report share code and are not independent; the intervals resample reports.
