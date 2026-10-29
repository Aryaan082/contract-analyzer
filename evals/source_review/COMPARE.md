# Plain model against our source review (head-to-head)

Question: with the same model reading the same contract text, does our source-review pipeline find
more of the planted bugs, with fewer false alarms, than the model used the way a person would use it?

```
uv run python -m evals.source_review compare dry-run                 # free, scripted
uv run python -m evals.source_review compare estimate --cache-dir D  # free; fetches source, no model
uv run python -m evals.source_review compare run --confirm-spend --max-spend 9 --cache-dir D
uv run python -m evals.source_review compare log --report results/compare-<date>.json
```

Model `anthropic/claude-opus-5.5` ($4 in / $20 out per million tokens, 1M context; checked against
OpenRouter's public catalogue on 2026-10-29), temperature 0, 8192 output tokens a request, the same
client class (`review.client.OpenRouterReviewer`, JSON-schema reply) in both arms.

**Arm P (plain)** is `plain.py`. One request per contract (split only above 250k estimated tokens).

> system: `You are an experienced smart-contract security auditor.`
> user: `Review the Solidity source below for security vulnerabilities. List the vulnerabilities you
> find. For each one give the file, the contract and function it is in, the line range, a severity
> (low, medium or high) and one sentence saying what is wrong. Report real vulnerabilities only;
> leave out gas optimisations and style comments. If you find none, return an empty list.`
> then the reply shape, then every file as `=== file: <path> ===` with numbered lines.

Raw source: comments, strings and imported libraries stay. Lines are numbered (the only change; a
line range cannot be given without numbers). No checklist, no stripping, no scan, no span check, no
second pass. An unusable reply (not JSON, cut off, no list) is "no findings" and is reported.

**Arm A (ours)** is `review.reviewer.run_review` with the same model as the reviewer, `second=None`:
stripped view, 32-entry checklist, span checks, known-library split for real contracts (none for
fixtures). It is NOT the full `python -m agent analyze`: the planted variants are not deployed.

**Items** (dev only): 12 planted families (vulnerable, safe twin, renamed copy), the 5 labelled dev
incidents (may be memorised), the dev benign contracts the explorer can read. Fixtures get one
neutral path for both arms. Third-party source is fetched at run time; results hold no source text.

**Scoring** is deterministic and at function level against draft labels (all unreviewed): recall and
precision on vulnerable items, false alarms (a medium-or-high flag) on safe twins and benign
contracts, the paired view with an exact sign test, twin discrimination, the renamed-copy control,
cost and seconds per contract, reply-format failures. No model judges anything.

**Money.** `run` refuses without `--confirm-spend` and `--max-spend`. `Budget` refuses any request
whose worst case (3.0 chars a token plus the full output limit) could pass the cap, adds the cost
OpenRouter reports after every reply, and the run also stops before an item whose central estimate
does not fit. A checkpoint line is written per item (`--resume` continues). What was not run is
listed in the report.
