# verify/ — checking a flag by running code

Built 2026-11-05 on Aryaan's direction. Status: **the offline pieces are built and tested; nothing
has been run against a model, and no compiler or library has been downloaded.** See
`CLAUDE.md` (Guardrails) for the one exception this package makes to "no exploit code".

A flag the reviewer raises is a sentence about a place. Here it is checked by running code: a model
writes a Foundry test that asserts the bad outcome and `forge test` runs it on a local EVM, offline.
Simulating every flag is too much, so a funnel decides which ones (`verify/funnel.py`):

| Step | What | Credit |
|---|---|---|
| 1 | the flag matches an auditor's finding (the judge panel's call) | `report` |
| 2 | a deterministic rule confirms the construct the flag names (`mechanical.py`) | `check` |
| 3 | a cheap call (DeepSeek flash) says whether the rest are testable logic bugs (`triage.py`) | |
| 4 | rank (both layers agreeing first) and cap per report (default 10), then simulate (`sandbox.py`, `poc.py`, `runner.py`) | `sandbox` |

Everything not simulated stays in the output as `unverified`. A failed attempt never calls a flag
false: `not_confirmed` means no working test in the attempts allowed.

## Safety

* The test is untrusted model output. Its text is checked first (`poc.check_test_text`): no file,
  environment, fork or network cheatcode, no `vm.store`/`vm.etch`/`vm.load`, no `stdstore`, no
  inline assembly, project imports only, an assertion that can fail.
* The run (`runner.py`): a throwaway folder outside the repo, `ffi = false`, no `fs_permissions`,
  `--offline`, `sandbox-exec` with the network denied and writes only in the folder, an
  environment with no API key, a timeout that kills the process group.
* A passing test is read by a model from another company than the writer's before the flag counts
  (`poc.review_prompt`); `verify_candidate` refuses the same company for both.
* The test text and the model's sentences stay out of the repo. What is kept is `funnel.record`:
  place, disposition, credit, and the test's hash.
* The compiler must already be installed (`~/.svm`); nothing here downloads one.

## The layered run (2026-11-06)

`review/layered.py` runs the plain reader, then the pipeline as a separate run, and joins their flags by
place; `verify/stage.py` triages and (when given a writer, a reviewer and a runner) simulates the places
and orders the output without the answer key; `evals/source_review/precision.py` scores that output
(recall at K, credited and unverified shares, so that writing more flags does not score more). Nothing in
the chain removes a place: it is additive over the plain reader. The rule for the first layered evaluation
is in `evals/datasets/tob_items/README.md`.

## Status after the Radius calibration (2026-11-06, DeepSeek flash writer, glm reviewer, $0.05 in all)

* Toolchain: the real sandboxed runner compiles, deploys and runs the audited Radius EVMAuth code with its
  pinned libraries (solc 0.8.20, london). Libraries are downloaded by hand into a cache folder, not by the runner.
* Recall on the 7 known findings of the report: 0 of 7 with the finding's title as the claim; 1 of 7 with the
  auditors' description and 5 attempts (the Low one: the batch blacklist functions skip checks). None of the 4
  High findings: the writer's tests ran the real contract and failed on their own setup, or the writer said a unit
  test cannot show it. The cheap writer does not build the multi-step state (expiring token groups) they need.
* The funnel on the 12 plain flags that match no auditor finding: 9 skipped by triage, 2 confirmed by a mechanical
  rule, 1 simulated and confirmed. That one is documented design (the function's own NatSpec says it), so a
  confirmed test shows the behaviour exists, not that it is a flaw.
* Patch control: both confirmed tests pass on the original code and fail on hand-fixed code.

So the verifier can promote, but not yet gate anything: it misses real logic bugs and it can confirm intended
behaviour. Auto-adding verified findings to the directory stays off until the reviewer sees the function's own
comments and a test has to show harm, and a stronger writer has been measured.

## The three verifier fixes (2026-11-06, built, tested offline and measured once on Radius)

Made after the Radius calibration showed the verifier confirming documented design and missing multi-step
bugs: (1) the reviewer now sees the flagged function with the comments directly above it (where "this is
intended" lives) and must reject documented design; (2) the writer must state in one sentence who loses what
(`harm`, required) and the reviewer rejects a test that only shows what the code does; (3) the writer sees the
code of the contracts the flagged one inherits from (`source.inheritance_files`, nearest first, up to 60,000
characters), not only an outline of signatures. On Radius that is about 20 KB for the flagged file's three
bases and a writer prompt of about 16k tokens.

Measured on Radius (one run each, DeepSeek flash writer, $0.05): with titles as claims 2 of 7 findings were
confirmed (0 before), with the auditors' descriptions 1 of 7 (1 before, a different one); 3 of the 4 High
findings were confirmed in at least one run (0 of 4 before), but no run got more than 2 and the two runs disagree
on every claim, so the writer is noisy. A patch control (the same test on hand-fixed code) fails for all three
new confirmations: 0 wrong confirms of 3. The reviewer now rejects the earlier documented-design test, with and
without a stated harm.

Three repeats (n = 4 per claim and mode, $0.17 for 42 verifications): the confirm rate per High claim is
RADIUS-1 1/4 from the title and 4/4 from the auditors' description, RADIUS-2 2/4 and 2/4, RADIUS-3 3/4 and
1/4, RADIUS-4 0/8; about 1.6 of the 4 High findings per run, 3 of 4 over all runs. The Low, Medium and
Informational findings were never confirmed. All 10 new confirmations of RADIUS-1/2/3 fail on code with the bugs
fixed (0 wrong confirms). So the verifier is a reliable promoter and an unreliable finder: what it confirms
is real, it confirms roughly 40% of a High finding set per attempt, and which ones depends on the wording.

## Verification by reasoning (2026-11-06, Aryaan: "just run a reverification of each flag")

The sandbox is slow and only promotes (it confirmed 3 of the 4 High Radius findings on a good
day and 0 of 4 on RADIUS-4). So the main verifier is now a model that re-reads the flag
(`reason.py`), run on DeepSeek v4 pro or GLM 5.3 (Aryaan: no Claude model, too expensive). The
sandbox stays as an optional last step for logic bugs, off by default.

* It sees the claim, the flagged file (the function alone if the file is long), the files of the
  contracts it inherits from and an outline of the other files, all fenced.
* It must write the strongest reason the claim is wrong first (`counter`: a comment saying it is
  intended, a role check, a guard elsewhere, a power the owner is meant to have, no harm). Only if
  that fails may it say `valid`, and then it must describe the failing situation (`scenario`, a
  description and no steps or code) and the `harm`. A `valid` without both is read as `uncertain`.
* Its sentences are untrusted: links and markup are removed and they are cut to length. They are
  shown to the user and never stored in a record of ours (`stage.record` keeps the verdict words).
* `stage.annotate(..., reasoner=client)` runs it on every place no report or check already vouches
  for (`reason_only_logic=True` restricts it to what triage calls logic). `stage.review_list` splits
  the places into shown (credited or valid), uncertain, rejected and unchecked: **nothing is
  deleted**, the user is just not shown the rejected ones by default.
* v2 (`definitions.py`): the instructions carry Trail of Bits' 14 types, severity levels and difficulty
  levels (their difficulty scale has a level for a flaw only a privileged role can trigger, so that
  is a finding, not a non-finding). The model reads the lines first (`trace`), an `invalid` must cite
  lines that exist in the code shown or it counts as `uncertain`, and a real flaw whose cause is in
  another function is `valid` with a `root_cause`.
* The user's approval is the only gate to the directory (`queue.py`): the places the verifier calls
  valid become pending entries; `decide` marks one approved or rejected; only an approved entry of a
  report that may feed the directory (`directory_ok`, false for a held-out report) becomes a
  `directory_record`, tagged `source: model_verified_user_approved` so an evaluation can leave it out.
  Nothing writes the directory yet and there is no command for it.

First measurement (dev, 2026-11-06, not quotable): as a filter it raised the credited share about
2.6 times but dropped about 60% of what plain finds (no improvement shown by the pre-registered
rule); used as an order it looked better (exploratory). A stronger model (DeepSeek pro) was stricter
and no better matched to the answer key. A second reading is not a proof; two models can agree and both be wrong. How well it separates the
flags an auditor also found from the rest is measured, not assumed: see
`evals/datasets/tob_items/README.md` ("Reasoning verification").

## Not done yet

1. A command line (`python -m verify ...`); 2. the Radius calibration again with the three fixes, then a
stronger writer on the 4 High claims; 3. the first layered evaluation WITH the sandbox (needs more than one
report's toolchain: Radius only so far); 4. the staging area for the directory (never for held-out reports).
Design: `sandbox-verification-design.md` in the side-session notes folder.
