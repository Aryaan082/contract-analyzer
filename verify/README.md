# verify/ — a second reading of every flag

A reader (plain or the checklist pipeline) raises flags: a sentence about a place in the code. Most are
not real. This package re-reads each flag with a different model and says whether to show it.
Results and how they were measured: the root [README](../README.md) and
[`evals/datasets/tob_items/README.md`](../evals/datasets/tob_items/README.md). The evaluation command is
[`evals/source_review/verifier_eval.py`](../evals/source_review/verifier_eval.py).

| Module | Role |
|---|---|
| `reason.py` | **The verifier (v3).** Reads the claim, the flagged file, the files it inherits from and an outline of the rest, all fenced as untrusted text. |
| `definitions.py` | Trail of Bits' 14 types, severity levels and difficulty levels, in our words. |
| `funnel.py`, `mechanical.py` | Credit a flag that matches a known audit finding, then one a deterministic rule confirms (tx.origin auth, `transfer`'s 2300 gas); everything else goes to the verifier. |
| `stage.py` | Runs the funnel and the verifier over a report's places, orders the output without the answer key, splits it for a person (`review_list`). |
| `queue.py` | The review queue: pending, approved, rejected. Only an approved entry of a non-held-out report can become a directory record. |
| `source.py`, `types.py` | The code shown to a model; the shapes passed around. |

## The verifier (v3)

* The model answers two plain questions, not "is it valid": `claim_true` (does the code shown do what
  the claim says: yes, no, unclear) and `severity` (Trail of Bits' scale; `none` means harmless).
  **The harness works out the verdict** (`parse_reasoned`): no with a cited line is `invalid`,
  unclear is `uncertain`, yes is `valid` unless severity is none. A `valid` without a scenario,
  harm, type, severity and difficulty, or an `invalid` without a line that exists in the code shown,
  is read as `uncertain`.
* Trail of Bits counts a flaw only a privileged role can trigger as a finding (difficulty high), and
  best-practice gaps as informational. The instructions say so; a documented behaviour is not taken
  as safe; a flaw whose size depends on unseen code is `valid` with severity undetermined.
* The model's sentences are untrusted: links and markup are removed, lengths are cut, and they are
  never stored in a record of ours. The repo and the directory hold no attack steps or exploit code.
* `stage.review_list` shows credited places and valid places at severity low or up
  (`SHOWN_SEVERITIES`). Informational and undetermined go to `minor`; uncertain, rejected and
  unchecked are counted and kept. **Nothing is deleted.**
* Model: DeepSeek V4.1 Flash with thinking off (`deepseek/deepseek-v4.1-flash-1017`), about $0.0014
  a flag.

## The review queue

The user's approval is the only gate to the directory. Valid places become pending entries
(`queue.entries_from`); `decide` approves or rejects one; `directory_record` builds the record, tagged
`source: model_verified_user_approved` so an evaluation can leave it out. Entries from a held-out
report can be shown but never approved. Nothing writes the directory yet and there is no command
for it.

## Removed

A sandbox verifier (a model wrote a Foundry test, a second model reviewed it, `forge` ran it offline)
and the cheap triage call that chose which flags to simulate were removed on 2026-11-06: no
evaluation or flow used them once the reasoning verifier replaced them. On Radius EVMAuth the sandbox
confirmed 3 of the 4 High findings over all runs, never RADIUS-4, and nothing Low or Medium. Both are in
git history, last present at `c2ecc2f` (`verify/sandbox.py`, `poc.py`, `runner.py`, `triage.py`).

## Not done

A command line (`python -m verify ...`); wiring the layered run and the queue into `python -m agent`;
writing approved records into the directory; a test on reports nobody has read.
