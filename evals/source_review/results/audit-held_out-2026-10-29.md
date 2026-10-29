# Audit items: plain anthropic/claude-opus-5.5 against our source review (2026-10-29)

**DRAFT LABELS (unreviewed). Public reports: the model may have seen them. One run per file at temperature 0. Rules and headline were fixed before any run: evals/datasets/audit_items/README.md.**
Code at `8911b34`; spent $3.70 of a cap of $5.00; 21 files run.

## held_out: 26 judged issues in 21 files

| | found | recall [95% CI over files] |
|---|---|---|
| plain | 11/26 | 0.42 [0.24–0.61] |
| pipeline | 12/26 | 0.46 [0.27–0.67] |

- Difference (pipeline minus plain): 0.04 [-0.08–0.17] points; relative improvement (A - P) / P: 0.09 [-0.20–0.57].
- Paired: both 10, pipeline only 2, plain only 1, neither 13. Sign test p = 1.0 over issues, 1.0 over files (2 files pipeline ahead, 1 plain ahead).
- **Claim allowed by the pre-registration:** none. No improvement is shown.
- By stratum (plain / pipeline found of n): hard 3/4 of 11, other 8/8 of 15, high 2/2 of 4, medium 9/10 of 22

## Flags, cost, failures

| arm | flags/file | medium+/file | in a judged fn | cost | median s | calls |
|---|---|---|---|---|---|---|
| P | 2.1905 | 1.4286 | 14/46 | $1.556 | 16.2648 | 21 |
| A | 3.619 | 2.619 | 16/76 | $2.141 | 13.6792 | 21 |

A flag outside every judged function is not necessarily false: the judged list holds only the issues the contest accepted.
Unreadable replies: P none, A none. Arm A coverage: {'ran': 21}.
