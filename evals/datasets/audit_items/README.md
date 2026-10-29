# Audit-report items: pre-registration

Written on 2026-10-29 and committed before any model read any of these files. Question: on real, judged, known bugs, does the source-review pipeline find more of them than
the same model read plain? The headline is fixed below so that the result can be quoted whatever it
is, including "no difference shown".

`items.toml` lists the issues (title, file, linked line ranges, size, side, rank). It holds no source
text. The source is fetched at run time into a cache outside the repo.

## Where the items come from

Sherlock judging repos (judge-validated findings, public) created since 2025-01-01. Code4rena's GitHub
findings files for recent contests are output of an automated tool ("not all issues are guaranteed to
be correct"), not judged, so they are not used.

## Rules (fixed before any model saw any item)

1. Pool: every High or Medium issue in those repos whose report links code in the contest repo.
2. Keep an issue only if all its code links are in ONE `.sol` file; the file is not a test, mock,
   script, interface or library path; the linked lines span at most 150 lines; the file is 1.5 to 90
   KB; the title does not match `centraliz|trusted|malicious owner/admin|gas|typo|event|natspec|
   compiler|pragma`.
3. Per contest keep at most 6 issues: fewest auditors who found it first (a human-difficulty measure
   that does not depend on any model), then SHA-256 of `contest:issue`.
4. Sides: whole contests go to dev or held-out (hash order of the slug; each contest goes to the side
   with fewer items so far, tie to dev). Each side is cut to its first 30 issues by item hash
   (`rank` 1 to 30). The rest are the reserve (`rank` 31 and up), in order.
5. Bundle, identical for both arms: the affected file alone, as published, at the commit pinned in
   `items.toml` (head of `main` on 2026-10-29). No imports are resolved.
6. Checks after the fetch, which never involve a model. An issue is replaced by the next reserve
   issue of its side if (a) a code block of its report quotes this file but not at the linked lines
   (the file has moved on since the audit): at least 70% of the block's distinctive code lines (8
   characters or more, comments and spaces removed) are found in the file, none of the issue's blocks
   has that share within 5 lines of its linked ranges; (b) no function of the file contains or
   overlaps the linked lines (nothing to label); or (c) the file does not exist at the pinned
   commit. An issue none of whose blocks quotes the file (no block, or only a proof of concept)
   cannot fail (a) and is kept, flagged "unverified position". If the reserve runs out, the side
   stays short.
7. Label: the contract and function containing each linked range, from the parser (draft; unreviewed).
   An issue is found if any of its labelled functions is flagged by an arm, at any severity.
8. One run per FILE (a file may hold several issues; each run is scored against all of them). Files,
   not issues, are the unit for every interval.

## Arms

As in `evals/source_review/COMPARE.md`: P = plain, one request, raw source with line numbers, the
neutral prompt written there. A = `review.reviewer.run_review` with the same model (Claude Opus 5.5),
real known-library list, no second pass. Both: temperature 0, 8192 output tokens, JSON asked for in
the prompt with the schema (this model has no `response_format` endpoint), one run, same spend guard.

## Procedure

1. Dev (30 issues): run both arms. Fixing a defect in the pipeline found there (not tuning to
   the dev score) is allowed once, and must be logged in `evals/results.md` before held-out runs.
2. Held-out (30 issues): run both arms exactly once, with the code frozen at a named commit. If the
   run is interrupted it is resumed, never restarted. Whatever the result, it is reported.

## Headline (fixed now)

Held-out issue recall, pipeline against plain: the absolute difference in points and the relative
improvement (A - P) / P, each with a 95% bootstrap interval that resamples files, plus the paired
counts (A found and P missed, P found and A missed). "The pipeline performed x% better" may be said
only if the interval of the relative improvement excludes 0; otherwise the report says no difference
was shown. Dev results are exploratory and are never combined with held-out.

Also reported, descriptive only: recall by difficulty (found by 3 or fewer auditors against more),
by severity, flags per file and the share that match a judged issue (precision against a judged list
is a lower bound: real bugs outside the list count as misses), cost and seconds, unreadable replies.

## Known limits

The reports are public and a model may have seen them (contests are 2025-01 to 2025-07); the planted
renamed-copy control does not exist for these files. Both arms see only the affected file, so bugs
that need other files are hard for both. Labels are drafts until Aryaan reviews them. 30 held-out
issues in about 25 files can only show a large difference.

## Changes made before the first commit (no model had read anything)

- Rule 6(a) first looked only for a code block placed directly under a link. The first fetch showed
  that this verified 9 of 59 accepted issues, so it was widened to every code block of the issue, as
  written above. Nothing else was changed after the files were fetched.
- With the reserve used up, the held-out side holds 26 issues, not 30 (the 2025-04-burve repo no
  longer has the audited files at its head, and other files have changed). Dev holds 30.
