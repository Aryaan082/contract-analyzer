# evals/: measuring the system

The retrieval layer is run by `evals.run` (below). The agent layers (tool use, detection, answer
quality, time and cost) are scored offline from stored agent traces by `evals.score` (section
"Scoring agent runs"); generating the runs (`make eval-run`) and the judge come in later parts.

```text
make eval                                                    dev questions, core variants, small models
python -m evals.run retrieval --variants all                 every variant (needs `make models-all`)
python -m evals.run retrieval --variants rerank-small --by style kind    recall@5 per query style and kind
python -m evals.run retrieval --variants rerank-small --also dense-small --log "change"
python -m evals.run retrieval --held-out --variants ...      held-out totals; milestones only
python -m evals.run retrieval --variants dense-small --save-baseline
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
  Held-out rows are marked. Held-out is run at milestones (declared in results.md), never to choose between variants, and `--by` is refused on it.
- **The baseline check** (`baseline_retrieval.json`, `--check-baseline`) is a regression gate, not
  a target: it fails only when the saved variant (`dense-small`, the local stand-in for the default mode) is confidently
  worse on the same questions.
- Nobody edits chunk text or labels in response to a quiz result. A label fix
  is logged as its own row and applied to every variant.

## Scoring agent runs (`python -m evals.score`)

No model, no network, no money: it reads agent traces (`agent/runs/*.json`, or a run-set folder).

```text
python -m evals.score agent/runs                                   label-free metrics for every trace
python -m evals.score runs/ --cards evals/datasets/contracts.toml --split dev
python -m evals.score runs/ --model deepseek/deepseek-v4.1-flash-1017   one model (several pooled is only a survey)
python -m evals.score runs/ --cards ... --write                    also save evals/results/<date>-<commit>-<label>.json
python -m evals.score runs/ --cards ... --held-out --declare "why"  held-out: totals only, a declared look
```

| Module | Does |
|---|---|
| `runs.py` | reads a trace (old or new) into a `RunRecord`: tools in order, arguments, seconds, attempts |
| `cards.py` | the labels: one `[[contract]]` per test contract (class, reasons, split, acceptable verdicts); `reviewed_by` empty until a person has read it |
| `trajectory.py` | tool-use rules: hygiene for every run, plus rules from the card's traits and reasons; per-rule failure counts |
| `detection.py` | verdict to warn/clear/abstain; precision, recall, F1 (strict: an abstain on a flagged contract is a miss), coverage, recall by reason; grey cards judged on their not-checked list |
| `repeats.py` | single-run, majority and all-correct rates over repeats; verdict flips; the cluster bootstrap (resamples contracts, not runs) |
| `citations.py` | citation validity on the FIRST `submit_report` (the final report is valid by construction); older traces are rebuilt with the validator |
| `timing.py` | median, p95 (only from 40 runs), max and n, per model; leaves out runs without timing, run alongside others, or slept through; cost per report |
| `results_file.py` | the versioned results document (`schema_version`, manifest, every layer) |

Rules that matter when reading the numbers:
- Cards are computed by script and unreviewed for now; the manifest and the printed report say
  "N of M labels unreviewed". The numbers mean "agrees with the script and the written rules", not
  "agrees with Aryaan".
- A claim is only valid here if its evidence exists and is of the right kind. Whether the evidence
  says what the claim says (faithfulness) is the judge's job, not done yet (`faithfulness: null`).
- `honeypot-sell-blocked` recall is decided by the sell rehearsal and the validator's floor rather than
  by the model's judgement; read it as such.
- Time: never one number, never mixed models, never from runs that shared the machine or slept.
  Timing exists only in traces written since the instrumentation (`agent/trace.py`).

## Collecting the detection set (`python -m evals.collect`)

Part C of Component 6: the labelled contracts the agent layers are scored against. Read-only (node
reads and Etherscan log reads), no model call, no money. The rules are in
`datasets/label_rules.toml` (hashed in `label_rules.sha256`); the files it writes and what a label
means are in `datasets/README.md`.

```text
python -m evals.collect scan --env /path/to/.env      discover, inspect and measure candidate tokens (resumable; cache in evals/collect/cache/, git-ignored)
python -m evals.collect verify --env /path/to/.env    identify_contract on the grey, famous and incident contracts: traits, implementation, code family
python -m evals.collect build                         label, pick one token per code family, split, write contracts*.toml, disputed.toml, collection_log.md
```

| Module | Does |
|---|---|
| `rules.py` | reads the numbers from `label_rules.toml`; the router and event lists the rules point at |
| `chaindata.py` | the small interface to the chain (Etherscan logs, node code and supply); scripted in tests |
| `scan.py` | `discover`, `inspect`, `measure` (counts, no labels) and `decide` (counts to label under the rules) |
| `existing.py` | the eight existing cards (from the smoke-test note's Part 1), the extra grey and famous contracts, the 13 disputed tokens |
| `split.py` | the split rule and the tool-use set |
| `build.py`, `log.py`, `tomlout.py` | assembling, the counts-only log, writing TOML safely |

`build` refuses to run if `label_rules.toml` no longer matches its recorded hash. To change a rule:
edit the file, say why in its version history, replace the hash on purpose, rebuild, and log the
change in `results.md`.
