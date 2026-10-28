# evals/judge: does the cited evidence say what the claim says?

The citation validator proves that every cited id exists and is of the right kind. It does not prove
the evidence says what the claim says. This folder is the second check: a judge model reads each claim
with the evidence it cites and gives one label. **Nothing here has been sent to any model yet.** The
build is offline; paid calls need `--go` and a `--max-spend` cap.

## What the judge is, and is not

- It is **a model's opinion** of whether the cited evidence supports a claim. It is not truth.
- It departs from the spec. The spec says the judge is "checked against Aryaan's own labels". For now
  it is not: nobody has labelled claims. It is checked instead against (1) faults we planted, (2)
  faults found by reading real reports, and (3) a second judge from another company. Agreement
  between two judges shows consistency, not correctness; two models can share a mistake. When
  Aryaan has time, 20 hand-labelled claims would add the check the spec asks for (19 of 20 agreeing
  has a 95% interval of about 76% to 99%: enough to reveal a bad judge, not to rank good ones).
- **The faults are ours.** The 39 planted faults were written by the session that built the judge, as
  small edits to real Sonnet claims. The 5 real claim faults and 4 real summaries were found by an
  earlier reader (the 2026-10-27 smoke-test session) by eye. A judge that catches them has shown it
  catches the kinds of fault we thought of, not the ones we did not.
- It sees only what a claim cites. It cannot tell that a claim should have cited something else.

## Files

| File | Does |
|---|---|
| `rubric.toml` | the instructions the judge is given, for a claim and for a summary. **Draft for Aryaan to approve.** |
| `rubric.sha256` | the recorded rubric hash; the tests fail if the rubric or a schema changes without it |
| `rubric.py` | loads the rubric, hashes it (file bytes + both JSON schemas), builds the two messages, fences untrusted text |
| `inputs.py` | what the judge is given for a claim or a summary, built from a run record |
| `parse.py` | strict reading of a reply: a label or a recorded refusal, never a guess |
| `client.py` | `JudgeClient` interface; `OpenRouterJudge` (urllib, temperature 0, no tools, JSON-schema output) |
| `cache.py` | one JSON line per judgement; key = (claim, evidence, kind, severity, judge, rubric hash, repeat) |
| `runner.py` | runs jobs with a hard spend cap, a cache, worker threads, an estimate |
| `faithfulness.py` | counts claim-level and report-level faithfulness from the cache (offline) |
| `compare.py` | catch and false-alarm rates, two-judge agreement, stability: plain counting |
| `checkset_spec.toml`, `checkset.py`, `checkset/` | the faults and sound claims used to check a judge |
| `judges.toml` | candidate judge slugs and the prices their runs are capped with |
| `cli.py` | `python -m evals.judge` |

## What the judge is given

For each claim: its text, severity and kind, and the text of every record it cites **as the agent saw
it** (the tool message, or the knowledge record's text), cut to 24,000 characters in all with the cut
shown. Not the verdict, not the other claims, not the summary, not the agent's reasoning.

The reply is one JSON object `{"label", "reason"}`: `supported`, `partly` (true but stronger or
broader than the evidence), `not_supported`, or `contradicted`, and a one-line reason that quotes
the evidence field used between backticks. Temperature 0, no tools, the reply asked for against a JSON
schema. The reply is read strictly (`parse.py`): anything that is not exactly that object, bar one
Markdown fence around it, is recorded as refused with a code and counted as unlabelled. Whether
each backticked quote really occurs in the claim or evidence is recorded too (`quotes_found`).

The summary is judged in one call per report: each numbered sentence must trace to a finding, a
checked item or a not-checked item. A sentence that asserts nothing about the contract (advice, a
general caution) is `supported`.

**Untrusted text.** The claim and the evidence are chain and retrieved strings. They enter the prompt
only inside blocks `<<<BEGIN name code>>>` ... `<<<END name code>>>`, where `code` is a hash of
everything untrusted in that message, so text inside cannot contain its own block's closing line;
control characters are replaced; the system message holds the rubric and nothing else; the rubric tells
the judge to ignore any instruction inside a block. This is tested with hostile evidence, but a model can
still be talked into an answer, and the strict parser only limits what such an answer can look like.

## Counting

- **Claim level** = the share of labelled claims that are `supported`.
- **Report level** = the share of reports with zero `not_supported` or `contradicted` claims. A report
  with a claim that has no label is *unknown*, not clean: it stays in the denominator.
- Worked example: 6 claims, 5 supported, 1 not: claim level 83%, and the report fails.
- Intervals resample contracts, not claims. Only accepted reports are counted.
- `partly` lowers the claim-level share and does not fail the report.

`python -m evals.score RUNS --judge-cache evals/judge/cache --judge SLUG [--judge SLUG2]` merges the
cached judgements into the results file: judge slugs, the rubric hash, per-judge counts and
intervals, the judge's cost, the two judges' agreement, and the limits above. Without those flags
`faithfulness` stays `null` and the report prints "not judged yet".

## The check set

`checkset_spec.toml` is the recipe; `python -m evals.judge checkset build --traces <folder>` turns it
into `checkset/` (committed, so nothing needs the traces again). It is built from **dev traces only**:
the 13 Sonnet runs and the smoke-test runs of the eight dev-only contracts. Held-out contracts are
never used.

| Part | What |
|---|---|
| Planted faults | 39 edits of real Sonnet claims: flipped fact, changed number, capability stretched into an ability ("has mint" to "owner can mint"), same claim with the wrong evidence cited, an outside fact added, two true facts put the wrong way round, an absence or a rehearsal claimed too strongly |
| Their twins | the unedited claims, as sound items |
| More sound claims | 13 real claims believed to follow from the evidence, read by the author |
| Real faults | 5 claims written by DeepSeek-pro and Qwen that an earlier reader found unsupported (Seaport as "the OpenSea marketplace protocol", selector `0x00000000` read as "the fallback", a context claim naming Circle) |
| Summaries | 16 summaries: real Sonnet summaries with one sentence inserted or changed, the originals, and 4 real summaries with the faults the earlier reader found (USDC "Circle's regulated fiat-backed stablecoin", MILKA "a reflection-style ERC-20", Seaport "OpenSea's NFT exchange") |

Every planted claim must pass the project's own citation validator, so each is a fault the validator
accepts. The build also checks that the sound claims' key facts really occur in the cited evidence
and that each planted fact does not.

**Definitions.** A fault is *caught* when the label is one of its `catch` labels (a flipped fact:
`not_supported` or `contradicted`; a stretched claim also `partly`). *Strictly caught* counts only
`not_supported` and `contradicted`. A *false alarm* is a sound item labelled `not_supported` or
`contradicted`; a sound item labelled `partly` is *downgraded*. Items without a label are in no
denominator and are listed apart. Intervals are Wilson 95%; with about 45 items they are wide.

**Choosing judges.** Run each candidate once on the check set (about $0.1 or less each, see
`estimate`), keep the two with the highest catch rate and a low false-alarm rate from two different
companies, neither the agent's vendor. Then run the winner a second time with `--repeat 1` to see how many
labels change. Changing the judge or the rubric restarts these checks.

## Commands

```text
python -m evals.judge rubric                                  the rubric hash, and whether it is the recorded one
python -m evals.judge checkset show                           what the check set holds
python -m evals.judge checkset estimate --judge SLUG          calls, tokens and cost; nothing sent
python -m evals.judge checkset run --judge SLUG --max-spend 0.1 --go      PAID
python -m evals.judge checkset report --judge A --judge B     catch, false alarm, agreement (offline)
python -m evals.judge checkset run --judge A --repeat 1 --max-spend 0.1 --go   the stability pass
python -m evals.judge run RUNS --judge SLUG --max-spend 1 --go            judge agent traces (PAID)
```

Without `--go` nothing is sent. With it, a judge needs `OPENROUTER_API_KEY` (read the way the agent
reads it, never printed). Before each call the runner works out the most it could cost (its size at 3
characters a token at the input price, plus `max_tokens` at the output price) and does not make the
call if the total so far plus that would pass `--max-spend`; what it counts afterwards is the cost
OpenRouter reports. A run that reaches the cap stops and says how many calls were left; everything
finished is in the cache. With several workers a call that cannot yet be reserved ends the run early,
which is conservative. Prices in `judges.toml` bound the worst case; a provider that charges more than its
listed price could exceed it.

## Not verified

- **No request has been sent**, so these are untested against the live API: that each candidate model
  accepts `response_format` with a JSON schema and `provider.require_parameters`; that thinking models
  leave room for the JSON inside `max_tokens`; what a call really costs. The first paid trial is the
  test. A model that refuses the request format can be set to `structured = "none"` in `judges.toml`.
- The prices in `judges.toml` are what was read on the date written there, not a guarantee.
- The sound claims and the sentences marked sound were read by one person (the author). Some may be
  arguable; `partly` on a sound claim is reported as a downgrade for that reason.
- The check set is small (about 45 claims of each kind, 16 summaries). Differences between judges of
  a few items are not shown.
