# Experiment log

One row per variant per run, appended by `python -m evals.run retrieval --log "<change>"`; the
variants are defined in `evals/variants.py` and the quiz in `evals/datasets/`. **dev** rows are
the 69 questions used to compare variants; **held-out** rows (31 questions) were run three times
only (milestone 1: dense baseline; 2: hybrid; 3: everything else) and never used to choose
anything. Rows before the last decision note below are on those original questions; the quiz
grew on 2026-10-26 (dev 87, held-out 38). Intervals are bootstrap 95% over the questions; a "paired difference" row compares two
variants on the same questions, and "not shown" means its interval includes zero. Latency is one
search call in milliseconds after warm-up, query embedding and reranking included, on an 8-core
Apple Silicon laptop (0.1 = under a tenth of a millisecond). The first run of the bigger models was
stopped after 35 minutes and is not logged; the big reranker costs 2.7 s per query at 10
candidates, 5.8 s at 20 and 15.3 s at 50, so it is compared at 20.

**Decision (2026-10-26):** the default search mode is hybrid. The rule agreed beforehand ("the
simplest variant stays the default unless a gain is shown on dev") pointed at keyword search,
because no variant was shown to beat another. Aryaan overruled it for hybrid: by question kind
(dev) keyword search is perfect on exact code names (1.00, dense 0.94) and meaning search is better
on paraphrases (0.92, keyword 0.81), the corpus is meant to grow, and 69 questions are too few to
show a difference this size. The held-out set was not used for the decision. The CI baseline now
guarded `hybrid-small` (it is `dense-small` since the final note below).

**Decision (2026-10-26, later): the default embedder is Qwen3-Embedding-8B through OpenRouter.**
Chosen on dev only. Dense recall@5 / MRR: bge-small 0.87 / 0.81, Qwen3-8B 0.92 / 0.93,
text-embedding-3-large 0.90 / 0.89, gemini-embedding-2 0.89 / 0.90. The MRR gain over bge-small is
shown for all three (Qwen +0.12 [+0.06..+0.18]); the recall@5 gain is not shown for any
(Qwen +0.05 [-0.01..+0.12]). Qwen has the best point estimates in both dense and hybrid and costs
$0.01 per million tokens (OpenAI $0.13, Gemini $0.20); the three hosted models were not tested
against each other and their intervals overlap almost entirely. Costs of the whole comparison: about
$0.007. Latency per query: bge-small 7 ms, OpenAI 370 ms (95th percentile 0.6 s), Gemini 520 ms
(0.6 s), Qwen 420 ms with a 95th percentile of 5.6 to 6.5 s and a maximum of 14 s (two separate
runs, no retries involved). The laptop sleeping during long runs froze several of them; those
runs were repeated and are not logged. A held-out check of the winner (milestone 4, declared as an
extra look) agrees in direction and shows no gain: hybrid recall@5 0.96 against 0.95 for bge-small.

**Quiz grown (2026-10-26, last): 25 code fragments added, so dev is now 87 questions and
held-out 38.** The fragments are real listings cut from audit reports (14 from findings that are in
the index, 11 from reports that are not); see `evals/datasets/README.md`. Labels were fixed before
any search ran on them. The 69 old dev questions score exactly as before (checked against the saved
baseline), and the baseline was re-saved on the 87. The held-out file was not read for the new
questions. Recall@5 on dev: code-style queries (n=27) are the weakest style for every variant:
keyword 0.57, bge-small meaning-only 0.67, hybrid 0.63, Qwen meaning-only 0.76, Qwen hybrid 0.70,
against 0.87 to 0.95 for question-style. Fragments quoted from an indexed finding (n=10) score 0.80
to 0.85; fragments from code that is not in the index, where only the pattern can match (n=8),
score 0.12 (keyword) to 0.50 (Qwen meaning-only, MiniLM rerank). Those intervals are wide (Qwen
meaning-only on the 8: 0.50 [0.12-0.88]) and no variant is shown to differ from another on code
(hybrid-small to hybrid-qwen8b +0.07 [+0.00..+0.19]). Chunk text with code examples is the only
change that moved code in the expected direction (code 0.74 against 0.63, unseen 0.50 against 0.25,
total +0.03 [-0.01..+0.07], not shown); it was the first thing to retest on a bigger code set (superseded:
under meaning-only search it does not move code, see the final decision). No default was changed. Qwen's latency on these runs was 1.7 s median and 6.9 s at the 95th
percentile (slower than on the 25th); the 87 queries cost about $0.0001.

**Decision (2026-10-26, final): keyword search is removed and the default is meaning-only
search.** Aryaan's call after the quiz grew. Basis, dev (87 questions), recall@5: bge-small
meaning-only 0.82, hybrid 0.81, keyword alone 0.78; Qwen meaning-only 0.87, hybrid 0.84. On the 27
code-style questions: keyword 0.57, bge-small hybrid 0.63, bge-small meaning-only 0.67, Qwen hybrid
0.70, Qwen meaning-only 0.76. Held-out (the original 31 questions, milestone 4): Qwen meaning-only
0.97, hybrid 0.96. No difference among these is shown to be real; the case for removal is that
hybrid never won, was no better on code, and word scoring was extra code to maintain and explain.
Removed: BM25, the tokenizer, rank fusion, the modes `bm25`, `hybrid` and `hybrid_rerank`, the
variants `bm25*` and `hybrid-*`, and the chunks' `keywords` field (the taxonomy's `source_keywords`
stay in `knowledge/`, unread by retrieval). Every row above that names a removed variant is
history, and so are the `rerank-*`, `stack-*` and `recipe-*` rows dated before this note, which ran
over the hybrid list; reproduce them at commit `90d2cb1`. After the removal the meaning-only
variants score exactly as before on the same questions (bge-small 0.82 / 0.77, bge-base 0.82 / 0.79;
Qwen 0.87 recall, MRR 0.88 against 0.89 earlier: rebuilding the Qwen vectors through OpenRouter
moved one ranking, so hosted embeddings are not bit-for-bit repeatable). The reranker now
re-scores the meaning list: dense-small to rerank-small +0.01 [-0.05..+0.08] (0.84 against 0.82),
not shown, about 2.5 s a query. Chunk text with code examples no longer looks like a lever: with
meaning search it moves code from 0.67 to 0.69 and the total by +0.01 [-0.03..+0.04]; the
earlier gain was with hybrid. The CI baseline is now `dense-small`. Qwen latency in these runs was
2.0 s median and 8.8 s at the 95th percentile (slower than on the 25th).

## Faithfulness judge: check-set trials

Component 6, part D. Each row is one judge asked about the committed check set (`evals/judge/checkset/`,
dev traces only; the faults are **ours**: 39 planted edits of real Sonnet claims and 5 claims found
unsupported by an earlier reader, against 46 sound claims believed to follow from their evidence, plus
15 summaries). The judge is a model's opinion; it was **not** checked against Aryaan's own labels (the
spec asks for that) and it is not truth. Rubric draft `faa679eb…` (unapproved). Rates are k/n with a
Wilson 95% interval; "caught" means the label is one the fault should get (`partly` counts for a stretched
claim, not for a flipped fact); "false alarm" is a sound item labelled `not_supported` or `contradicted`;
"downgraded" is a sound item labelled `partly`. Items a judge did not label are in no denominator.
The full records are `evals/judge/trials/2026-10-28-stage1.json` and, for the third column below,
`evals/judge/trials/2026-10-28-deepseek.json`.

| date | change | judge | what | n | caught | false alarm | notes |
|---|---|---|---|---|---|---|---|
| 2026-10-28 | stage 1, first look at the check set | qwen/qwen3.7-flash | claims | 44 faults, 46 sound | 37/44 = 0.84 [0.71–0.92] | 0/46 = 0.00 [0.00–0.08] | downgraded 11/46 (0.24). Changed numbers caught 3/7. Thinks first: 27 of 103 replies were cut off at 2,500 tokens, so the retry used 6,000 (then 0 refused). $0.046 |
| 2026-10-28 | stage 1 | qwen/qwen3.7-flash | summary sentences | 14 faults, 38 sound | 13/14 = 0.93 [0.69–0.99] | 2/38 = 0.05 [0.01–0.17] | strict (`not_supported`/`contradicted`) 10/14 |
| 2026-10-28 | stage 1 | z-ai/glm-5.3-flash | claims | 41 faults, 46 sound | 39/41 = 0.95 [0.84–0.99] | 1/46 = 0.02 [0.00–0.11] | 3 faults unlabelled (reply cut off, even at 12,000 tokens). downgraded 10/46 (0.22). Missed: `flip-milka-owner`, `num-milka-block`. $0.098 |
| 2026-10-28 | stage 1 | z-ai/glm-5.3-flash | summary sentences | 14 faults, 38 sound | 14/14 = 1.00 [0.78–1.00] | 0/38 = 0.00 [0.00–0.09] | strict 13/14 |
| 2026-10-28 | two judges together (Alibaba, Z.ai) | both | all scored units | 139 both labelled | exact label 110/139 = 0.79 [0.72–0.85] | | supported-or-not 124/139 = 0.89 [0.83–0.93], kappa 0.78; 29 disagreements listed in the record. Agreement is consistency, not correctness |
| 2026-10-28 | stability: the same inputs asked again | z-ai/glm-5.3-flash | all scored units | 133 both labelled | | | 7 labels changed (5%), 4 of them between supported and not (3%); 9 items unlabelled in one pass or the other. $0.072 |
| 2026-10-28 | third column, own $0.20 cap (same company as the agent: not for the kept pair) | deepseek/deepseek-v4.1-flash-1017 | claims | 44 faults, 46 sound | 42/44 = 0.95 [0.85–0.99] | 0/46 = 0.00 [0.00–0.08] | strict 23/44. downgraded 8/46 (0.17). Missed: `cite-pair-identical`, `num-milka-block`. Changed numbers caught 6/7. 0 unlabelled, none cut off. $0.058 for the pass |
| 2026-10-28 | third column | deepseek/deepseek-v4.1-flash-1017 | summary sentences | 14 faults, 38 sound | 14/14 = 1.00 [0.78–1.00] | 0/38 = 0.00 [0.00–0.09] | strict 12/14 |
| 2026-10-28 | third column: agreement with Alibaba | deepseek flash, qwen | all scored units | 142 both labelled | exact label 115/142 = 0.81 [0.74–0.87] | | supported-or-not 126/142 = 0.89 [0.82–0.93], kappa 0.77; 27 disagreements listed in the record. On the 139 items glm also labelled: 114/139 and 124/139 |
| 2026-10-28 | third column: agreement with Z.ai | deepseek flash, glm | all scored units | 139 both labelled | exact label 121/139 = 0.87 [0.80–0.92] | | supported-or-not 133/139 = 0.96 [0.91–0.98], kappa 0.91; 18 disagreements. Agreement is consistency, not correctness |
| 2026-10-28 | third column: items from deepseek-v4-pro reports | deepseek flash (qwen, glm) | 3 claims + 4 summary sentences, all faults | 7 units from 5 items | 7/7 (qwen 5/7, glm 7/7) | | strict 3/7 (qwen 2/7, glm 5/7). n is tiny and none of the 7 is a sound item: see the paragraph below |

Spend: $0.2163 of the approved $0.33 for stage 1. Two things found by the trial, both fixed in the open: a strict
JSON-schema request found no provider for qwen3.7-flash (HTTP 404, nothing charged), so it is asked
for plain JSON and read strictly; and the answer key for one real summary (deepseek-v4-pro on Seaport)
was wrong, because its unsupported phrases are repeated in the report's own findings, so the summary
rule correctly calls it supported; it was removed after the first look (summary catch before the
correction: qwen 13/17, glm 14/17). With 44 faults per judge, differences under about 10 points are
not shown, so "glm catches more" is a lean, not a result.

Third column, `deepseek-v4.1-flash` as a judge, at Aryaan's request and with its own hard cap of $0.20.
It is the same company, and the same model, as the agent's eval tier, so shared blind spots and a taste
for its own wording are possible; the kept pair stays Alibaba + Z.ai. Spend $0.0575 (a 23-call probe
$0.0081, then the other 79 calls $0.0495; no cut-off replies, so no retry round); it equals the sum of the
102 per-call costs OpenRouter reported, $0.057521. With stage 1 the judge trial has cost $0.274 of the
$0.50 ceiling. A strict JSON-schema request was accepted on the first call. On this check set it does
as well as or better than the other two on the headline counts (claim catch 0.95 against glm 0.95 and
qwen 0.84, no false alarm, nothing unlabelled, fewer `partly` on sound claims, 0.17 against 0.22 and
0.24; strict-label catch is level with glm, 23 of 44 against 23 of 41) and costs $0.00056 a call,
between qwen and glm; the intervals overlap, so it is not shown to differ from glm. It agrees with glm
more (supported-or-not 0.96, kappa 0.91) than qwen does (0.89, 0.78); all three missed
`num-milka-block`. Self-preference cannot be tested here: the check set is mostly Sonnet-written,
and the only DeepSeek-written items are 3 claims and 4 summary sentences from 2 `deepseek-v4-pro`
reports, all faults. It flagged all 7 but gave a strict label to 3, glm to 5; elsewhere in the summaries
it gave a strict label to 10 of 10 faults. That is a hint of leniency on its sibling's wording at n = 7
from two reports; it is equally what two borderline sentences would give (two of the gaps with glm are
sentences #1 and #2 of one USDC summary, true of the real USDC but not stated in the evidence).

## Run-to-run noise study

Component 6, part F, step 1. The eval-tier agent (`deepseek/deepseek-v4.1-flash-1017`) run 5 times on each of
10 dev contracts chosen by a rule fixed before any run (`evals/noise-study.md`, registered at `b427b83`),
one run at a time, at the pinned blocks: 50 runs, none failed. Run-set `noise-eval-r5`, code at `6b7f1a2`,
analysis corrected at `bf5701a` (see the last attribution row); full record
`evals/results/2026-10-28-6b7f1a24-noise-eval-r5.json`. Runs of a contract are compared with each other only;
n is contracts unless it says runs or pairs. Two intervals for a share of contracts: the exact binomial one
and, in brackets after it, the bootstrap over contracts that the other layers use (it collapses at 0 or 10).
The cards' labels (computed by script, reviewed by nobody) are used only for the two pass-stability rows and
the last row.

| date | change | what | n | result [95% CI] | notes |
|---|---|---|---|---|---|
| 2026-10-28 | noise study | verdict differs between the 5 runs (an abstain counts as a verdict) | 10 contracts, 50 runs | 2/10 = 0.20 [exact 0.03–0.56; bootstrap 0.00–0.50] | `tok-e2f2f46b` 4x elevated_risk + 1x no_red_flags_found; `tok-f7755634` 4x elevated_risk + 1x insufficient_evidence. The other 8 gave one verdict in all 5 runs (Seaport 5x no_red_flags_found) |
| 2026-10-28 | noise study | verdict flips between warn and clear, abstains left out | 10 contracts | 1/10 = 0.10 [exact 0.00–0.44; bootstrap 0.00–0.30] | only `tok-e2f2f46b`. One flip among ten contracts leaves an upper bound of 0.44, so it cannot rule out that a third of contracts flip |
| 2026-10-28 | noise study | abstains (`insufficient_evidence`), counted apart | 50 runs | 1/50 = 0.02 [bootstrap 0.00–0.06] | one run of `tok-f7755634`, ended because its report was rejected by the validator. The 25-call cap was hit in 4/50 runs (`inc-termfinance` 3 of 5, `tok-f7755634` 1 of 5); all four still ended with an accepted report |
| 2026-10-28 | noise study | the set of tools called changes between the 5 runs | 10 contracts | 8/10 = 0.80 [exact 0.44–0.97; bootstrap 0.50–1.00] | `read_storage`, `search_knowledge`, `diff_against_reference` and `read_getter` come and go; 6 to 25 tool calls a run. A change of tools did not by itself change a verdict (Seaport changed `search_knowledge` in 4 of 5 runs and kept its verdict) |
| 2026-10-28 | noise study | cause, for the pairs of runs of one contract with a different warn/clear verdict | 4 pairs, 1 contract | tools 0 of 4, model 4 of 4 | the model: in every pair the two runs made different calls and every call both made returned the same result. Pairs of one contract are not independent, so read it as one contract. The 4 pairs with an abstain, apart: the same split |
| 2026-10-28 | noise study | the same question asked twice (identical call, 13 tools) | 1,362 comparisons of an identical call in two runs of one contract (100 pairs of runs) | 0 returned a different result | the tools are repeatable at the pinned block, including `simulate_sell` 50 and `get_verified_source` 123. The first printed report said 20 comparisons did (`lookup_selector` 16 of 96, `taxonomy_lookup` 4 of 76) and "tools 1 of 4"; both were the analysis, corrected in `bf5701a`: `lookup_selector` counts names from the cache (cold first run, warm later runs), `taxonomy_lookup` without arguments reads the evidence gathered so far (a groklet run that identified the Uniswap pair first got a different question) |
| 2026-10-28 | noise study | pass stability, for the 6 scored contracts (verdict in the card's accepted set) | 6 contracts, 30 runs | one run right 0.70 [0.37–1.00]; the majority of 5 right 0.67 [0.33–1.00]; every run right 0.67 [0.33–1.00] | 4 contracts always right, 1 sometimes, 1 never. See the last row |
| 2026-10-28 | noise study | pass stability of the tool-use rules | 10 contracts, 50 runs | one run 0.90 [0.76–1.00]; majority 0.90 [0.70–1.00]; every run 0.80 [0.50–1.00] | 8 contracts always pass, 2 sometimes |
| 2026-10-28 | noise study | time, sequential, eval tier | 49 of 50 runs | median 39.5 s; 95th percentile 72.8 s [63.4–84.3]; max 84.3 s | model 31.6 s and tools 5.9 s of the median. Warm runs median 37.8 s (n = 41), mixed 43.0 s (n = 8). One run left out: the laptop slept from 07:54 to 08:14 UTC, the run in flight froze and then finished |
| 2026-10-28 | noise study | cost | 50 runs | $0.3241 ($0.0065 a run, dearest $0.022) | estimate $0.28, cap $0.75. OpenRouter's own meter moved $0.3244; the $0.0003 difference is the hosted embedding of knowledge searches, which the cap does not count |
| 2026-10-28 | noise study | decision rule for R (written before the run) | 1 contract flips warn/clear, 2 differ in any verdict | moderate: quick R=1 to check nothing broke, R=3 to compare two changes; milestone R=3 then 2 more repeats for contracts that disagree (cap 5) | a proposal, **not approved by Aryaan**. Neither override applied (1 abstaining run, limit 5; 0 of 4 differing-verdict pairs caused by tools, limit half) |
| 2026-10-28 | noise study | the two benign obscure tokens in the study against their labels | 2 contracts, 10 runs | `tok-e2f2f46b` right in 1 of 5 runs, `tok-f7755634` in 0 of 5 | the labels say benign, the agent says elevated_risk in 4 of 5 runs of each because it reads a bot-blacklist, a tax setter and an owner withdraw in the code. That is the agent and the script-computed label disagreeing, not noise; no card was changed |

## First full dev run

Component 6, first `make eval-run`. The eval-tier agent (`deepseek/deepseek-v4.1-flash-1017`) on all 29 dev cards,
3 repeats each, at the pinned blocks, one run at a time: 87 runs, none failed. Run-set `dev-eval-r3`, code at
`0c888d0` (clean tree), no judge; full record `evals/results/2026-10-28-0c888d04-dev-eval-r3.json`. Every
label is computed by script and **nobody has reviewed them** (29 of 29): the numbers say "agrees with the script and
the written rules", not "agrees with Aryaan". Only 5 flagged and 12 benign contracts are scored on the verdict, so
the detection intervals are wide. Intervals resample contracts (a contract's 3 runs stay together).

| date | change | what | n | result [95% CI] | notes |
|---|---|---|---|---|---|
| 2026-10-28 | first dev run | tool use: every rule of the card holds | 29 contracts, 87 runs | one run 0.74 [0.59–0.87]; majority of 3 0.72 [0.55–0.86]; every run 0.66 [0.48–0.83] | 19 contracts always pass, 5 sometimes, 5 never. Rules failing: under the 25-call cap 15 of 87, finished with an accepted report 8 of 87, a context lookup 3 of 84, no repeated call 3 of 87, a sell rehearsal 1 of 63 |
| 2026-10-28 | first dev run | detection: precision of the warnings | 17 scored contracts, 51 runs; 38 warned | 0.39 [0.14–0.67] (15 of 38) | the 15 right ones are all the flagged runs; the 23 wrong ones are benign contracts the agent warned on |
| 2026-10-28 | first dev run | detection: recall, F1, coverage | 5 flagged contracts, 15 runs | recall 1.00 [1.00–1.00] (15 of 15 warned); F1 0.57 [0.26–0.80]; coverage 0.94 | read recall as a count of 5 contracts. Benign runs: 23 warned, 10 cleared, 3 abstained |
| 2026-10-28 | first dev run | verdict as the card labels it | 17 contracts, 51 runs | one run 0.49 [0.25–0.73]; majority 0.47 [0.24–0.71]; every run 0.47 [0.24–0.71] | 8 contracts always right (the 5 flagged and `uniswap-pair`, `famous-weth`, `tok-f2b36ba4`), 1 sometimes (`tok-e2f2f46b`), 8 never |
| 2026-10-28 | first dev run | verdict flips between the 3 runs of a contract | 29 contracts | 4/29 = 0.14 | `permit2`, `tok-e2f2f46b`, `inc-etherfiatomicqueue`, `inc-termfinance`. The noise study (10 contracts x 5 runs) found 2 of 10 differing in any verdict |
| 2026-10-28 | first dev run | citation validity (the validator program, not a judge) | 87 runs, 1,004 claims | first submit accepted 0.72 [0.60–0.83]; claims valid 0.995 [0.990–0.999] | 6 runs submitted no report, 21 were accepted after a retry, 2 ended rejected. 5 of 1,004 claims failed the first time |
| 2026-10-28 | first dev run | faithfulness | 87 runs | not judged | no judge was named for this run-set |
| 2026-10-28 | first dev run | runs that ended without an accepted report | 8 of 87 | `tok-7427b616` and `inc-reflexergeb` no report in all 3 runs each; `inc-termfinance` report rejected twice | the cause is not looked at yet. They are `insufficient_evidence` verdicts: 3 abstains on benign, 5 on beyond-reach |
| 2026-10-28 | first dev run | hit the 25-tool-call cap | 15 of 87 runs | `tok-ff2f0388` 3, `inc-ensofinance` 3, `usdc` 2, `tok-f7755634` 2, `inc-usm` 2, `inc-termfinance` 2, `inc-notionalfinance` 1 | 14 of the 15 still ended with an accepted report; one `inc-termfinance` report was rejected |
| 2026-10-28 | first dev run | the sell rehearsal could not run | `inc-ensofinance`, 3 of 3 runs | `simulate_sell` returned "inconclusive, sandbox_failed" | the Foundry test reverts for this contract every time (deterministic, not a rate limit). Not scored on the verdict (beyond reach) |
| 2026-10-28 | first dev run | time, sequential, eval tier | 87 of 87 runs | median 40.3 s; 95th percentile 85.7 s [73.5–103.0]; max 111.9 s | 64 minutes for the whole run |
| 2026-10-28 | first dev run | cost | 87 runs | $0.6509 ($0.0075 a run, median $0.006, dearest $0.024) | estimate $0.487, cap $1.00. 17.8 tool calls a run, 73% of input tokens cached |
| 2026-10-28 | first dev run | the benign obscure tokens against their labels | 12 benign contracts, 36 runs | 7 contracts warned in all 3 runs (`famous-wbtc`, `tok-0c862c4a`, `tok-432a8e9d`, `tok-5c36660f`, `tok-d170c582`, `tok-ff2f0388`, `tok-f7755634`) | the labels say benign; the agent warns because it reads a bot-blacklist, a tax setter or an owner withdraw in the code. That is the agent and the script-computed label disagreeing, as in the noise study; no card was changed |

## Retrieval rows

| date | change | variant | split | n | recall@5 [95% CI] | MRR [95% CI] | p50 / p95 ms | notes |
|---|---|---|---|---|---|---|---|---|
| 2026-10-25 | baselines: random, keyword search (plain vs identifier-aware tokenizer), dense-only (bge-small) | random | dev | 69 | 0.18 [0.10–0.26] | 0.15 [0.09–0.21] | 0.0 / 0.0 |  |
| 2026-10-25 | baselines: random, keyword search (plain vs identifier-aware tokenizer), dense-only (bge-small) | bm25-plain | dev | 69 | 0.84 [0.77–0.91] | 0.80 [0.72–0.87] | 0.0 / 0.0 |  |
| 2026-10-25 | baselines: random, keyword search (plain vs identifier-aware tokenizer), dense-only (bge-small) | bm25 | dev | 69 | 0.86 [0.79–0.92] | 0.83 [0.75–0.90] | 0.0 / 0.0 |  |
| 2026-10-25 | baselines: random, keyword search (plain vs identifier-aware tokenizer), dense-only (bge-small) | dense-small | dev | 69 | 0.87 [0.80–0.93] | 0.81 [0.73–0.88] | 7.0 / 9.0 |  |
| 2026-10-25 | baselines: random, keyword search (plain vs identifier-aware tokenizer), dense-only (bge-small) | bm25-plain → bm25 | dev, paired difference | 69 | +0.01 [-0.03..+0.06] | | | recall@5 not shown; 2 better, 1 worse, 66 same; MRR +0.03 [-0.00..+0.07] |
| 2026-10-25 | held-out milestone 1 (dense-only baseline): same variants as the dev baselines | random | held-out | 31 | 0.05 [0.00–0.13] | 0.08 [0.05–0.12] | 0.0 / 0.0 |  |
| 2026-10-25 | held-out milestone 1 (dense-only baseline): same variants as the dev baselines | bm25-plain | held-out | 31 | 0.85 [0.74–0.95] | 0.88 [0.78–0.97] | 0.0 / 0.0 |  |
| 2026-10-25 | held-out milestone 1 (dense-only baseline): same variants as the dev baselines | bm25 | held-out | 31 | 0.90 [0.80–0.98] | 0.88 [0.77–0.97] | 0.0 / 0.0 |  |
| 2026-10-25 | held-out milestone 1 (dense-only baseline): same variants as the dev baselines | dense-small | held-out | 31 | 0.92 [0.83–0.99] | 0.87 [0.77–0.95] | 7.0 / 10.0 |  |
| 2026-10-25 | held-out milestone 1 (dense-only baseline): same variants as the dev baselines | bm25-plain → bm25 | held-out, paired difference | 31 | +0.05 [+0.00..+0.13] | | | recall@5 not shown; 2 better, 0 worse, 29 same; MRR -0.00 [-0.01..+0.01] |
| 2026-10-25 | hybrid: keyword + dense (bge-small) merged by reciprocal rank fusion, k 60 | hybrid-small | dev | 69 | 0.87 [0.80–0.93] | 0.85 [0.78–0.92] | 7.5 / 9.6 |  |
| 2026-10-25 | hybrid: keyword + dense (bge-small) merged by reciprocal rank fusion, k 60 | dense-small → hybrid-small | dev, paired difference | 69 | +0.00 [-0.04..+0.05] | | | recall@5 not shown; 3 better, 3 worse, 63 same; MRR +0.04 [-0.02..+0.09] |
| 2026-10-25 | hybrid: keyword + dense (bge-small) merged by reciprocal rank fusion, k 60 | bm25 → hybrid-small | dev, paired difference | 69 | +0.01 [-0.04..+0.07] | | | recall@5 not shown; 4 better, 2 worse, 63 same; MRR +0.02 [-0.04..+0.09] |
| 2026-10-25 | held-out milestone 2 (hybrid) | hybrid-small | held-out | 31 | 0.95 [0.88–1.00] | 0.92 [0.85–0.98] | 16.3 / 23.8 |  |
| 2026-10-25 | held-out milestone 2 (hybrid) | dense-small → hybrid-small | held-out, paired difference | 31 | +0.03 [-0.04..+0.11] | | | recall@5 not shown; 2 better, 1 worse, 28 same; MRR +0.06 [-0.04..+0.16] |
| 2026-10-25 | held-out milestone 2 (hybrid) | bm25 → hybrid-small | held-out, paired difference | 31 | +0.05 [+0.00..+0.13] | | | recall@5 not shown; 2 better, 0 worse, 29 same; MRR +0.04 [-0.03..+0.12] |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | rerank-small | dev | 69 | 0.88 [0.81–0.93] | 0.86 [0.80–0.92] | 2581.7 / 2791.5 |  |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | rerank-small-n10 | dev | 69 | 0.86 [0.78–0.92] | 0.85 [0.77–0.91] | 485.9 / 532.7 |  |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | rerank-small-n20 | dev | 69 | 0.86 [0.79–0.93] | 0.86 [0.79–0.92] | 994.2 / 1121.5 |  |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | hybrid-small → rerank-small | dev, paired difference | 69 | +0.01 [-0.06..+0.07] | | | recall@5 not shown; 4 better, 3 worse, 62 same; MRR +0.01 [-0.05..+0.08] |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | rerank-small → rerank-small-n10 | dev, paired difference | 69 | -0.02 [-0.07..+0.03] | | | recall@5 not shown; 1 better, 3 worse, 65 same; MRR -0.02 [-0.05..+0.00] |
| 2026-10-25 | reranker: MiniLM cross-encoder over the top 50 / 20 / 10 of the hybrid list | rerank-small → rerank-small-n20 | dev, paired difference | 69 | -0.01 [-0.04..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 68 same; MRR -0.00 [-0.01..+0.00] |
| 2026-10-25 | chunk text: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev | 69 | 0.87 [0.80–0.93] | 0.82 [0.75–0.89] | 13.7 / 31.2 | timings inflated: the big-model run was using the CPU at the same time |
| 2026-10-25 | chunk text: with code examples / title+summary+description only, against the default recipe | recipe-short | dev | 69 | 0.84 [0.75–0.91] | 0.81 [0.73–0.89] | 12.5 / 38.9 | timings inflated: the big-model run was using the CPU at the same time |
| 2026-10-25 | chunk text: with code examples / title+summary+description only, against the default recipe | hybrid-small → recipe-examples | dev, paired difference | 69 | +0.00 [-0.02..+0.02] | | | recall@5 not shown; 1 better, 1 worse, 67 same; MRR -0.03 [-0.07..+0.02] |
| 2026-10-25 | chunk text: with code examples / title+summary+description only, against the default recipe | hybrid-small → recipe-short | dev, paired difference | 69 | -0.04 [-0.09..+0.00] | | | recall@5 not shown; 0 better, 3 worse, 66 same; MRR -0.04 [-0.09..+0.01] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | dense-base | dev | 69 | 0.87 [0.80–0.93] | 0.84 [0.77–0.91] | 20.1 / 29.0 |  |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | hybrid-base | dev | 69 | 0.89 [0.82–0.94] | 0.89 [0.83–0.95] | 20.0 / 26.3 |  |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | rerank-bigreranker-n20 | dev | 69 | 0.87 [0.80–0.93] | 0.83 [0.76–0.90] | 5784.5 / 5957.3 |  |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | stack-base-n20 | dev | 69 | 0.86 [0.79–0.93] | 0.83 [0.75–0.90] | 5807.7 / 5961.0 |  |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | dense-small → hybrid-small | dev, paired difference | 69 | +0.00 [-0.04..+0.05] | | | recall@5 not shown; 3 better, 3 worse, 63 same; MRR +0.04 [-0.02..+0.09] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | dense-small → dense-base | dev, paired difference | 69 | +0.00 [-0.04..+0.05] | | | recall@5 not shown; 3 better, 3 worse, 63 same; MRR +0.03 [-0.03..+0.09] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | hybrid-small → hybrid-base | dev, paired difference | 69 | +0.01 [-0.01..+0.05] | | | recall@5 not shown; 2 better, 1 worse, 66 same; MRR +0.04 [-0.01..+0.10] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | rerank-small-n20 → rerank-bigreranker-n20 | dev, paired difference | 69 | +0.00 [-0.03..+0.04] | | | recall@5 not shown; 2 better, 2 worse, 65 same; MRR -0.03 [-0.08..+0.02] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | rerank-small-n20 → stack-base-n20 | dev, paired difference | 69 | +0.00 [-0.03..+0.04] | | | recall@5 not shown; 1 better, 2 worse, 66 same; MRR -0.03 [-0.09..+0.01] |
| 2026-10-25 | big vs small: bge-base embedder and bge-reranker-base (top 20) against the small models. Timings measured with no other job running | hybrid-base → stack-base-n20 | dev, paired difference | 69 | -0.02 [-0.06..+0.00] | | | recall@5 not shown; 0 better, 2 worse, 67 same; MRR -0.06 [-0.14..+0.01] |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 | dev | 69 | 0.86 [0.79–0.92] | 0.83 [0.75–0.90] | 0.1 / 0.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small | dev | 69 | 0.87 [0.80–0.93] | 0.81 [0.73–0.88] | 7.2 / 9.4 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small | dev | 69 | 0.87 [0.80–0.93] | 0.85 [0.78–0.92] | 6.9 / 8.8 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | rerank-small | dev | 69 | 0.88 [0.81–0.93] | 0.86 [0.80–0.92] | 2340.2 / 2511.4 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 | dev [style=code] | 9 | 0.72 [0.44–1.00] | 0.66 [0.40–0.91] | 0.1 / 0.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.79 [0.57–0.94] | 0.1 / 0.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 | dev [style=question] | 44 | 0.87 [0.78–0.94] | 0.85 [0.77–0.93] | 0.1 / 0.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 0.1 / 0.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small | dev [style=code] | 9 | 0.72 [0.44–1.00] | 0.63 [0.36–0.90] | 8.5 / 12.9 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small | dev [style=description] | 9 | 0.78 [0.56–1.00] | 0.61 [0.37–0.85] | 7.2 / 7.6 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.87 [0.78–0.95] | 7.2 / 8.4 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 5.1 / 6.2 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small | dev [style=code] | 9 | 0.72 [0.44–1.00] | 0.72 [0.44–0.93] | 8.2 / 12.7 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.68 [0.48–0.87] | 7.3 / 7.6 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small | dev [style=question] | 44 | 0.89 [0.80–0.97] | 0.89 [0.80–0.96] | 6.9 / 8.2 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 5.0 / 6.5 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | rerank-small | dev [style=code] | 9 | 0.67 [0.39–0.89] | 0.59 [0.35–0.84] | 2341.0 / 2481.5 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | rerank-small | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.89 [0.72–1.00] | 2337.8 / 2413.9 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | rerank-small | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.92 [0.85–0.97] | 2341.2 / 2533.1 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | rerank-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.86 [0.71–1.00] | 2339.6 / 2378.8 |  |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | dense-small → hybrid-small | dev, paired difference | 69 | +0.00 [-0.04..+0.05] | | | recall@5 not shown; 3 better, 3 worse, 63 same; MRR +0.04 [-0.02..+0.09] |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | bm25 → hybrid-small | dev, paired difference | 69 | +0.01 [-0.04..+0.07] | | | recall@5 not shown; 4 better, 2 worse, 63 same; MRR +0.02 [-0.04..+0.09] |
| 2026-10-25 | query styles: recall@5 by how the query is phrased (tool words, question, code, description) | hybrid-small → rerank-small | dev, paired difference | 69 | +0.01 [-0.06..+0.07] | | | recall@5 not shown; 4 better, 3 worse, 62 same; MRR +0.01 [-0.05..+0.08] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small | held-out | 31 | 0.91 [0.82–0.98] | 0.91 [0.81–0.98] | 2354.6 / 2469.9 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small-n10 | held-out | 31 | 0.90 [0.80–0.98] | 0.91 [0.82–0.98] | 462.2 / 500.1 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small-n20 | held-out | 31 | 0.91 [0.82–0.98] | 0.91 [0.81–0.98] | 925.0 / 958.1 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | dense-base | held-out | 31 | 0.95 [0.89–1.00] | 0.94 [0.87–0.98] | 20.0 / 28.3 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-base | held-out | 31 | 0.94 [0.87–0.99] | 0.96 [0.90–1.00] | 20.8 / 28.4 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-bigreranker-n20 | held-out | 31 | 0.85 [0.74–0.95] | 0.91 [0.82–0.98] | 5874.8 / 6281.6 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | stack-base-n20 | held-out | 31 | 0.85 [0.74–0.95] | 0.91 [0.82–0.98] | 5864.4 / 6281.8 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | recipe-examples | held-out | 31 | 0.95 [0.88–1.00] | 0.93 [0.86–0.98] | 6.9 / 9.5 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | recipe-short | held-out | 31 | 0.85 [0.74–0.95] | 0.91 [0.82–0.98] | 7.0 / 10.2 |  |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | dense-small → hybrid-small | held-out, paired difference | 31 | +0.03 [-0.04..+0.11] | | | recall@5 not shown; 2 better, 1 worse, 28 same; MRR +0.06 [-0.04..+0.16] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-small → rerank-small | held-out, paired difference | 31 | -0.04 [-0.12..+0.02] | | | recall@5 not shown; 1 better, 2 worse, 28 same; MRR -0.02 [-0.11..+0.07] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small → rerank-small-n10 | held-out, paired difference | 31 | -0.01 [-0.03..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 30 same; MRR +0.00 [+0.00..+0.01] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small → rerank-small-n20 | held-out, paired difference | 31 | +0.00 [+0.00..+0.00] | | | recall@5 not shown; 0 better, 0 worse, 31 same; MRR +0.00 [+0.00..+0.00] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | dense-small → dense-base | held-out, paired difference | 31 | +0.03 [+0.00..+0.10] | | | recall@5 not shown; 1 better, 0 worse, 30 same; MRR +0.07 [-0.03..+0.17] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-small → hybrid-base | held-out, paired difference | 31 | -0.01 [-0.03..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 30 same; MRR +0.04 [+0.00..+0.10] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small-n20 → rerank-bigreranker-n20 | held-out, paired difference | 31 | -0.05 [-0.13..+0.00] | | | recall@5 not shown; 0 better, 3 worse, 28 same; MRR +0.00 [-0.08..+0.09] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | rerank-small-n20 → stack-base-n20 | held-out, paired difference | 31 | -0.05 [-0.13..+0.00] | | | recall@5 not shown; 0 better, 3 worse, 28 same; MRR +0.01 [-0.07..+0.09] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-base → stack-base-n20 | held-out, paired difference | 31 | -0.08 [-0.18..+0.00] | | | recall@5 not shown; 0 better, 3 worse, 28 same; MRR -0.05 [-0.13..+0.02] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-small → recipe-examples | held-out, paired difference | 31 | +0.00 [+0.00..+0.00] | | | recall@5 not shown; 0 better, 0 worse, 31 same; MRR +0.01 [+0.00..+0.02] |
| 2026-10-25 | held-out milestone 3 (final): rerankers, big vs small, chunk text | hybrid-small → recipe-short | held-out, paired difference | 31 | -0.09 [-0.19..-0.01] | | | recall@5 shown; 0 better, 4 worse, 27 same; MRR -0.01 [-0.11..+0.08] |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 0.1 / 0.2 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.58 [0.16–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=paired] | 34 | 0.85 [0.75–0.94] | 0.79 [0.68–0.89] | 0.1 / 0.1 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=paraphrase] | 13 | 0.81 [0.62–0.96] | 0.81 [0.62–0.96] | 0.1 / 0.1 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | bm25 | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.92 [0.75–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.8 / 7.6 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=identifier] | 8 | 0.94 [0.81–1.00] | 0.90 [0.70–1.00] | 6.9 / 8.8 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=multi] | 4 | 0.62 [0.42–0.88] | 0.80 [0.40–1.00] | 7.5 / 7.8 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=paired] | 34 | 0.84 [0.72–0.94] | 0.73 [0.60–0.85] | 6.8 / 12.3 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=paraphrase] | 13 | 0.92 [0.77–1.00] | 0.89 [0.72–1.00] | 8.4 / 9.0 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | dense-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.89 [0.67–1.00] | 7.8 / 8.4 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.5 / 7.9 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.94 [0.81–1.00] | 7.3 / 8.9 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.77 [0.31–1.00] | 8.1 / 8.2 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=paired] | 34 | 0.87 [0.76–0.96] | 0.81 [0.70–0.90] | 7.1 / 11.4 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=paraphrase] | 13 | 0.85 [0.62–1.00] | 0.82 [0.61–1.00] | 8.4 / 8.7 |  |
| 2026-10-26 | by question kind (dev): recall@5 for keyword, dense and hybrid, the evidence behind choosing hybrid as the default | hybrid-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 7.3 / 8.7 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-qwen8b | dev | 69 | 0.92 [0.86–0.97] | 0.93 [0.88–0.97] | 416.3 / 5630.9 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-qwen8b | dev | 69 | 0.90 [0.84–0.95] | 0.92 [0.87–0.97] | 476.2 / 12612.6 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-openai-large | dev | 69 | 0.90 [0.84–0.95] | 0.89 [0.82–0.95] | 370.7 / 619.2 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-openai-large | dev | 69 | 0.87 [0.80–0.93] | 0.88 [0.81–0.94] | 374.0 / 531.9 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-gemini2 | dev | 69 | 0.89 [0.84–0.94] | 0.90 [0.85–0.95] | 521.6 / 638.7 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-gemini2 | dev | 69 | 0.89 [0.83–0.94] | 0.89 [0.83–0.95] | 524.4 / 736.9 |  |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-small → hybrid-small | dev, paired difference | 69 | +0.00 [-0.04..+0.05] | | | recall@5 not shown; 3 better, 3 worse, 63 same; MRR +0.04 [-0.02..+0.09] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-small → dense-qwen8b | dev, paired difference | 69 | +0.05 [-0.01..+0.12] | | | recall@5 not shown; 8 better, 3 worse, 58 same; MRR +0.12 [+0.06..+0.18] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-small → hybrid-qwen8b | dev, paired difference | 69 | +0.03 [-0.01..+0.07] | | | recall@5 not shown; 4 better, 1 worse, 64 same; MRR +0.07 [+0.03..+0.13] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-small → dense-openai-large | dev, paired difference | 69 | +0.03 [-0.00..+0.08] | | | recall@5 not shown; 5 better, 1 worse, 63 same; MRR +0.08 [+0.02..+0.15] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-small → hybrid-openai-large | dev, paired difference | 69 | -0.00 [-0.02..+0.01] | | | recall@5 not shown; 1 better, 1 worse, 67 same; MRR +0.03 [-0.02..+0.08] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | dense-small → dense-gemini2 | dev, paired difference | 69 | +0.02 [-0.03..+0.08] | | | recall@5 not shown; 5 better, 4 worse, 60 same; MRR +0.09 [+0.02..+0.16] |
| 2026-10-26 | hosted embedders via OpenRouter: Qwen3-Embedding-8B, text-embedding-3-large, gemini-embedding-2 against bge-small | hybrid-small → hybrid-gemini2 | dev, paired difference | 69 | +0.01 [-0.02..+0.06] | | | recall@5 not shown; 4 better, 3 worse, 62 same; MRR +0.04 [-0.01..+0.10] |
| 2026-10-26 | held-out milestone 4 (declared extra look): the dev winner, Qwen3-Embedding-8B via OpenRouter, dense and hybrid | dense-qwen8b | held-out | 31 | 0.97 [0.93–1.00] | 0.96 [0.90–1.00] | 469.8 / 8103.1 |  |
| 2026-10-26 | held-out milestone 4 (declared extra look): the dev winner, Qwen3-Embedding-8B via OpenRouter, dense and hybrid | hybrid-qwen8b | held-out | 31 | 0.96 [0.90–1.00] | 0.94 [0.87–1.00] | 762.6 / 5124.4 |  |
| 2026-10-26 | held-out milestone 4 (declared extra look): the dev winner, Qwen3-Embedding-8B via OpenRouter, dense and hybrid | hybrid-small → hybrid-qwen8b | held-out, paired difference | 31 | +0.01 [+0.00..+0.03] | | | recall@5 not shown; 1 better, 0 worse, 30 same; MRR +0.02 [-0.04..+0.08] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | bm25 | dev | 87 | 0.78 [0.71–0.85] | 0.77 [0.70–0.84] | 0.1 / 0.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-small | dev | 87 | 0.82 [0.75–0.89] | 0.77 [0.70–0.85] | 8.5 / 38.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-small | dev | 87 | 0.81 [0.74–0.88] | 0.81 [0.74–0.88] | 8.7 / 37.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | rerank-small | dev | 87 | 0.84 [0.77–0.90] | 0.81 [0.74–0.87] | 2993.1 / 4407.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-base | dev | 87 | 0.82 [0.75–0.89] | 0.79 [0.72–0.86] | 25.7 / 132.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-base | dev | 87 | 0.84 [0.76–0.90] | 0.84 [0.77–0.90] | 25.0 / 150.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-qwen8b | dev | 87 | 0.87 [0.81–0.93] | 0.89 [0.83–0.94] | 1667.1 / 6943.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-qwen8b | dev | 87 | 0.84 [0.78–0.90] | 0.87 [0.81–0.93] | 1786.6 / 5770.7 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-small → hybrid-small | dev, paired difference | 87 | -0.01 [-0.06..+0.03] | | | recall@5 not shown; 3 better, 4 worse, 80 same; MRR +0.04 [-0.01..+0.08] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | bm25 → hybrid-small | dev, paired difference | 87 | +0.03 [-0.02..+0.07] | | | recall@5 not shown; 6 better, 2 worse, 79 same; MRR +0.04 [-0.02..+0.10] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-small → rerank-small | dev, paired difference | 87 | +0.02 [-0.05..+0.09] | | | recall@5 not shown; 7 better, 5 worse, 75 same; MRR -0.00 [-0.07..+0.06] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-small → dense-base | dev, paired difference | 87 | +0.00 [-0.05..+0.05] | | | recall@5 not shown; 4 better, 4 worse, 79 same; MRR +0.02 [-0.03..+0.07] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-small → hybrid-base | dev, paired difference | 87 | +0.02 [-0.01..+0.06] | | | recall@5 not shown; 3 better, 1 worse, 83 same; MRR +0.03 [-0.02..+0.07] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | dense-small → dense-qwen8b | dev, paired difference | 87 | +0.05 [-0.01..+0.11] | | | recall@5 not shown; 9 better, 4 worse, 74 same; MRR +0.11 [+0.06..+0.17] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): totals | hybrid-small → hybrid-qwen8b | dev, paired difference | 87 | +0.03 [-0.00..+0.07] | | | recall@5 not shown; 5 better, 1 worse, 81 same; MRR +0.06 [+0.02..+0.11] |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [style=code] | 27 | 0.57 [0.41–0.74] | 0.60 [0.44–0.75] | 0.1 / 0.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.79 [0.57–0.94] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [style=question] | 44 | 0.87 [0.78–0.94] | 0.85 [0.77–0.93] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [style=code] | 27 | 0.67 [0.50–0.83] | 0.63 [0.47–0.78] | 16.5 / 36.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [style=description] | 9 | 0.78 [0.56–1.00] | 0.61 [0.37–0.85] | 7.5 / 8.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.87 [0.78–0.95] | 7.4 / 8.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 5.2 / 6.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [style=code] | 27 | 0.63 [0.46–0.80] | 0.68 [0.52–0.84] | 16.1 / 34.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.68 [0.48–0.87] | 7.6 / 8.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [style=question] | 44 | 0.89 [0.80–0.97] | 0.89 [0.80–0.96] | 7.2 / 8.6 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 5.2 / 6.6 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [style=code] | 27 | 0.67 [0.50–0.81] | 0.59 [0.44–0.74] | 2692.8 / 3048.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.89 [0.72–1.00] | 2705.1 / 3044.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.92 [0.85–0.97] | 2692.6 / 2969.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.86 [0.71–1.00] | 2625.9 / 2825.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [style=code] | 27 | 0.63 [0.46–0.80] | 0.60 [0.44–0.75] | 53.6 / 124.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.79 [0.57–1.00] | 22.3 / 22.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.88 [0.81–0.95] | 21.7 / 26.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 14.6 / 18.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [style=code] | 27 | 0.70 [0.54–0.85] | 0.73 [0.57–0.87] | 51.4 / 125.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.81 [0.61–1.00] | 22.4 / 34.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [style=question] | 44 | 0.89 [0.81–0.96] | 0.89 [0.81–0.96] | 21.0 / 26.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 14.6 / 18.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [style=code] | 27 | 0.76 [0.61–0.89] | 0.78 [0.64–0.91] | 1252.1 / 4700.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [style=description] | 9 | 0.78 [0.56–1.00] | 0.72 [0.50–0.94] | 1884.3 / 2845.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [style=question] | 44 | 0.95 [0.89–0.99] | 0.96 [0.91–1.00] | 2191.6 / 6624.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 2499.9 / 5891.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [style=code] | 27 | 0.70 [0.54–0.85] | 0.73 [0.58–0.88] | 3737.7 / 11516.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.81 [0.63–1.00] | 2974.7 / 8539.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [style=question] | 44 | 0.90 [0.83–0.96] | 0.95 [0.88–1.00] | 1693.8 / 5798.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [style=tool_words] | 7 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 3762.3 / 5792.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.58 [0.16–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=paired] | 34 | 0.85 [0.75–0.94] | 0.79 [0.68–0.89] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=paraphrase] | 13 | 0.81 [0.62–0.96] | 0.81 [0.62–0.96] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=snippet] | 10 | 0.80 [0.65–0.95] | 0.85 [0.70–1.00] | 0.2 / 0.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.92 [0.75–1.00] | 0.1 / 0.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | bm25 | dev [kind=unseen] | 8 | 0.12 [0.00–0.38] | 0.20 [0.06–0.44] | 0.2 / 0.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.5 / 8.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=identifier] | 8 | 0.94 [0.81–1.00] | 0.90 [0.70–1.00] | 7.1 / 8.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=multi] | 4 | 0.62 [0.42–0.88] | 0.80 [0.40–1.00] | 7.6 / 8.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=paired] | 34 | 0.84 [0.72–0.94] | 0.73 [0.60–0.85] | 6.7 / 11.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=paraphrase] | 13 | 0.92 [0.77–1.00] | 0.89 [0.72–1.00] | 8.3 / 8.7 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.85 [0.70–1.00] | 18.8 / 34.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.89 [0.67–1.00] | 7.6 / 7.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-small | dev [kind=unseen] | 8 | 0.38 [0.12–0.75] | 0.34 [0.09–0.64] | 29.3 / 47.9 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 7.1 / 7.7 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.94 [0.81–1.00] | 6.6 / 8.6 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.77 [0.31–1.00] | 7.8 / 8.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=paired] | 34 | 0.87 [0.76–0.96] | 0.81 [0.70–0.90] | 6.7 / 11.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=paraphrase] | 13 | 0.85 [0.62–1.00] | 0.82 [0.61–1.00] | 8.3 / 8.6 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.95 [0.85–1.00] | 18.5 / 33.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 7.4 / 7.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-small | dev [kind=unseen] | 8 | 0.25 [0.00–0.50] | 0.30 [0.06–0.55] | 28.0 / 47.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 524.9 / 1175.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.94 [0.81–1.00] | 2628.0 / 2785.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.79 [0.38–1.00] | 2708.8 / 2742.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=paired] | 34 | 0.84 [0.74–0.93] | 0.83 [0.73–0.93] | 2705.1 / 3044.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=paraphrase] | 13 | 0.96 [0.88–1.00] | 0.86 [0.71–1.00] | 2762.5 / 3191.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=snippet] | 10 | 0.80 [0.65–0.95] | 0.82 [0.65–1.00] | 2697.8 / 2749.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.92 [0.75–1.00] | 2768.2 / 2885.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | rerank-small | dev [kind=unseen] | 8 | 0.50 [0.12–0.88] | 0.30 [0.10–0.53] | 2647.3 / 2880.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 19.4 / 23.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=identifier] | 8 | 0.94 [0.81–1.00] | 0.94 [0.81–1.00] | 19.4 / 25.7 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=multi] | 4 | 0.67 [0.25–1.00] | 0.65 [0.30–1.00] | 22.1 / 23.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=paired] | 34 | 0.82 [0.71–0.93] | 0.80 [0.68–0.90] | 19.9 / 33.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=paraphrase] | 13 | 0.96 [0.88–1.00] | 0.91 [0.78–1.00] | 24.7 / 30.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.90 [0.75–1.00] | 63.1 / 110.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.83 [0.67–1.00] | 21.7 / 24.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-base | dev [kind=unseen] | 8 | 0.38 [0.12–0.75] | 0.25 [0.07–0.49] | 94.4 / 153.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 18.7 / 22.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 19.7 / 25.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.64 [0.29–1.00] | 22.7 / 23.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=paired] | 34 | 0.88 [0.79–0.96] | 0.88 [0.79–0.96] | 19.7 / 34.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=paraphrase] | 13 | 0.88 [0.69–1.00] | 0.89 [0.71–1.00] | 24.3 / 26.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.95 [0.85–1.00] | 60.2 / 108.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.92 [0.75–1.00] | 21.0 / 23.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-base | dev [kind=unseen] | 8 | 0.38 [0.12–0.75] | 0.25 [0.07–0.49] | 105.6 / 153.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 1353.2 / 3820.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 2115.9 / 2660.4 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.79 [0.38–1.00] | 1924.8 / 2482.7 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=paired] | 34 | 0.90 [0.81–0.97] | 0.90 [0.81–0.97] | 2032.2 / 4238.3 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=paraphrase] | 13 | 1.00 [1.00–1.00] | 0.92 [0.81–1.00] | 2444.2 / 6542.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=snippet] | 10 | 0.80 [0.65–0.95] | 0.95 [0.85–1.00] | 934.6 / 2727.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=twin] | 6 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 3412.7 / 7199.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | dense-qwen8b | dev [kind=unseen] | 8 | 0.50 [0.12–0.88] | 0.44 [0.17–0.77] | 975.7 / 4700.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 1206.2 / 2522.1 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 2388.2 / 5866.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=multi] | 4 | 0.62 [0.42–0.88] | 0.81 [0.44–1.00] | 1660.7 / 3376.2 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=paired] | 34 | 0.90 [0.81–0.97] | 0.90 [0.81–0.97] | 3011.3 / 6361.5 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=paraphrase] | 13 | 0.88 [0.69–1.00] | 0.93 [0.78–1.00] | 1349.2 / 4828.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 1.00 [1.00–1.00] | 1546.1 / 8702.0 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 1482.2 / 3772.8 |  |
| 2026-10-26 | bigger quiz (87 dev, 25 code fragments added 2026-10-26): by style and kind | hybrid-qwen8b | dev [kind=unseen] | 8 | 0.38 [0.12–0.75] | 0.27 [0.09–0.50] | 8474.5 / 21352.3 |  |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → hybrid-small | dev [style=code], paired difference | 27 | -0.04 [-0.11..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 26 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | bm25 → hybrid-small | dev [style=code], paired difference | 27 | +0.06 [-0.07..+0.19] | | | recall@5 not shown; 3 better, 1 worse, 23 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → rerank-small | dev [style=code], paired difference | 27 | +0.04 [-0.17..+0.24] | | | recall@5 not shown; 5 better, 4 worse, 18 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-base | dev [style=code], paired difference | 27 | +0.07 [+0.00..+0.19] | | | recall@5 not shown; 2 better, 0 worse, 25 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → dense-qwen8b | dev [style=code], paired difference | 27 | +0.09 [-0.02..+0.22] | | | recall@5 not shown; 4 better, 1 worse, 22 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-qwen8b | dev [style=code], paired difference | 27 | +0.07 [+0.00..+0.19] | | | recall@5 not shown; 2 better, 0 worse, 25 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → hybrid-small | dev [kind=snippet], paired difference | 10 | +0.00 [+0.00..+0.00] | | | recall@5 not shown; 0 better, 0 worse, 10 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | bm25 → hybrid-small | dev [kind=snippet], paired difference | 10 | +0.05 [+0.00..+0.15] | | | recall@5 not shown; 1 better, 0 worse, 9 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → rerank-small | dev [kind=snippet], paired difference | 10 | -0.05 [-0.15..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 9 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-base | dev [kind=snippet], paired difference | 10 | +0.00 [+0.00..+0.00] | | | recall@5 not shown; 0 better, 0 worse, 10 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → dense-qwen8b | dev [kind=snippet], paired difference | 10 | -0.05 [-0.15..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 9 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-qwen8b | dev [kind=snippet], paired difference | 10 | +0.00 [+0.00..+0.00] | | | recall@5 not shown; 0 better, 0 worse, 10 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → hybrid-small | dev [kind=unseen], paired difference | 8 | -0.12 [-0.38..+0.00] | | | recall@5 not shown; 0 better, 1 worse, 7 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | bm25 → hybrid-small | dev [kind=unseen], paired difference | 8 | +0.12 [+0.00..+0.38] | | | recall@5 not shown; 1 better, 0 worse, 7 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → rerank-small | dev [kind=unseen], paired difference | 8 | +0.25 [-0.25..+0.62] | | | recall@5 not shown; 3 better, 1 worse, 4 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-base | dev [kind=unseen], paired difference | 8 | +0.12 [+0.00..+0.38] | | | recall@5 not shown; 1 better, 0 worse, 7 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | dense-small → dense-qwen8b | dev [kind=unseen], paired difference | 8 | +0.12 [+0.00..+0.38] | | | recall@5 not shown; 1 better, 0 worse, 7 same; recall only |
| 2026-10-26 | bigger quiz: paired differences on the code questions | hybrid-small → hybrid-qwen8b | dev [kind=unseen], paired difference | 8 | +0.12 [+0.00..+0.38] | | | recall@5 not shown; 1 better, 0 worse, 7 same; recall only |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev | 87 | 0.81 [0.74–0.88] | 0.81 [0.74–0.88] | 8.0 / 33.1 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev | 87 | 0.84 [0.77–0.90] | 0.78 [0.71–0.85] | 7.8 / 33.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev | 87 | 0.78 [0.70–0.86] | 0.78 [0.70–0.85] | 7.7 / 31.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small → recipe-examples | dev, paired difference | 87 | +0.03 [-0.01..+0.07] | | | recall@5 not shown; 4 better, 1 worse, 82 same; MRR -0.03 [-0.07..+0.01] |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small → recipe-short | dev, paired difference | 87 | -0.03 [-0.07..+0.00] | | | recall@5 not shown; 0 better, 3 worse, 84 same; MRR -0.04 [-0.08..+0.00] |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [style=code] | 27 | 0.63 [0.46–0.80] | 0.68 [0.52–0.84] | 17.0 / 35.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.68 [0.48–0.87] | 7.9 / 17.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [style=question] | 44 | 0.89 [0.80–0.97] | 0.89 [0.80–0.96] | 7.4 / 8.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 6.0 / 6.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [style=code] | 27 | 0.74 [0.59–0.87] | 0.63 [0.49–0.77] | 16.4 / 34.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [style=description] | 9 | 0.89 [0.72–1.00] | 0.62 [0.44–0.81] | 7.9 / 8.8 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [style=question] | 44 | 0.88 [0.79–0.95] | 0.89 [0.80–0.96] | 7.3 / 8.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 5.4 / 8.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [style=code] | 27 | 0.59 [0.41–0.76] | 0.64 [0.47–0.79] | 16.1 / 35.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [style=description] | 9 | 0.72 [0.44–1.00] | 0.51 [0.32–0.72] | 7.6 / 8.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [style=question] | 44 | 0.89 [0.80–0.97] | 0.89 [0.80–0.97] | 7.3 / 9.3 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 5.4 / 6.8 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.5 / 8.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.94 [0.81–1.00] | 7.2 / 10.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.77 [0.31–1.00] | 7.6 / 7.7 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=paired] | 34 | 0.87 [0.76–0.96] | 0.81 [0.70–0.90] | 7.2 / 13.5 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=paraphrase] | 13 | 0.85 [0.62–1.00] | 0.82 [0.61–1.00] | 8.2 / 8.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.95 [0.85–1.00] | 20.0 / 33.8 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 7.6 / 7.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | hybrid-small | dev [kind=unseen] | 8 | 0.25 [0.00–0.50] | 0.30 [0.06–0.55] | 28.5 / 48.5 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.6 / 7.5 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.94 [0.81–1.00] | 7.3 / 8.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.79 [0.36–1.00] | 7.7 / 7.8 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=paired] | 34 | 0.87 [0.78–0.94] | 0.75 [0.64–0.85] | 6.9 / 11.0 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=paraphrase] | 13 | 0.85 [0.62–1.00] | 0.82 [0.61–1.00] | 8.5 / 8.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=snippet] | 10 | 0.90 [0.75–1.00] | 0.90 [0.75–1.00] | 18.6 / 33.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 7.3 / 7.8 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-examples | dev [kind=unseen] | 8 | 0.50 [0.12–0.88] | 0.30 [0.16–0.52] | 27.9 / 48.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.5 / 9.3 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=identifier] | 8 | 1.00 [1.00–1.00] | 0.90 [0.70–1.00] | 6.7 / 8.9 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=multi] | 4 | 0.54 [0.17–0.88] | 0.64 [0.27–1.00] | 8.0 / 8.1 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=paired] | 34 | 0.79 [0.66–0.91] | 0.74 [0.62–0.85] | 6.8 / 10.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=paraphrase] | 13 | 0.85 [0.62–1.00] | 0.86 [0.64–1.00] | 7.9 / 9.4 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.95 [0.85–1.00] | 19.1 / 34.1 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 1.00 [1.00–1.00] | 7.2 / 8.6 |  |
| 2026-10-26 | chunk text on the bigger quiz: with code examples / title+summary+description only, against the default recipe | recipe-short | dev [kind=unseen] | 8 | 0.25 [0.00–0.50] | 0.24 [0.06–0.47] | 28.2 / 50.3 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small | dev | 87 | 0.82 [0.75–0.89] | 0.77 [0.70–0.85] | 8.1 / 32.9 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | rerank-small | dev | 87 | 0.84 [0.77–0.90] | 0.81 [0.74–0.87] | 2485.1 / 3327.5 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-base | dev | 87 | 0.82 [0.75–0.89] | 0.79 [0.72–0.86] | 21.3 / 93.7 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-qwen8b | dev | 87 | 0.87 [0.81–0.93] | 0.88 [0.82–0.93] | 2015.6 / 8838.0 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | recipe-examples | dev | 87 | 0.83 [0.76–0.89] | 0.75 [0.68–0.82] | 8.1 / 31.4 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | recipe-short | dev | 87 | 0.78 [0.70–0.85] | 0.77 [0.69–0.84] | 9.0 / 33.8 |  |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small → rerank-small | dev, paired difference | 87 | +0.01 [-0.05..+0.08] | | | recall@5 not shown; 8 better, 7 worse, 72 same; MRR +0.03 [-0.03..+0.10] |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small → dense-base | dev, paired difference | 87 | +0.00 [-0.05..+0.05] | | | recall@5 not shown; 4 better, 4 worse, 79 same; MRR +0.02 [-0.03..+0.07] |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small → dense-qwen8b | dev, paired difference | 87 | +0.05 [-0.01..+0.11] | | | recall@5 not shown; 9 better, 4 worse, 74 same; MRR +0.11 [+0.06..+0.16] |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small → recipe-examples | dev, paired difference | 87 | +0.01 [-0.03..+0.04] | | | recall@5 not shown; 2 better, 1 worse, 84 same; MRR -0.02 [-0.05..+0.00] |
| 2026-10-26 | keyword search removed: meaning-only variants on the 87 dev questions (rerank now over the dense list, recipes now dense) | dense-small → recipe-short | dev, paired difference | 87 | -0.04 [-0.09..+0.00] | | | recall@5 not shown; 1 better, 7 worse, 79 same; MRR -0.01 [-0.06..+0.04] |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [style=code] | 27 | 0.69 [0.52–0.83] | 0.59 [0.44–0.74] | 17.6 / 36.0 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [style=description] | 9 | 0.78 [0.56–1.00] | 0.61 [0.37–0.85] | 7.3 / 9.5 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [style=question] | 44 | 0.91 [0.84–0.97] | 0.85 [0.77–0.93] | 7.3 / 8.7 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 0.93 [0.79–1.00] | 5.8 / 7.0 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [style=code] | 27 | 0.61 [0.44–0.78] | 0.59 [0.42–0.74] | 15.6 / 37.9 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [style=description] | 9 | 0.72 [0.44–1.00] | 0.47 [0.29–0.69] | 7.2 / 7.7 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [style=question] | 44 | 0.87 [0.78–0.95] | 0.90 [0.82–0.97] | 6.9 / 8.3 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [style=tool_words] | 7 | 0.93 [0.79–1.00] | 1.00 [1.00–1.00] | 4.9 / 6.2 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.3 / 7.3 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=identifier] | 8 | 0.94 [0.81–1.00] | 0.90 [0.70–1.00] | 6.7 / 8.4 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=multi] | 4 | 0.62 [0.42–0.88] | 0.83 [0.50–1.00] | 7.4 / 7.6 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=paired] | 34 | 0.85 [0.75–0.94] | 0.71 [0.59–0.83] | 7.3 / 10.7 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=paraphrase] | 13 | 0.92 [0.77–1.00] | 0.84 [0.66–1.00] | 8.1 / 8.7 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=snippet] | 10 | 0.85 [0.70–1.00] | 0.83 [0.67–1.00] | 18.6 / 36.0 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.83 [0.67–1.00] | 7.3 / 8.1 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-examples | dev [kind=unseen] | 8 | 0.38 [0.12–0.75] | 0.30 [0.11–0.53] | 29.9 / 52.1 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=filtered] | 4 | 1.00 [1.00–1.00] | 1.00 [1.00–1.00] | 6.0 / 7.2 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=identifier] | 8 | 0.81 [0.56–1.00] | 0.88 [0.65–1.00] | 6.8 / 8.4 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=multi] | 4 | 0.46 [0.17–0.67] | 0.77 [0.31–1.00] | 7.4 / 7.6 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=paired] | 34 | 0.82 [0.71–0.93] | 0.71 [0.59–0.83] | 6.5 / 10.4 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=paraphrase] | 13 | 0.92 [0.77–1.00] | 0.89 [0.72–1.00] | 8.1 / 8.3 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=snippet] | 10 | 0.80 [0.65–0.95] | 0.95 [0.85–1.00] | 22.6 / 37.9 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=twin] | 6 | 0.92 [0.75–1.00] | 0.92 [0.75–1.00] | 7.0 / 7.7 |  |
| 2026-10-26 | keyword search removed: chunk-text variants by style and kind (dense) | recipe-short | dev [kind=unseen] | 8 | 0.25 [0.00–0.50] | 0.22 [0.03–0.47] | 27.3 / 47.1 |  |

## Source review: plain model against our pipeline

| date | change | model | arms | n scored | recall P / A (planted) | false alarm P / A (safe twins) | cost P / A | notes |
|---|---|---|---|---|---|---|---|---|
| 2026-10-29 | plain Opus 5.5 vs our source review (same model, stripped view + checklist + span checks, no second pass) | anthropic/claude-opus-5.5 | P, A | 46 of 47 | 12/12 vs 12/12 | 7/12 vs 8/12 | $1.33 / $3.16 | DRAFT labels (unreviewed), one run each at temperature 0; incidents may be memorised; full report evals/source_review/results/compare-2026-10-29.md |
