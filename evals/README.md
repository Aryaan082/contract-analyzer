# evals/: measuring the system

Only the retrieval layer exists so far (the spec's Component 6 adds tool use, detection and
answer quality as further entries in `evals.run.LAYERS`).

```text
make eval                                                    dev questions, core variants, small models
python -m evals.run retrieval --variants all                 every variant (needs `make models-all`)
python -m evals.run retrieval --variants rerank-small --by style kind    recall@5 per query style and kind
python -m evals.run retrieval --variants hybrid-small --also bm25,dense-small --log "change"
python -m evals.run retrieval --held-out --variants ...      held-out totals; milestones only
python -m evals.run retrieval --variants hybrid-small --save-baseline
python -m evals.run retrieval --check-baseline               what CI runs
```

- **The quiz** is in `datasets/` (125 questions, answer keys, a dev/held-out split; its README
  explains the fields and the rules). **Variants** are in `variants.py`: each is a complete
  configuration of the search, so a row names exactly what ran.
- **Metrics** (`metrics.py`): recall@5, MRR, and a bootstrap 95% interval over the questions.
  Variants are compared on the same questions (`CHAIN` in `variants.py`), reporting how many
  questions got better, worse or stayed the same. A difference whose interval includes zero is
  "not shown", and results.md says so.
- **Latency** is the time of one search call (query embedding and reranking included, model
  loading excluded). Do not run two evals at once if the timings matter: they share the CPU.
- **`--also a,b`** runs more variants for the paired comparisons without logging their rows again.
- **`results.md`** is the log: one row per variant per run, appended with `--log "<change>"`.
  Held-out rows are marked. Held-out is run at milestones (after the dense baseline, after hybrid,
  at the end), never to choose between variants, and `--by` is refused on it.
- **The baseline check** (`baseline_retrieval.json`, `--check-baseline`) is a regression gate, not
  a target: it fails only when the saved variant (`hybrid-small`, the default mode) is confidently
  worse on the same questions.
- Nobody edits chunk text, `source_keywords` or labels in response to a quiz result. A label fix
  is logged as its own row and applied to every variant.
