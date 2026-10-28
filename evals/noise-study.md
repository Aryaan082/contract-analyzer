# The noise study (Component 6, part F, step 1)

**Status: registered, not run.** This note and `evals/datasets/noise_study.toml` are committed before
any run. `make eval-noise --go` refuses to start while either file has uncommitted changes, and the
run-set records the commit that registered them and a hash of both files, so a rule changed after
the fact shows.

## The question

Every number the harness reports comes from an agent that is not repeatable to the letter, even at
temperature 0. The only measurement of that is one anecdote: Seaport at the same block, with the
same tools, once gave `insufficient_evidence` and once `elevated_risk` (agent/README.md; the traces
of that flip were not kept). We have no measured flip rate. So before any other paid run:

1. How often does the **verdict** change between repeats of one contract?
2. How often does the **set of tools** the agent calls change?
3. When it changes, is the **model** the cause, or did the **tools return something different**?

The answer sets R, the number of repeats per contract, instead of guessing it (the draft was R=3).

## The ten contracts: a rule, not a choice

Written down and fixed before any run; applied once by `evals/noise_select.py`; recomputed by a test
and by `make eval-noise` (a list that is not what the rule gives is refused).

1. **Pool:** the 20 dev cards in the tool-use set, plus the cards named in the study file. Seaport is
   named: it is the one contract with an observed flip, which is why the study exists. It is not
   drawn, and it is not in the tool-use set. No held-out card can be in the pool.
2. **Quotas, 10 in all:** 3 flagged, 3 benign, 2 grey, 2 beyond-reach. The two scored classes get
   six of the ten because the verdict is what they are scored on.
3. **Trajectory coverage:** each trait that decides what a good run must call (proxy, official,
   not a token, token) is covered once, by the best-ranked card with it from a class that still has
   room.
4. **Fill:** each class is filled to its quota, preferring a trait mix not yet chosen in that class.
5. **Rank:** "best-ranked" is the smallest SHA-256 of `noise-study-v1:<card id>`. The salt was fixed
   once. Nothing about how any contract behaved was looked at, and the rule was not re-run to get a
   different list.

The result (also in `noise_study.toml`):

| contract | class | traits | pinned block |
|---|---|---|---|
| uniswap-pair | benign | official | 26280500 |
| tok-f7755634 | benign | token | 25460015 |
| tok-e2f2f46b | benign | token | 25870238 |
| milka | flagged | token | 26280500 |
| groklet | flagged | token | 26280500 |
| meowe | flagged | token | 26280500 |
| seaport | grey | not a token | 26280500 |
| steth | grey | not a token | 26280500 |
| inc-etherfiatomicqueue | beyond reach | not a token | 25952623 |
| inc-termfinance | beyond reach | proxy, not a token | 25816048 |

Seen from outside, the list has no famous-token benign contract, no `asasar` or `brick`, and one
proxy. That is what the rule gave; a different salt would give another list, and ten contracts are a
sample either way (see "What this cannot show").

## How it runs

`python -m evals.run noise` (`make eval-noise`): the estimate, nothing spent; with `--go` and a
`--max-spend` that covers the estimate, 10 contracts x 5 repeats = 50 runs on the eval tier, **one at
a time** (no `--workers`), repeat by repeat (every contract once, then again), so a run-set stopped
early still has whole repeats and the seconds of each run are usable. The contracts and the repeats
cannot be changed on the command line; the study file sets them. It is a run-set of its own kind,
`noise`: `make eval` never picks it up as the newest dev run-set and refuses it if named.

Each run is tagged `cache_state` from what the run itself counted: `warm` if every code-store and
selector-name lookup was answered from the store, `cold` if none was, `mixed` otherwise, `no_store`
if nothing asked a store. (The code store is Postgres and survives between runs, so the first repeat
is not necessarily cold.)

`python -m evals.run noise-report` (`make eval-noise-report`) analyses the newest noise run-set
offline and saves `evals/results/<date>-<commit>-<run-set>.json`.

## What is measured

Runs of a contract are compared with each other only. n (runs, contracts, pairs) is printed beside
every figure.

- **Verdict flip, exact:** the contract's runs do not all have the same verdict. `insufficient_evidence`
  counts as a verdict here.
- **Verdict flip, direction:** among runs that did not abstain, some warn (`elevated_risk`,
  `high_risk`) and some do not (`no_red_flags_found`). This is the one that changes right/wrong for a
  flagged or benign contract.
- **Abstains** (`insufficient_evidence`) are counted apart: how many runs, in how many contracts,
  and why the run ended (`stopped_because`, with runs that hit the tool-call cap counted separately).
  They are neither a pass nor a fail.
- **Tool-set change:** the set of distinct tools called differs between runs of the contract; which
  tools come and go, and in how many runs.
- **Attribution,** for every pair of runs of one contract. A *call* is a tool and its arguments.
  Each run records a fingerprint of every result (without the evidence id).
  - `same_inputs`: the same calls with the same result fingerprints. A different verdict here is
    the model.
  - `calls_differ`: the model made different calls but every call both made returned the same
    fingerprint. The model chose differently from the same facts: the model.
  - `results_differ`: at least one call both runs made returned a different fingerprint. The tools
    gave a different answer to the same question: the tools (or what they read: a node, an
    explorer, the hosted embedding of a search).
  - `unknown`: a run has no fingerprints.
  The pairs with a different verdict are counted by these classes (pairs with an abstain apart),
  with the same classes for pairs with the same verdict as a base rate, and each tool's own rate of
  differing results for identical calls. The classes are set-based: they do not order the calls, so a
  tool difference after the model has already diverged counts only when both runs made that call.
  Free-text arguments (a knowledge search) are rarely identical across runs, so those tools are
  rarely compared. Pairs of one contract are not independent; the counts say how many contracts they
  come from.
- **Pass stability,** with the existing `repeats.py` machinery: for the scored contracts, the share
  of single runs that are right, the share where the majority is right, the share where every run
  is right; and the same for the tool-use rules.
- Intervals resample contracts, not runs (cluster bootstrap). For a share of contracts out of ten the
  bootstrap collapses to a point at 0 or 10, so the exact (Clopper-Pearson) 95% interval is printed
  beside it. Zero flips among ten contracts only rules out a flip rate above about 31%.

## Decision rule for R

Written before the run. The thresholds are judgement: they are in `noise_study.toml` and applied by
`evals/noise.py` (`decide_r`), which prints its answer under the figures as a **proposal for Aryaan to
approve**, never as a setting.

On the 17 scored dev contracts one contract is about 6 points of detection accuracy. A flip between
warning and clear is what moves a flagged or benign contract between right and wrong, so it is what
decides how much a repeat of the same run can move the headline. An exact-verdict difference
(elevated vs high) alone does not change right and wrong.

| what the study finds (10 contracts, 50 runs) | quick runs | milestone runs |
|---|---|---|
| **calm:** no contract flips between warning and clear, and at most 3 differ in any verdict | **R=1** | **R=3** (the default stays) |
| **moderate:** anything between calm and noisy | R=1 only to check that nothing broke; **R=3** to compare two changes | **R=3, then 2 more repeats for each contract that disagrees** (capped at 5) |
| **noisy:** 3 or more contracts flip between warning and clear, or 7 or more differ in any verdict | **R=3** | **R=5 for every contract**, no adaptive top-up |

Then three checks that can override the table:

- **Abstains:** more than 5 of the 50 runs abstain. Repeats do not fix an abstain caused by the
  25-call cap or a time limit, and an abstain is a wrong answer on a flagged or benign contract.
  Find the cause before choosing R, and report the abstain rate as a result of its own.
- **Tools:** at least half of the pairs with a different verdict have `results_differ`. The tools are
  the main source: make the volatile tool repeatable (record and replay its result, or pin its input)
  before spending on repeats. The table's R is what to use in the meantime.
- **A floor for milestones:** whatever the study finds, a milestone run keeps R at 3 or more. Ten
  contracts that never flip are a sample, and the 95% bound on that (31%) is too wide to
  go below a majority of three for a number that will be quoted.

Not decided by this study: R for Sonnet (a stronger model with its own noise; the same ten
contracts on Sonnet are 50 runs at about $3.3 and would be a separate decision), and the held-out
set, which is looked at once.

## What this cannot show

- Ten contracts out of the dev set, five runs each, on one model (the eval tier). A flip rate from
  ten contracts has a wide interval; the report prints it.
- It measures the agent's run-to-run noise, not whether the verdicts are right. Right and wrong come
  from the cards, which nobody has reviewed yet.
- A tool whose result changes between runs because a node or explorer changed (a hiccup, a refreshed
  Sourcify entry) shows up as `results_differ`; the study cannot say why it changed.
- The hosted embedding of a knowledge search is not bit-for-bit repeatable, which can change which
  records a search returns. Its results are rarely compared (the query text is the model's).
- A judge is not needed to answer the three questions. Judging the 50 reports (`--judge-mode quick`)
  would add whether the share of supported claims, and the presence of an unsupported or
  contradicted claim, varies between repeats of one contract. It is priced in the estimate and is a
  separate decision.

## Where it is registered

`evals/noise-study.md` (this note) and `evals/datasets/noise_study.toml` are one registration: the run-set's
manifest has `noise_study.registered_commit` (the commit that last changed them) and
`noise_study.files_sha256` (a hash of both). `make eval-noise-report` says if either differs from
what was registered.
