# evals/: measuring the system

Three commands run it all (section "`make eval`, `make eval-run`, `make eval-heldout`"). The retrieval
layer is run by `evals.run retrieval` (below). The agent layers (tool use, detection, answer quality,
time and cost) are scored offline from stored agent traces by `evals.score` (section "Scoring agent
runs") or, for a stored run-set, by `make eval`; the faithfulness judge is in `judge/` (section "The
faithfulness judge"); a run-set is made by `make eval-run`, which is paid and spends nothing without
`--go`.

```text
make eval                                                    retrieval on dev + the newest stored dev run-set, scored (free)
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
| `detection.py` | verdict to warn/clear/abstain; precision, recall, F1 (strict: an abstain on a flagged contract is a miss), coverage, recall by reason; reason recall (the share of a card's required facts the report states, next to the all-or-nothing check); grey cards judged on their not-checked list |
| `repeats.py` | single-run, majority and all-correct rates over repeats; verdict flips; the cluster bootstrap (resamples contracts, not runs) |
| `citations.py` | citation validity on the FIRST `submit_report` (the final report is valid by construction); older traces are rebuilt with the validator |
| `timing.py` | median, p95 (only from 40 runs), max and n, per model; leaves out runs without timing, run alongside others, or slept through; cost per report |
| `results_file.py` | the versioned results document (`schema_version`, manifest, every layer) |

Rules that matter when reading the numbers:
- Cards are computed by script and unreviewed for now; the manifest and the printed report say
  "N of M labels unreviewed". The numbers mean "agrees with the script and the written rules", not
  "agrees with Aryaan".
- A claim is only valid here if its evidence exists and is of the right kind. Whether the evidence
  says what the claim says (faithfulness) is the judge's job (`judge/`); `faithfulness` is `null`
  until judgements are brought in with `--judge-cache` and `--judge`.
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

## The faithfulness judge (`python -m evals.judge`)

Part D of Component 6. A judge model reads each claim with the evidence it cites and gives one label
(`supported`, `partly`, `not_supported`, `contradicted`); the summary is checked sentence by sentence.
Built and tested offline; the only paid calls so far are the stage-1 trial on its check set (2026-10-28, $0.22). Everything about it, including what
it is not (a model's opinion, not Aryaan's labels, not truth), the rubric, the check set and the
spend cap, is in [`judge/README.md`](judge/README.md).

```text
python -m evals.judge checkset estimate --judge z-ai/glm-5.3-flash     calls, tokens, cost; nothing sent
python -m evals.judge checkset run --judge SLUG --max-spend 0.1 --go   PAID, hard cap
python -m evals.judge checkset report --judge A --judge B              catch rate, false-alarm rate, agreement
python -m evals.judge run RUNS --judge A --judge B --max-spend 1 --go  judge agent traces (PAID)
python -m evals.score RUNS --judge-cache evals/judge/cache --judge A --judge B   count them into the results file
```

## `make eval`, `make eval-run`, `make eval-heldout`

Part E of Component 6. Each is `python -m evals.run <command>`; extra arguments go in `ARGS="..."`.

| Command | Does | Cost |
|---|---|---|
| `make eval` | retrieval on dev (local small models, as before) **and** the scoring of the newest stored dev run-set: tool use, detection, citation validity, cached judge results, time, cost. Prints the layer table and the full report, saves `evals/results/<date>-<commit>-<runset>.json`. With no run-set it prints "no run-set yet" for those layers and still runs retrieval. `--no-retrieval` skips retrieval; `--runset PATH` picks a run-set; `--write-summary` also writes the committed summary | $0 |
| `make eval-run` | makes a run-set: the agent on the dev contracts, R repeats (default 3), then a judge **only if named**. Bare, it prints the estimate and what to add, and does nothing else | paid, only with `--go` |
| `make eval-heldout DECLARE="why"` | milestones only. Held-out retrieval, and the newest held-out run-set scored, totals only. Refuses without `DECLARE`. `ARGS="--generate --go"` makes a held-out run-set first (cap $2) | $0, or paid with `--generate --go` |

What `make eval` prints with no run-set yet, today (nothing has been generated):

```text
layer           n  metric                                value [95% CI]
retrieval[...]  87 dev   recall@5 / MRR                  (from the quiz)
tool use        -  pass rate (single run / all-correct)  no run-set yet (make eval-run)
detection       -  precision / recall / F1 (abstains)    no run-set yet (make eval-run)
answer quality  -  citation validity / faithfulness      no run-set yet (make eval-run)
time            -  end-to-end p50 / p95 (s)              no run-set yet (make eval-run)
cost            -  $ per report (p50 / max)              no run-set yet (make eval-run)
```

**Spending is explicit.** A paid command (`generate`, `heldout --generate`) works out an estimate
first and prints it. It then does nothing unless all of these hold (`evals/spend.py`, tested):

1. `--go` was given. Without it only the estimate and what to add are printed (this is what a bare
   `make eval-run` does);
2. `--max-spend` covers the estimate. The default cap is $4 for a dev run and $2 for held-out;
3. a model dearer than $0.02 a run (Sonnet) also has `--allow-expensive`;
4. a judge only runs if one is asked for on purpose: `--judge-mode quick|milestone`, or `--judge SLUG`
   for an extra. There is no default. Aryaan approved the rubric as written on 2026-10-28
   (`approved = true` in `evals/judge/policy.toml`, rubric hash `faa679eb...`); run-sets made before
   that keep the "rubric draft" notice they were made with, and a changed rubric needs a new
   approval (see "Who judges" below).

While it runs, each agent run first reserves its ceiling (the per-run cap handed to the agent loop:
$0.05 for the eval tier, $0.25 for Sonnet) from a `SpendCap`, and does not start if that would pass
`--max-spend`; the judge uses the same cap. Estimates come from measured costs, each with its source in
`evals/spend.py`: eval tier $0.0056 a run (24 runs), Sonnet $0.065 (5 runs), Haiku $0.0049 (8 runs), the
judges' per-call costs from the stage 1 trial. An unknown model or judge has no estimate unless
`--per-run-usd` or `--judge-call-usd` is given. Every paid command appends to a git-ignored ledger
(`evals/runsets/ledger.jsonl`); `python -m evals.run ledger` sums it against the roughly $25 envelope. The
ledger starts empty: the smoke test ($1.64) and the judge trial ($0.2163) were spent before it existed.

**Who judges** (Aryaan's decision of 2026-10-28; `evals/judge/policy.toml`, read by
`evals/judge/policy.py`):

| `--judge-mode` | Judges | Use | Results are labelled |
|---|---|---|---|
| `quick` | `z-ai/glm-5.3-flash` | development runs | "single judge, not for quoting" |
| `milestone` | `z-ai/glm-5.3-flash` and `qwen/qwen3.7-flash` (two companies) | milestone runs, the held-out look, the transfer check, the final Sonnet run, any number that will be quoted | "two or more independent judges (different companies)" |

`--judge deepseek/deepseek-v4.1-flash-1017` may be added as an optional extra, but only for agents from
other companies. **A judge never grades an agent from its own company**: the vendor prefix of the
judge's slug is compared with the agent's, and the command refuses before it spends anything (and the
judge step, the judge command on traces, and the scoring each check it again). So DeepSeek cannot judge
the eval-tier agent, which is DeepSeek, and an Anthropic judge could never grade Sonnet. Only the judges
named in the policy can be used. The estimate prices the chosen mode from the measured cost of one pass
over the check set (glm $0.098, qwen $0.046, 103 calls each), as (claims in a report + 1) calls a report
per judge, and says so. The run-set manifest and the results file record the mode, the judge slugs, the
rubric hash, the label, and whether the rubric was approved **when the run-set was made** (approving it
later does not change a committed result). A two-judge run lists every disagreement:
`python -m evals.run disagreements --runset PATH [--out FILE]` shows each claim or summary sentence
the two judges labelled differently, with both reasons (dev run-sets only; held-out has totals only). The
results file keeps the ids and labels.

**A run-set** is `evals/runsets/<name>/` (git-ignored): `traces/*.json` and `manifest.json`. The manifest
has the git commit and dirty flag, the model tier and slug, hashes (settings file, system prompt, tool
schemas, the cards plus the label rules), the block pins (dev only), R, the judge slugs and rubric hash,
the spend (estimated, cap, actual) and the environment. Runs go repeat by repeat; `--workers N` runs
several at once and marks the run-set and its traces concurrent, which the timing statistics leave out.

**The summary.** For a milestone dev run, `make eval ARGS="--write-summary"` writes
`evals/summaries/<name>.json`: what the free scoring reads from each run (verdict, report text, tool
calls with arguments and seconds, attempts, cost, tokens) and the labels the judge gave, but not the
conversation or the evidence. Commit it with the results file it names. `python -m evals.run
check-summaries` scores every committed summary again and requires exactly the committed results file
(only the time of writing may differ); it is what CI runs. Until the first milestone summary is committed
there is nothing to compare and it says so.

**Held-out** is totals only, in every file: no per-contract table, no summary, and a manifest that names
no contract, address or block (only how many, and a hash). The `--cards` file must be all held-out cards
(the Makefile passes it; dev tooling never loads it otherwise), and `--declare` is required.

### The noise study (`make eval-noise`, `make eval-noise-report`)

How much does a contract's result change between runs of the same agent, and is the model or the
tools to blame? The study is registered before it is run: `evals/noise-study.md` (the question, the
ten contracts by a written rule, the definitions, the decision rule for R) and
`evals/datasets/noise_study.toml` (the list, recomputed from the cards by `evals/noise_select.py`; a
list the rule does not give is refused).

| Command | Does | Cost |
|---|---|---|
| `make eval-noise` | the estimate for 10 contracts x 5 repeats on the eval tier, one at a time; says whether the registration is committed. `ARGS="--go --max-spend X"` runs it (a cap must be named, and the registration files must be committed so the commit predates the run); afterwards it prints the analysis. `--judge-mode quick` adds a judge to the estimate (not needed for the three questions) | paid, only with `--go` |
| `make eval-noise-report` | the analysis of the newest noise run-set: flips (exact, and warn/clear), abstains apart, tool-set changes, the pairs of runs by cause, the decision rule applied. Saved to `evals/results/` | $0 |

The run-set is of its own kind, `noise`: `make eval` takes only dev run-sets, so a noise run-set can
never be mistaken for the newest dev one, and every other command refuses it by name. The contracts
and the repeats come from the study file and cannot be changed on the command line. Each run is tagged
`cache_state` (`cold`, `warm`, `mixed`, `no_store`) from what its own stores answered; the tag is put
on every run the live runner makes, so the timing statistics split cold from warm.

### CI

`.github/workflows-disabled/evals.yml` is the one parked workflow (it replaces the retrieval-only one);
nothing is in `.github/workflows/`. **CI would run only the free subset**: ruff, the offline tests
(dataset checks included), retrieval on dev with the local small model and the baseline gate, and the
re-scoring of committed summaries (timing and cost reported, never gated). It has no RPC, Etherscan or
OpenRouter key and makes no model call. So it does **not** run the agent: the spec's "runs in CI on every
PR" holds for retrieval and for scoring stored results, not for the agent layers. A change that makes
the agent worse shows only when someone makes a run-set by hand and commits its summary. The workflow's
header lists what must happen before it is switched on.
