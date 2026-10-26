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
guards `hybrid-small`.

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
total +0.03 [-0.01..+0.07], not shown); it is the first thing to retest on a bigger code set. No
default was changed. Qwen's latency on these runs was 1.7 s median and 6.9 s at the 95th
percentile (slower than on the 25th); the 87 queries cost about $0.0001.

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
