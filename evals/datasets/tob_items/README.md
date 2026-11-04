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
