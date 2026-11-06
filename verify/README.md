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

## Not done yet

1. A command line (`python -m verify ...`); 2. the toolchain probe on Radius EVMAuth (needs
`solc` >= 0.8.20 and OpenZeppelin, both downloads to be approved); 3. the calibration (recall on
known findings and the wrong-confirm rate on false claims, about $0.3); 4. wiring it behind the
pipeline and the staging area for the directory (never for held-out reports). Design:
`sandbox-verification-design.md` in the side-session notes folder.
