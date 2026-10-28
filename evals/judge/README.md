# evals/judge: does the cited evidence say what the claim says?

The citation validator proves that every cited id exists and is of the right kind. It does not prove
the evidence says what the claim says. This folder is the second check: a judge model reads each claim
with the evidence it cites and gives one label. The code and the check set are built and tested
offline; the only paid calls so far are the stage-1 trial on the check set (2026-10-28, $0.22 of an
approved $0.33) and the DeepSeek third-column test (the same day, $0.06 of its own $0.20), both below.
Paid calls need `--go` and a `--max-spend` cap.

## What the judge is, and is not

- It is **a model's opinion** of whether the cited evidence supports a claim. It is not truth.
- It departs from the spec. The spec says the judge is "checked against Aryaan's own labels". For now
  it is not: nobody has labelled claims. It is checked instead against (1) faults we planted, (2)
  faults found by reading real reports, and (3) a second judge from another company. Agreement
  between two judges shows consistency, not correctness; two models can share a mistake. When
  Aryaan has time, 20 hand-labelled claims would add the check the spec asks for (19 of 20 agreeing
  has a 95% interval of about 76% to 99%: enough to reveal a bad judge, not to rank good ones).
- **The faults are ours.** The 39 planted faults were written by the session that built the judge, as
  small edits to real Sonnet claims. The 5 real claim faults and 3 real summaries were found by an
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
| Summaries | 15 summaries: real Sonnet summaries with one sentence inserted or changed, the originals, and 3 real summaries with faults the earlier reader found (USDC "Circle's regulated fiat-backed stablecoin" twice, MILKA "a reflection-style ERC-20"). A fourth, Seaport's "OpenSea's NFT exchange", was removed after the first run: see the trial results |

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
companies, neither the agent's vendor (the one exception is the third-column test below, which does not
change the kept pair). Then run the winner a second time with `--repeat 1` to see how many
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

## Trial results (2026-10-28, stage 1)

Two judges from two companies, each asked once about the check set (102 calls: 87 claims and 15
summaries after the correction below), then the stability pass of the one with the higher catch rate.
Full numbers and the list of disagreements are in `trials/2026-10-28-stage1.json` and a table in
`../results.md`. Spend $0.2163 of the approved $0.33. Rubric draft `faa679eb…`, unapproved at the time
(Aryaan approved this exact rubric, hash unchanged, on 2026-10-28, after the trial).

| | `qwen/qwen3.7-flash` (Alibaba) | `z-ai/glm-5.3-flash` (Z.ai) |
|---|---|---|
| Claim faults caught | 37/44 = 0.84 [0.71–0.92] | 39/41 = 0.95 [0.84–0.99] |
| of those that must get a strict label (flip, number, citation, swap) | 16/22 | 17/19 |
| Sound claims called `not_supported` or `contradicted` | 0/46 | 1/46 |
| Sound claims called `partly` ("downgraded") | 11/46 = 0.24 | 10/46 = 0.22 |
| Summary-sentence faults caught / false alarms | 13/14 and 2/38 | 14/14 and 0/38 |
| Replies left unlabelled | 0 | 3 of 103 (cut off) |
| Cost of the check set | $0.046 | $0.098 |

- **Agreement.** The two judges give the same label on 110 of 139 items (0.79 [0.72–0.85]) and agree
  on "supported or not" on 124 (0.89); kappa 0.78. Agreement is consistency, not correctness.
- **Stability.** `glm-5.3-flash` asked twice about the same inputs changed 7 of 133 labels (5%), 4 of
  them between supported and not (3%).
- **Both judges are strict about hedged inferences.** About a quarter of the sound claims came back
  `partly`. They are inferences such as "no code at this address, so a single key". That lowers the
  claim-level share and does not fail a report; expect claim-level faithfulness to read low even for a
  good report.
- **Changed numbers are the hardest fault.** `qwen3.7-flash` caught 3 of 7 (an extra-function count, a
  block distance, decimals); `glm-5.3-flash` 5 of 6. Both missed `num-milka-block`.
- **Hidden thinking.** Both models think before they answer and bill it as output. At 2,500 tokens 27 of
  qwen's 103 replies (all 16 summaries among them) were used up by thinking and cut off, so qwen uses
  6,000 and none was refused after that. glm at 6,000 had 12 cut off; at 12,000, 3 still were. One of
  them was a loop of whitespace after an opening quote. Cut-off replies are recorded as refused and
  counted as unlabelled, never guessed, and are retried with `--retry-unparsed`.
- **Quotes.** The rubric asks for a backticked quote of the evidence field. `qwen3.7-flash` seldom
  does (19 of 205 reasons); `glm-5.3-flash` does in 118 of 202, and 15 of those are not found in what it
  was given. That is a hint of paraphrase or invention, not a finding.
- **A wrong answer key, corrected after the first look.** Both judges missed the same three sentences
  in one real summary (deepseek-v4-pro on Seaport: "OpenSea's NFT exchange", "the 0x00000000
  fallback", "consistent with Seaport's permissionless design"). Those phrases are repeated in that
  report's own findings, and the summary check asks whether the summary follows the findings, so the
  judges were right and the key was wrong. The item was removed from the summary check; the same
  claims are checked at claim level. Before the correction the summary catch was 13/17 and 14/17.
- **Sizes.** With 44 faults, differences under about 10 points are not shown, so "glm catches more"
  is a lean. Next, if wanted: a stage 2 with `xiaomi/mimo-v2.6-pro` ($0.12–0.33 a pass), the rubric
  tweak below, and a re-run on a larger fault set.

## Third column: `deepseek/deepseek-v4.1-flash-1017` (2026-10-28)

Aryaan asked for DeepSeek to be tried as a judge anyway, with its own hard cap of $0.20, one pass over
the check set and no stability pass. **It is the same company, and the same model, as the agent's eval
tier** (`agent/models.toml`), so it can share the agent's blind spots and favour its own wording. It is
therefore a comparison only: the kept pair stays Alibaba + Z.ai, and `judges.toml` marks the entry as an
exception to the "not DeepSeek" rule (the judge-list test names that one slug). Same rubric (`faa679eb…`,
untouched), same check set, temperature 0, `max_tokens` 6000. The record is
`trials/2026-10-28-deepseek.json`; qwen's and glm's labels were read from the stage 1 cache (copied, not
re-run) and match the stage 1 record.

| | `qwen/qwen3.7-flash` | `z-ai/glm-5.3-flash` | `deepseek/deepseek-v4.1-flash-1017` |
|---|---|---|---|
| Claim faults caught | 37/44 = 0.84 [0.71–0.92] | 39/41 = 0.95 [0.84–0.99] | 42/44 = 0.95 [0.85–0.99] |
| of those that must get a strict label (flip, number, citation, swap) | 16/22 | 17/19 | 20/22 |
| Sound claims called `not_supported` or `contradicted` | 0/46 | 1/46 | 0/46 [0.00–0.08] |
| Sound claims called `partly` ("downgraded") | 11/46 = 0.24 | 10/46 = 0.22 | 8/46 = 0.17 |
| Summary-sentence faults caught / false alarms | 13/14 and 2/38 | 14/14 and 0/38 | 14/14 and 0/38 |
| Replies left unlabelled | 0 | 3 of 103 (cut off) | 0 of 102 |
| Cost of the check set | $0.046 | $0.098 | $0.058 |

- **Spend and cap.** $0.0575 of the $0.20: a 23-call probe under a $0.02 cap ($0.0081), then the other
  79 calls ($0.0495). No reply was cut off, so there was no retry round. The caps were set so that the
  money already spent plus the next command's cap never passed $0.20. The runner's tally equals the sum of
  the 102 per-call costs in the cache ($0.057521); both are the cost OpenRouter reported per reply. The
  account balance was not read, so the bill itself is not checked. With stage 1 ($0.2163) the judge trial
  has cost $0.274, under the $0.50 ceiling.
- **Request and providers.** A strict JSON-schema request with `provider.require_parameters` was accepted
  on the first call (no fallback to `json_object`). DeepSeek's own endpoint lists `response_format` only,
  so OpenRouter routed to 15 other hosts (31 of 102 calls to Wafer), at fp4, fp8 and unstated precision.
  The entry in `judges.toml` is priced at the dearest host that takes a schema request (0.45 in, 1.80 out
  per million), which puts the worst case of the full pass at $1.23; that bound is why the cap, not the
  price, stops a run early. At the prices actually charged a call cost $0.00056 on average (median
  $0.00035, from $0.00009 to $0.0055).
- **Hidden thinking is light.** Median 235 completion tokens, of which the median is 155 thinking
  tokens; the longest reply used 5,189 of the 6,000, so 6,000 only just clears it (3 replies passed
  2,500).
- **Agreement.** With glm: the same label on 121 of 139 items (0.87 [0.80–0.92]), the same on "supported
  or not" on 133 (0.96 [0.91–0.98]), kappa 0.91. With qwen: 115 of 142 (0.81 [0.74–0.87]), 126 (0.89),
  kappa 0.77 (on the 139 items glm also labelled: 114 and 124, against 110 and 124 for qwen and glm).
  All three give the same label on 103 of 139 items and the same supported-or-not on 121; DeepSeek is the
  odd one out on 7 and 3. Agreement is consistency, not correctness. All three missed `num-milka-block`
  (a changed block distance); DeepSeek's other miss was `cite-pair-identical`, which qwen missed too.
- **Quotes.** 170 of its 202 reasons quote the evidence in backticks (glm 118, qwen 19), and 17 of those
  are not found in what it was given (glm 15 of 118): a hint of paraphrase, not a finding.
- **Self-preference cannot be tested here.** The check set is mostly Sonnet-written. Its only
  DeepSeek-written items are 3 claims and 2 summaries (4 scored sentences) from `deepseek-v4-pro` reports
  (the Pro model of the same company), all faults found by an earlier reader; there is no sound
  DeepSeek-written item and no way to compare like faults in Sonnet and DeepSeek wording. What the 7 units
  show: flash flagged all 7 (`partly` or worse; glm 7, qwen 5), so it did not wave its sibling's mistakes
  through; a 7-of-7 result has a 95% interval of about 0.65 to 1.0, so a moderate blind spot is not
  excluded. It gave a strict label (`not_supported` or `contradicted`) to 3 of 7 (glm 5, qwen 2), against
  10 of 10 on the other summary faults. Two of the gaps with glm are sentences #1 ("Circle's regulated
  fiat-backed stablecoin") and #2 ("a legitimate, widely-used token") of the same USDC summary, which flash
  called `partly` and glm `not_supported`; both are true of the real USDC but not stated in the evidence,
  so they are arguable. That could be a taste for DeepSeek's wording or two borderline sentences; at n = 7
  from two reports it cannot be told apart. A real test needs sound and faulty claims written by DeepSeek
  and by another model, judged by a judge from neither.
- **What it changes.** Nothing in the kept pair. On this set DeepSeek flash does as well as glm (not shown
  to differ: the intervals overlap), loses no replies and is a little cheaper, but a judge that shares
  the agent's family is the case the pair rule was written to avoid. A full dev run of 120 reports would
  cost about $0.48 (Sonnet reports, 7.1 calls) or $0.89 (flash reports, 13.1 calls) at $0.00056 a call:
  a projection from 102 calls, not a measured run, and the host that serves a call moves it.
- **Not checked.** A second pass (stability) was not run, as agreed. Whether the label depends on the
  host (fp4 against fp8) was not tried. The sound claims are still read by one person.

## What judging a run set would cost (projected from the trial)

Measured on 2026-10-28: `qwen3.7-flash` $0.00045 a call, `glm-5.3-flash` $0.00095 a call (money spent,
retries of cut-off replies included; the check set's claims cite about the same amount of evidence as real
ones, 3.8k characters on average against 3.8k to 4.2k). A report costs one call per claim plus one for the
summary. The reports in `agent/runs` average:

| Agent that wrote the reports | Claims a report | Calls a report | 120 reports (a full dev run) with qwen / glm / both |
|---|---|---|---|
| Sonnet 5.5 | 6.1 | 7.1 | about $0.38 / $0.81 / $1.19 |
| DeepSeek flash (the eval tier) | 12.1 | 13.1 | about $0.71 / $1.49 / $2.20 |

So the judge costs more than the eval-tier agent itself ($0.67 for the same 120 runs); with flash as the agent
and two judges a full dev run is about $2.9, under the $4 rule. One judge on every report, and the second
only on a sample, would roughly halve it. These are projections from 103 calls each, not a measured run;
cut-off replies, retries and a different mix of claims move them.

**Rubric change worth considering before approval** (it would give a new hash and restart these
checks, about $0.15 for the two judges): the reason asks for a quote that contains JSON quote marks
(`"verdict": "sell_blocked"`), which has to be escaped inside a JSON string. Asking for the quoted
words without quote marks would remove that.

## Verified and not verified

- **Verified by the trial:** `glm-5.3-flash` and `deepseek-v4.1-flash-1017` accept a strict JSON-schema
  request with `provider.require_parameters`; `qwen3.7-flash` is refused it (HTTP 404, no provider, nothing charged)
  and answers plain JSON (`structured = "json_object"`); the strict parser read every reply that was not
  cut off except one (not JSON, in the stability pass); the spend cap and the cost accounting work against the real API.
- **Not verified:** that `reasoning = "off"` works (never sent); whether the other two candidates in
  `judges.toml` (`xiaomi/mimo-v2.6-pro`, `minimax/minimax-m3`) accept the request; judging full agent
  runs (`python -m evals.judge run`), which is tested only with a scripted judge.
- Prices in `judges.toml` are what was read on the date written there. A cost the cache records for a
  call is the one OpenRouter reported; a cut-off reply that was retried costs twice, and only the later
  row stays in the cache, so the cache's own total is lower than the money spent (the table above
  uses the money spent).
- The sound claims and the sentences marked sound were read by one person (the author). Some may be
  arguable; `partly` on a sound claim is reported as a downgrade for that reason.
- The check set is small (44 claim faults, 46 sound claims, 15 summaries). Differences between judges of
  a few items are not shown.
