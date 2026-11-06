# Trail of Bits items: pre-registration

Status: **pre-registered, committed 2026-11-04 with Aryaan's yes, before any model call.** No model has
read any report, finding or file. From this commit the rules below do not change, and any later edit is
listed under "Changes after the first commit".

Question: on real, known bugs that plain Claude Opus 5.5 misses or mislabels, does our source-review
pipeline (same model) do better? Earlier comparisons (planted bugs 12/12 vs 12/12; 26 held-out Sherlock
issues, 11 vs 12 found) were too easy for any difference to show. This one selects the hard findings
first, then measures both arms on them. It is a measurement, not a hunt for a win: a tie is reported as
a tie, and the pipeline's prompt, checklist and code are not changed to fit these items.

## Source and credit

Findings come from public Trail of Bits (ToB) security reviews,
<https://github.com/trailofbits/publications>, pinned at commit `89940dfb…` (the commit
`knowledge/manifest.json` pins), licence **CC-BY-SA-4.0**. Credit: Trail of Bits. This directory holds
only ids, severities, titles, target paths, categories, difficulties and page numbers of their
findings, plus hashes and sizes. No report prose and no third-party code is stored in the repo: the PDFs
and the audited source live in a cache outside it, and the judge reads the report text from the PDF at
run time. None of these reports is in the knowledge base (`knowledge/manifest.json` lists 11 others), so
the agent cannot retrieve their answers.

`reports.toml` pins the 69 PDFs Aryaan approved on 2026-11-03 (33 chosen by name, then 36 more from a
metadata-only survey of the publications README: not in the knowledge base, EVM-like section, 2023 or
later, at least 2 person-weeks; each with size, SHA-256 and git blob hash, checked against the
publications repository's own listing) and records what each rule decided.
`files.toml` pins the in-scope Solidity files of each kept report (path, size, git blob hash).
`labels.toml` lists the findings to look for.

## The final pool (2026-11-04)

69 PDFs were pinned; the rules leave **12 reports and 51 labelled findings**. The sides are by
slug hash (29 findings in dev, 22 held out). The Aladdin f(x) report was dropped by rule 4
(its whole monorepo is 270,030 tokens).

| report | side | findings | pipeline tokens | repositories (licence) |
|---|---|---|---|---|
| 2023-04-ajnalabs | dev | 2 | 65,589 | ajna-finance/ajna-core (NOASSERTION), ajna-finance/ajna-grants (none) |
| 2023-05-fraxgov | dev | 3 | 25,755 | FraxFinance/frax-governance (none) |
| 2023-07-arcade | dev | 4 | 32,642 | arcadexyz/arcade-protocol (MIT) |
| 2025-05-caplabs-coveredagentprotocol | dev | 15 | 40,804 | cap-labs-dev/cap-contracts (NOASSERTION) |
| 2025-10-radiustechnology-evmauth | dev | 5 | 7,001 | evmauth/evmauth-core (MIT) |
| 2023-04-tempus-raft | held_out | 3 | 16,581 | raft-fi/contracts (NOASSERTION) |
| 2023-07-sandclock | held_out | 1 | 26,898 | lindy-labs/sandclock-contracts (AGPL-3.0) |
| 2024-05-lisksmartcontracts | held_out | 1 | 45,945 | LiskHQ/lisk-contracts (Apache-2.0) |
| 2025-01-bacon-labs-bunniv2 | held_out | 8 | 98,364 | Bunniapp/bunni-v2 (MIT), Bunniapp/biddog (AGPL-3.0) |
| 2025-02-automata-dcap-attestation-onchain-pccs | held_out | 4 | 46,829 | automata-network/automata-on-chain-pccs (MIT), automata-network/automata-dcap-attestation (MIT) |
| 2025-02-chainlink-customsendersreceivers | held_out | 1 | 14,825 | Aphyla/chainlink-csr (NOASSERTION) |
| 2025-04-reserve-folio-solidity | held_out | 4 | 16,100 | reserve-protocol/reserve-index-dtf (MIT) |

## Rules (fixed before any model saw any item; none involves a model)

A report is dropped, with its reason recorded in `reports.toml`, at the first rule it fails:

1. **Readable and relevant.** The knowledge parser (`knowledge/parse/tob.py`) reads it, and it has a
   finding rated High, Critical, Medium or Undetermined. Low and Informational findings are not looked
   for.
2. **Public Solidity code at a commit.** Its "Project Targets" name GitHub repositories, each with a
   commit; the review is not of a diff or pull request (the audited scope would be changed lines only);
   no repository is `n/a`, a zip file or on another host. For a repository listed twice (audited and
   fixed commit) the first commit is used.
3. **The code exists.** GitHub has the repository and the commit (a short hash is resolved to the full
   one). Source is fetched at that commit only, never at head.
4. **Size ceiling.** The in-scope code is at most **200,000 tokens**, counted as the pipeline counts
   (`review.known`: stripped text of definitions that are not a copy of a known library release, 3.5
   characters a token). The same ceiling applies to both arms. Aryaan raised it from 100,000 on
   2026-11-03, before any source was read, so that Bunni v2 (about 172,000 raw tokens) is not lost.
   Arm P sends the raw files in one request unless they pass 250,000 estimated tokens, when
   `plain.py` splits them into parts (every report keeps its own parts; this is noted per report).
5. **A finding names a file.** At least one kept finding's `Target` resolves to an in-scope file.

**In scope** means a `.sol` file that is not under a test, mock, script, deploy, example or docs
directory, not a `.t.sol` / `.s.sol` / `Mock*` / `Test*` / `*Harness` file, and not under a top-level
dependency folder (`lib/`, `node_modules/`, `out/`, …; a project's own `src/lib/` is kept). Interfaces
stay. The exact rule is `tob_items.in_scope`.

**A finding is kept** if its ToB severity is High, Critical, Medium or Undetermined and its `Target`
resolves (`tob_items.resolve_target`) to one or more in-scope files: braces expanded, a trailing line
number removed, then the exact path, a path that ends the other, the file name ignoring case, or (for an
extension that is not `.sol`, a typo like `.col`) the file stem, each only if it matches exactly one
file. A target that names a contract or a topic instead of a file (`OraclePool`, `Various targets`)
drops the finding. The kept findings are the labels; "target file" is the label's location.

**Sides.** Whole reports go to dev or held-out: in hash order of the slug, each report goes to the side
with fewer labelled findings so far, a tie to dev. Dev is exploratory; held-out is run once with the
code frozen at a named commit and whatever it shows is reported.

**Bundle, identical for both arms.** Every in-scope Solidity file of the report, at its commit, as
published, under its repository path (prefixed with the repository name when the report has more than
one repository). Arm P reads them raw with line numbers in one request; arm A reads them through
`review.reviewer.run_review` (stripped view, 12k-token chunks, real known-library list, `second=None`).

## Arms

As in `evals/source_review/COMPARE.md`: P = plain, the neutral prompt there; A = `run_review`; both
Claude Opus 5.5 (`anthropic/claude-opus-5.5`) at temperature 0, 8192 output tokens a request, JSON asked
for in the prompt (no `response_format` endpoint exists for this model), the same spend guard.

## Stages

1. **Screening.** Plain Opus (P1), once per kept report of both sides. The judge says which labelled
   findings P1 found, mislabelled or missed.
2. **Hard set.** The labelled findings P1 did not find (missed or mislabelled).
3. **Scoring.** A **fresh** plain run (P2) and a pipeline run (A) on the same bundles, scored on the hard
   set only. The baseline is P2, not P1: choosing items by P1's miss makes a re-run of plain look better
   by chance (regression to the mean), and P2 carries that effect. How often P2 finds what P1 missed,
   and misses what P1 found, is also the measurement of repeat noise, and is reported.

## The judge

**What it decides.** For one (flag, labelled finding) pair: the flag's text and place, and the ToB
finding's title, target and Description section (read from the PDF at run time). The arm is hidden, the
flag's severity and checklist entry are not shown, and the order of pairs is shuffled. All text from the
reviewer and from the report goes into fenced blocks as untrusted data (`evals/judge/rubric.py`). The
judge answers one of:

- `same`: the flag names the same underlying defect as the finding: fixing the finding's defect would
  fix the flag's. Different wording, only part of the impact, or another consequence is fine; being in
  the same function or on the same topic is not.
- `same_place`: the flag is about the code the finding is about (the same function or logic) but names a
  different defect, or a materially different mechanism.
- `unrelated`.

**Who judges.** `evals/judge/policy.toml`: one judge (`z-ai/glm-5.3-flash`) for development runs, and
two from two companies (`z-ai/glm-5.3-flash`, `qwen/qwen3.7-flash`) for every number that is quoted. A
judge is never from the company of the model it grades (Anthropic). The held-out headline uses both.

**Combining two judges (fixed now).** A pair is `same` only if both judges say `same`. It is
`place-match` if both say `same` or `same_place`. Any other disagreement is `disputed`; every disputed
pair is listed for Aryaan. Primary = strict (disputed counts as not found). A lenient reading (a pair
`same` if either judge says so) is reported as a sensitivity check.

**Verdict per finding and run.** *found*: at least one flag is `same`. *mislabelled*: not found, and at
least one flag is `place-match`. *missed*: neither.

**Rubric approval.** The rubric text is in `rubric.toml`; the hash Aryaan approved is recorded in `rubric.approved`. **Before any judged
number is used, Aryaan hand-checks at least 20 judged pairs** (drawn from dev, arm hidden, at least five
of each verdict that exists, written to the cache with the report text for him to read); agreement with
his labels is reported next to the result. A change to the rubric gives a new hash and restarts the
check. No held-out pair is used to develop the rubric.

## Headline (fixed now)

**Held-out hard-finding recall, pipeline (A) against fresh plain (P2)**: the absolute difference in
points and the relative improvement (A − P2) / P2, each with a 95% bootstrap interval that resamples
**reports** (a report's findings stay together), plus the paired counts (A found and P2 missed; P2 found
and A missed; both; neither). "The pipeline performed x% better" may be said only if the interval of the
relative improvement excludes 0; otherwise the report says no difference was shown. If P2 finds none of
the hard findings the relative figure is undefined and only the points are reported. Dev results are
exploratory and never combined with held-out.

Also reported, descriptive only: recall over all labelled findings for P1, P2 and A; how many of P1's
misses were mislabels; flags that match no labelled finding per report (a **lower bound on precision**:
real bugs ToB did not list, and bugs ToB listed as Low or Informational, count as false alarms); cost and
seconds; unreadable replies; recall by ToB's own exploit-difficulty label; the strict and lenient
readings; the P1-versus-P2 repeat-noise table.

## Budget and gates

Every paid step needs Aryaan's yes, with the estimate from real bundle sizes: free dry run first,
`--confirm-spend` and `--max-spend`, a checkpoint per item, `--resume`, stop after 3 silent items, and
the OpenRouter balance read first. Held-out additionally needs `--declare` and a clean tree.

## Known limits

- **Few reports.** There are 12 reports and 51 labelled findings, split between two sides (5 and 7
  reports), and only the findings plain missed are scored. Intervals that resample reports are wide
  with 5 to 7 reports a side; a real difference can be missed, and "no difference shown" is the
  likely outcome whatever the truth is.
- **Memorisation.** ToB reports and the repositories they audit are public, and Opus may have seen
  them; older reports are more likely to have been seen than newer ones, and no renamed-copy control
  exists for them. The screening run is what removes the findings plain already knows: the hard set
  holds only what plain missed, so memorisation inflates plain's screening score, not the hard set,
  but it can still lift the fresh plain run and the pipeline on the hard set if the miss was chance.
  Recall is also reported by report date, so an age effect would show.
- The labels are drafts made by rule from ToB's own `Target` line until Aryaan reviews them; a finding
  whose target names a contract rather than a file is not labelled.
- The judge is a small model checked against Aryaan's reading of 20 pairs, not against truth.
- A flag that matches no listed finding counts against precision even when it is a real bug.
- One run per arm and stage at temperature 0: repeat noise is estimated only from P1 against P2.

## Changes before the first commit (no model had read anything)

- Rule 5 first matched only `.sol` file names. The `Target` of three Bunni findings reads
  `BunniSwapMath.col`, so a stem match was added for an extension that is not `.sol`.
- The ceiling was raised from 100,000 to 200,000 tokens (Aryaan, 2026-11-03) before any source was
  read, expecting Bunni v2 to be over. Once fetched, the pipeline counts Bunni v2 at 98,364 tokens
  (raw files are about 172,000), so for the first six reports it changes nothing; it stays for the
  widened pool.
- The pool was widened (Aryaan, 2026-11-03) with 36 more PDFs. Two readings of "Project Targets" were
  generalised after seeing that they dropped reports for their layout alone: an `Initial Version` line
  counts as the audited commit (the `Final Version` is the fixes), and a bare `owner/name` counts as a
  GitHub repository. A URL with a path (`/pull/…`, `/tree/…`) is still refused. Of the 36: 7 are candidates (13 with the first 6), 11 audited a repository that
  is not (or no longer) public, 8 are "summary reports" with a findings table and no per-finding text,
  5 print no commit, a pull-request URL, a zip file or a path in a repository, 2 have a layout the
  parser does not read (Nested Finance, 83 pages, is one of them), 2 have no High or Medium finding and
  1 has none in Solidity.
- The first draft of "in scope" excluded every folder called `lib`, which removed Bunni's own
  `src/lib/`; dependency folders now count only at the top of the repository.

## Changes after the first commit (no model had read anything)

- The rubric file is `rubric.toml` (the first text said `tob_rubric.toml`), and its approved hash goes
  in `rubric.approved`; the judge, run and report code were added (`tob_judge.py`, `tob_run.py`,
  `tob_report.py`, `tob_eval_cli.py`). None of these touches a rule above.
- **Plain output limit (Aryaan, 2026-11-04).** The dev screening run (8,192 output tokens a request, as
  the arms section says) had two plain replies cut off at the limit (Cap and Ajna, the two largest
  bundles), which by the fixed reading rule counts as no findings and would have made 17 of the 29 dev
  findings "missed" by truncation. Both were run again with a limit of **32,768** and finished at 7,828
  and 6,267 tokens, so the first cut-offs were borderline. From then on every plain request has a
  32,768-token limit (`--plain-max-tokens`); the three dev replies that finished at 8,192 are kept.
  The pipeline keeps 8,192 a call (its calls are chunk-sized) and a cut-off there is still counted as
  no findings and reported. The $1.84 paid for the two cut-off replies stays in the cost totals. This
  changes no rule above for the findings, the judge or the headline.
- **Three judges, a majority (Aryaan, 2026-11-04; before any held-out run and before DeepSeek had
  judged anything).** The judges are `z-ai/glm-5.3-flash`, `qwen/qwen3.7-flash` and
  `deepseek/deepseek-v4.1-flash-1017` (three companies; DeepSeek is allowed by `policy.toml` because
  the model judged is Anthropic's). A pair is `same` if more than half the judges say `same`, otherwise
  `same_place` if more than half say `same` or `same_place`, otherwise `unrelated` if more than half
  say it; with three judges there is always a majority, so no pair is disputed. This replaces "both
  judges must agree" in the section on combining judges; the unanimous reading (every judge says
  `same`) and the lenient reading (any judge says `same`) are reported as sensitivity checks. The
  report also shows how often each judge is the minority of a 2 to 1 split and whether it was the more
  generous or the stricter one, so a judge that differs from the others shows. The first 249 dev
  screening pairs were judged by glm and qwen before this change; DeepSeek judges the same pairs.
- **Report-time accounting (2026-11-04, after the held-out run; no arm or judge was affected).** The
  judge cost shown in a report counts only the cached judgements of that side's reports (it first
  counted the whole cache). A judge pair that one judge could not answer after several tries stays
  unanswered: two glm pairs (one dev, Cap-6; one held-out, Bunni-15). In both the verdict kind of the
  finding is `unjudged` unless another pair settles it; for Bunni-15 the other two judges said
  `same_place`, so a `same` from glm could not have made it found by a majority.
- **Pipeline reply reader: one fence allowed, one retry (Aryaan, 2026-11-04; POST-HOC, after both
  sides were run and reported).** In the pre-registered runs 5 of the 11 scored pipeline reports
  lost a chunk because the reviewer's reply was not read as JSON (dev: Arcade 1 request of 3, Ajna 1
  of 7; held-out: Bunni 1 of 10, Automata 1 of 5, Reserve 1 of 2, which flagged nothing at all). The
  plain arm's reader already accepted one Markdown fence round the JSON, so the two arms were read
  by different rules. The raw replies were not kept, so whether they were fenced or had words round
  the JSON is not known. Changes, in `review/spans.py` and `review/client.py`: (1) the pipeline
  reader accepts exactly one code fence round an otherwise exact JSON object, the plain arm's rule;
  JSON is never searched for in prose. (2) A reply that is empty, not JSON or without an item list
  is requested once more: the same request with one fixed sentence at the end of the user message
  ("Reply with the JSON object only: ..."); the second answer replaces the first; a reply cut off
  at the output limit, or a model that cannot answer, is not asked again; both requests are paid
  for and counted. The system prompt (and its hash), the checklist, the chunking and the item checks
  are unchanged. The harness gained `--runs NAME` (a second folder in the cache, so the
  pre-registered records are not touched), `score --stages`, `report --note`, a log of raw replies
  that are not bare JSON (outside the repo) and a count of retried requests in each record.
  The pre-registered results (commit `b33e224`) stay the official ones. The pipeline is run again
  on all 11 scored reports, not only the five that failed; the plain stages are not run again and
  keep their records; the same three judges with the same rubric judge the new flags. Every result
  from this re-run is labelled "after the reply-reading fix, post-hoc". The held-out re-run is not a
  second blind test: the code was changed after its results were seen.
  Result of the re-run (judged with the draft rubric, so not quotable): dev pipeline 2/21 against
  fresh plain 1/21 (the first run: 5/21), held-out pipeline 1/20 against 1/20 (the first run: 0/20);
  neither shows an improvement. Only held-out needed the retry, twice in about 30 requests, and the
  two failed replies were a paragraph before a fenced block and an invalid JSON object, which a
  fence rule alone would not have read. Reports are `*-2026-11-04-fixed.*` next to the originals.
- **A model setting and a dev-only judge waiver (Aryaan, 2026-11-04; harness only, no
  pre-registered result is touched).** `--model opus|deepseek` picks the model under test for both
  arms (default `opus`, what every pre-registered run used); each stage record now names the model
  that made it, and a report prints the model of each stage. DeepSeek flash
  (`deepseek/deepseek-v4.1-flash-1017`, 1,048,576 tokens of context, JSON schema supported) is for
  development runs: about 13 times cheaper per input token than Opus. Because DeepSeek is also one
  of the three judges, the policy rule "a judge never grades its own company" refuses a DeepSeek
  run until `--allow-same-company WHY` is given; the report then carries a banner, says which judge
  it is, and is marked not quotable. The rule is unchanged for every other use. The hard set is
  still the findings Opus-plain missed in screening; a DeepSeek run compares DeepSeek-pipeline with
  DeepSeek-plain on it, and any run on a new directory or prompt is exploratory (these findings
  are no longer unseen) until it is confirmed on reports nobody has looked at.
- **An experimental two-pass pipeline (Aryaan, 2026-11-04; the pre-registered runs used the single
  pass and are untouched).** `--pipeline two_pass` on `score` runs arm A as leads then checks
  (`review/twopass.py`): per chunk, one request names the functions where a checklist entry might
  apply, a second reads the chunk again with only those entries and the definitions the lead
  functions call (one hop, by name) and decides each lead as applies, not applicable or unsure; only
  `applies` is flagged, and then through the same checks as a single-pass item (file and lines
  shown, quote in the code, entry in the checklist). The prompts for it say nothing about these
  findings. It is for development on DeepSeek and exploratory until it is confirmed on reports nobody
  has looked at. A runs folder holds one mode only (a mix is refused), the record names its mode, and
  the estimate for it is the single-pass estimate times 2.2, an assumption.
- **DeepSeek runs with its hidden thinking off (smoke test on the smallest dev report, 2026-11-04,
  $0.03).** At 8,192 output tokens DeepSeek's thinking alone used the whole budget and both modes
  returned an empty reply cut off at the limit. `--reasoning auto` (the default) now turns thinking
  off for `--model deepseek` and leaves Opus as it was; each record says which. With it off, the
  single pass gave 15 flags for $0.004 and the two-pass 26 flags for $0.011 (2.9 times, one reply of
  the second pass cut by a provider error and rescued by the retry), so the two-pass estimate factor
  is now 3.0. One report is a smoke test, not a result. The product's own reviewer setting
  (`reviewer_model = "eval"`, 8,192 tokens) was not tested in its own request form and may have the
  same problem.
- **Judging and reports that stop short now say so (2026-11-05, found in the DeepSeek dev
  comparison, no judge or model rule changed).** The 24-worker judging stopped at its spend cap
  (the cap counts each request in flight at its worst case, so many workers hold back much of it)
  and still exited with 0, and `report` then wrote files from partial judging: pairs without a
  verdict from every judge were silently left out, so the pipeline looked like 0 of 29. `judge` now
  exits 3 while any pair is unjudged (stopped, left over or unreadable), `report` refuses while any
  pair lacks a verdict unless `--allow-partial`, which puts PARTIAL JUDGING on top of the file and in
  the results row. The results row also named "plain Opus 5.5" whatever the model; it now names the
  model under test.
- **Two-pass first pass is told the outline is context (2026-11-05, prompt text changed; one
  re-run on two reports, not judged).** In the DeepSeek dev run leads named files or functions that
  this call did not show (Ajna 73, Fraxgov 21, Cap 18, Arcade 5, Radius 0); the bundle outline
  lists every file of the bundle, which fits Radius (one call, so nothing is outside it) having
  none. Those drops now count as `other_part` when the code exists in a part another call reads,
  and as `unknown_file` or `unknown_function` only when it does not exist, and the first-pass rule
  says a function that is only in the outline is not to be named. Re-run on Ajna and Fraxgov (one
  run each, $0.11): leads naming an unknown file went from 41 to 0 (Ajna) and 20 to 0 (Fraxgov),
  none counted as `other_part`, and `unknown_function` stayed at 27 (Ajna) and 1 (Fraxgov), so
  those are names the parser does not find in the file at all. Flags: Ajna 21 (was 15), Fraxgov 0
  (was 0, with 98 leads decided `not_applicable`), so Fraxgov's empty result is the second pass
  finding nothing, not lost leads. One run each is not enough to say the extra Ajna flags are the
  rule's doing, and none of the flags was judged.
- **A wide key as a post-hoc secondary view, dev only (2026-11-05, Aryaan: severity is subjective,
  and the flags of both arms also hit the Low and Informational findings).** `--key wide` on `judge`
  and `report` adds the Low and Informational findings of the five dev reports that name a Solidity
  file of the code (33 of 36; the other three are about tests, documentation or library versions) to
  the labels, each judged only against the flags in its own files (`Label.file_scoped`), which keeps
  8% of the pairs and drops about 14% of the `same` verdicts, so wide counts are lower bounds. The
  report file gets `-wide` in its name, a WIDE KEY banner and a marked results row, and it files no
  run records. The pre-registered key, its hard set and every number reported so far are unchanged;
  held-out refuses the option. First look (DeepSeek, 62 findings, 46 hard): fresh plain 1, single
  pass 3, two-pass 5 and 8 (earlier prompt, outline rule); the report's own rule still says no
  improvement shown for the best of them (+0.15 [-0.06, 0.27], sign test p 0.04, three comparisons
  and a key chosen after seeing results).
- **A plain-plus-checklist variant of arm P, for an ablation only (2026-11-05, Aryaan asked why the
  pipeline trails plain; run 3 times the same day, result at the end of this entry).** Five runs of each arm on the dev side (DeepSeek,
  not quotable) found plain 8.6 of 29 findings (range 7-10) and the two-pass pipeline 5.8 (range
  4-10); the single run of the outline rule that looked best for the pipeline was the best of its
  five. `--plain-variant checklist` on `screen` and `score` sends the same plain request with the
  pipeline's own 32-entry checklist (about 8,200 tokens, `review.pack.Checklist.text`) added between
  the reply shape and "the source files follow", framed as a reminder and not a limit, and nothing
  else changed: same raw source, same reply shape, same output limit. Its records carry
  `plain_variant`, a runs folder never mixes variants, the variant cannot run with the pipeline stage,
  and the report shows `[plain + checklist]` next to the model. The pre-registered arm is unchanged
  (its records have no `plain_variant` and its prompt is byte for byte the same). The question it
  answers: does adding the checklist to plain lower its count, or is the loss elsewhere (the second
  step, the 12k-token chunks, the stripped comments)?
  Result (DeepSeek, dev, 3 runs, 3-judge majority, not quotable: rubric unapproved, DeepSeek judges
  itself): plain + checklist found 5, 6 and 6 of the 29 findings (plain 8.6, range 7-10; two-pass
  5.8) and 7, 9 and 9 of the 62 on the wide key (plain 13.2; two-pass 10.2). The drop against plain
  is -2.9 and -4.9 findings per run (exact permutation p 0.036 and 0.018 over the 56 ways to split
  the 8 runs; the two groups do not overlap), and it lands at the pipeline's level, so the
  checklist alone is enough to cost the gap. It does not say why:
  anchoring on the list and 8,200 tokens of instructions ahead of the code are both consistent with
  it. One run had one reply-format failure (Arcade, counted as no findings by the fixed rule).
  Cost $0.33 for the runs and about $1.59 for judging.
- **A layered run, scored on its output (2026-11-06, written before any layered run; Aryaan: plain
  first, verify its flags, the pipeline second, the union, additive only; "we shouldn't just be able
  to spew information to perform well on evals").** `review/layered.py` runs the plain reader and the
  checklist pipeline as separate runs and joins them by place (file, contract, function); every plain
  place stays in the result with its flags unchanged, whatever the pipeline does or whether it ran
  (`additive_problems` checks it on every run). `verify/stage.py` annotates the places (triage, funnel,
  sandbox) and puts them in the system's own order, which never sees the answer key. The plain
  reader moved to `review/plain.py`; its request is byte for byte what it was (a test pins the hashes).
  Why the scoring changes: on the dev runs plain emits 146 flags a run, 9% are judged the same bug as
  an auditor's finding, 23% sit in the right place with a different bug and 69% are neither, and its
  first 20 flags per report find 7.4 findings while all of them find 13.2, so recall over everything
  rewards writing more. The rule for the layered evaluation, fixed now: unit is the report; arms are
  A plain alone in its own severity order, C the layered union in plain-first order (the control for
  the union) and B the layered union in the verification-aware order. Primary metric: findings found
  in the first K = 20 places of each report (`evals/source_review/precision.py`; K is taken from
  plain's own curve, before any layered run). Guard: the share of those places with any evidence (an
  answer-key match, a sandbox confirmation, a deterministic check) must not be lower than A's.
  Improvement is shown only if the report-level bootstrap interval of B minus A at K = 20 excludes 0
  and the guard holds; otherwise the report says no improvement shown. Reported beside it, never as
  the headline: findings found over everything, places emitted, the unverified share (an UPPER bound
  on false positives, never called false: the auditors' list is incomplete and the sandbox confirmed 0
  of the 4 High Radius findings), places with evidence the key does not know (`verified_beyond_key`),
  findings per 100 places, K = 5, 10, 40, both keys, cost. The first layered evaluation is offline on
  the ten judged dev runs (plain run i with two-pass run i), with real triage and no sandbox, so it
  tests the ordering and the union, not verification.
  Result of the first layered evaluation (2026-11-06, offline, DeepSeek, not quotable): by the rule
  above the verdict is NO improvement shown: B minus A at K = 20 is +2.0 findings per report-set
  [-0.4, +4.8] on the wide key and +1.8 [0.0, +3.8] on the original key (the guard holds). The union
  alone (C) minus A is +1.4 [+0.2, +3.2] on both keys, because in the small reports plain's list is
  shorter than 20 and the union fills the slots, and the triage ordering adds +0.6 [-1.2, +3.4] and
  +0.4 [-0.8, +2.2] on top of it, not distinguishable from 0. The unverified share stays 90% to 94%
  and findings per 100 places are 9.3 against 9.6, so precision did not improve: the layered output is
  38% longer for 38% more finds. Correction: the first run of this evaluation printed its decision
  block with B and C swapped (a driver bug) and was reported as "improvement shown"; the corrected
  second run above replaces it. Triage cost $0.10 a run.

## Reasoning verification (2026-11-06, written before any model read a place; dev only, not quotable)

Aryaan replaced the sandbox as the main verifier with a model that re-reads each flag
(`verify/reason.py`: refute first; a `valid` needs a scenario and a harm; DeepSeek v4 pro and GLM
5.3, no Claude model) and shows only what it calls valid. His approval is needed only to add a flag
to the directory, so no paid call here waits for a yes. The rule, fixed now:

* Data: the five judged dev run pairs (plain run i with two-pass run i): 350 distinct places, one
  verdict per distinct place (the claim of its first appearance), used wherever the place appears.
  No place is credited by a report (the reports are the answer key). Triage re-run with the current
  prompt (the old cache no longer matches).
* Arms: A plain alone (all its places); V the layered union with only the places the verifier calls
  `valid` shown. Variants beside it: each verifier alone, both agreeing, either, and a cascade
  (valid and triage says logic).
* Primary: the share of shown places that are credited (a flag there was judged the same bug as an
  auditor's finding, wide key), V minus A, with the report-level bootstrap interval (5 reports).
* Guards: findings found by V at least A's in the mean (no loss against plain), and fewer places
  shown than A.
* Verdict: "improvement shown" only if the credited-share interval excludes 0 on the high side and
  both guards hold; otherwise "no improvement shown", with the part that failed named.
* Reported beside it: the share of credited places the verifier keeps, the share of the others it
  drops, found, shown, the original key, cost, and the verifiers' agreement.
* Limits: "unverified" is an upper bound on false positives (the key is incomplete), one verdict per
  place (re-run noise is not measured), DeepSeek pro verifies flags DeepSeek flash raised (same
  company; GLM is the independent check), the judge panel includes DeepSeek under the waiver and the
  rubric is not approved.

Result (2026-11-06, dev, DeepSeek is also a judge under the waiver, **not quotable**): by the rule above
the verdict is NO improvement shown. DeepSeek flash as the verifier, all 350 distinct places: showing
only the places it calls valid gives 17 places a run (plain alone 142) with a credited share of 23.5%
against 8.9% (+14.6% [+3.7%, +35.7%] on the wide key), but it finds 5.2 of the 62 findings against
plain's 13.2 (-8.0 [-16.0, -2.0]), so the "no loss against plain" guard fails. It rejects 59% of the
places that matched an auditor finding and 81% of the others. A stronger verifier did not fix it: DeepSeek
pro on a 74-place sample (37 matched, 37 not) called 7 of 37 matched places valid against 4 of 37
others and rejected 28 of the 37 matched. Reasons seen in the rejections: a privileged-role or
documented-design reading (the prompt's standard is harm to an outsider, and many auditor findings
are about a misconfigurable role), and some real mistakes by the verifier. Exploratory and chosen
after seeing this: the same verdicts used as an order (nothing hidden) find 11.0 against plain's 7.2
in the first 20 places per report (+3.8 [+1.0, +6.6]); that needs a repeat, with new verdicts, before
anyone relies on it. No prompt change was made after looking at the rejections: the places that
matched a finding are the answer key.

Repeat (2026-11-06, same prompt, dev, not quotable): asking flash again gave the same verdict on 83% of
the places but only 14 of the 45 places either run called valid were valid in both, so one verdict is
noisy. The filter again fails the "no loss against plain" guard (6.8 found against 13.2). The order
result repeats (10.4 against plain's 7.2, +3.2 [+0.8, +5.6]) but its gain over the plain union (9.2) is
+1.2 [0.0, +3.2]: most of the lift is the union itself. GLM 5.3 could not be scored (83 of 350
answered: it thinks too long for the token limit; the spend cap refused the rest).

Verifier v2 (2026-11-06, written before v2 ran; dev, not quotable). v2 changes the prompt and the
reply checks only (`verify/definitions.py`, `verify/reason.py`): it gives the verifier Trail of Bits'
types, severity levels and difficulty levels; says that needing a privileged role or having a small
impact does not stop a flaw being a finding (difficulty high, severity low or informational); has it
read the lines first (`trace`); makes an `invalid` cite lines that exist in the code shown, else it
counts as `uncertain`; and calls a real flaw caused in another function `valid` with a `root_cause`.
It is run on the same 350 distinct places, the same first-seen claims, DeepSeek flash with thinking
off, and scored by the same rule as above (filter arm = places called valid; "improvement shown" only
if the credited-share interval excludes 0 on the high side and both guards hold: found at least as
many findings as plain alone, and fewer places shown). Declared before the run: v2 was written after
the rejections of the 16 dev places whose verified claim matched a finding had been read, so its dev
numbers are tuned to them and are no evidence that it generalises; Ajna is the one dev report where
no rejection was read, so it is reported on its own. Reported beside it: valid at severity medium or
high only, valid + uncertain, the order arm, per report, and how many replies were downgraded. The
v1 filter failed the guard twice (found 5.2 and 6.8 against plain's 13.2); a v2 filter that again
finds fewer than plain is reported as that, whatever its credited share.

Result of verifier v2 (2026-11-06, dev, flash, not quotable): by the rule above NO improvement shown. As
a filter (valid only) v2 shows 41 places a run against plain's 142 with a credited share of 21.6%
against 8.9% (+12.7% [+5.1%, +18.3%], so the interval now excludes 0), but it finds 9.4 of 62 findings
against plain's 13.2 (-3.8 [-8.2, -0.6]); the original key gives 6.0 against 8.6. Against v1 it keeps
more (45% of the places that matched a finding, against 20% to 28%) and drops fewer of the rest (82%,
against 89% to 93%): the same trade-off point v1 reached by also showing its uncertain places (44%
and 81%), so the place-level separation did not clearly improve. Where it did change: on the 25
places whose verified claim itself matched a finding, v2 says valid on 13 (v1: 3), and 7 of the 9
High findings survive a valid-only filter (v1: 3 to 4). Those 25 places contain the 16 rejections read
to write v2, so this is tuning on dev. Ajna cannot serve as the clean report: plain finds none of its
findings in any run. As an order, nothing hidden, v2 finds 12.2 against plain's 7.2 in the first 20
places per report (+5.0 [+0.8, +10.6]) and +3.0 [0.0, +8.6] over the plain union, with no gain at the
first 10 or 40. The next clean step is to verify every distinct claim (not one per place) and to
test on reports no one has read.

Iteration protocol for verifier v3 (2026-11-06, written before v3 ran; Aryaan: "recursively update the
verifier until it returns more bugs than plain with better hit rate"). This is tuning against the dev
answer key, which the working agreement forbids, so it is fenced in. Changes in v3: the verdict is
worked out by the harness from `claim_true` (does the code do what the claim says) and `severity`
(none = harmless), not from the model's word; a documented behaviour is not taken as safe; a flaw whose
size depends on unseen code is `valid` with severity undetermined. Round 1 also fixes how the verifier
is scored: every distinct flag gets its own verdict (about 900 claims, not one per place), a place is
shown if any of its claims is valid, and a finding counts as found only through a claim called valid.
Rules: the changes are made from Cap Labs and Fraxgov (tune); Arcade and Radius are the check, and no
case from them is read during the iteration, only totals; Ajna has no matched finding and says
nothing. Success is fixed now and is the earlier rule: on all five reports, found at least as many
findings as plain alone AND a credited share above plain's with the interval excluding 0 on the
high side AND fewer places shown, and the check reports must not contradict it. Showing everything
(the union) trivially finds more with a share of 10%, so it does not count: fewer places shown is
required. At most three rounds and $3 in all; each round is one named change, committed before it runs.
Whatever comes out is tuned on dev and is not evidence the verifier works on new reports.

Result of the v3 iteration (2026-11-06, dev, flash, tuned on the key, not quotable). Round 1 (v3 prompt,
every flag verified on its own): the verifier calls 72% of flags valid, so showing its valid places
shows 140 places against plain's 142 (union 196) with a credited share of 13.0% and 17.6 findings
found against plain's 13.2. The rule as written is met on the wide key only by one place, fails on the
original key, and the check reports (Arcade, Radius) show 15 more places than plain at no better
share, so it is not counted. Round 2 (a severity cutoff on that output, no model call): valid at
severity low or up shows 116 places with a credited share of 14.8% (+5.9% [+1.5%, +7.3%] over plain)
and 16.6 findings found against 13.2 (+3.4 [+0.6, +7.2]); the original key gives +2.3% [+0.2%,
+5.3%] and +0.4 [-0.6, +1.8], places shown -25. The rule is met on both keys, by the letter and weakly.
Against the plain union it shows 41% fewer places for 9% fewer findings. On the check reports the
share (+1.3%) and findings (+1.2) are above plain's but the list is 9 places longer, and on the
original key the tune reports find 0.2 fewer than plain. The cutoff was one of seven tried on dev; v3
mostly rates severity, since `claim_true` is yes for most flags; and none of this was tested on a
report nobody has read. Round 3 was not run (balance $1.93).

