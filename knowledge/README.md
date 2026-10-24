# knowledge/: the vulnerability taxonomy and the audit findings

Reading material for the agent, in two parts that carry stable IDs so that retrieval, the agent's
citations and the evals can all point at the same things.

- **Taxonomy** (`taxonomy/*.toml`): 35 hand-written entries, one per class of problem that can lose
  funds or hand over control (reentrancy, a blocked sell, an upgradeable proxy, ...). ID:
  `taxonomy:<slug>`.
- **Findings** (`findings/*.json`): 19 records, the findings of 11 public audit reports by Trail
  of Bits (125 read) that were rated high, medium or undetermined **and** judged to be exploitable. ID:
  `<report slug>:<finding id>`, for example `aave-v3-tob-2021-11:TOB-AAVE-002`, cited with its
  page. The other 106 are listed by ID, title and reason in each file's `excluded` section.

The taxonomy says what a kind of problem is and which tool results point to it. The findings are
specific instances an auditor wrote up. Neither is evidence about the contract being analysed: the
tools produce the facts, this is the context the agent cites for what they mean.

```text
python -m knowledge fetch [--record]      download the PDFs listed in manifest.json and check them
python -m knowledge parse [--only SLUG] [--held-out | --not-held-out] [--dry-run]
                                          PDFs -> findings/*.json, each checked against its report
python -m knowledge tag [--untagged]      tag findings with taxonomy entries (tagging_rules.toml)
python -m knowledge coverage              taxonomy entries that no kept finding illustrates yet
python -m knowledge check                 validate the committed taxonomy and findings
python -m knowledge spotcheck             write spotcheck.html: the records beside their PDF pages
```

Python access: `load_taxonomy()`, `taxonomy_index()`, `entries_for_signals(signals, kinds)` in
`knowledge/taxonomy.py`; `load_findings()`, `all_findings()` in `knowledge/findings.py`. Tests need
no network, no PDFs, no `.env`, no Postgres, no Foundry.

## Where the findings come from

`manifest.json` lists the reports: the source repository pinned to one commit, and for each report
its file, size, git blob hash and SHA-256. `fetch` downloads only from `raw.githubusercontent.com`
and rejects any file that does not match, so the same bytes can be fetched again by anyone. The
PDFs go in `reports/`, which is git-ignored: they are third-party documents, so they are not
redistributed here, only identified. Trail of Bits publishes them under **CC-BY-SA-4.0**; the
findings files are an adaptation (split per finding, only the exploitable ones kept, exploit
scenarios and code listings left out),
so `findings/NOTICE.md` carries the credit and the same licence. (My reading of the licence, not
legal advice.)

| Report | Date | Read | Kept |
|---|---|---|---|
| Uniswap v4 Core | 2024-09 | 6 | 0 |
| Aave V3 | 2021-11 | 15 | 5 |
| Aave V4 | 2026-02 | 7 | 2 |
| Balancer v3 | 2024-12 | 21 | 4 |
| Compound Protocol (file `compound-3.pdf`: an August 2019 follow-up to the April 2019 review of Compound 2.0) | 2019-08 | 5 | 0 |
| Frax Finance: Fraxlend and Fraxferry | 2022-11 | 13 | 0 |
| Maple Finance v2 | 2022-12 | 12 | 1 |
| Reserve Protocol | 2022-08 | 15 | 2 |
| Wormhole Governor and Watchers (the node software, written in Go) | 2023-04 | 17 | 0 |
| Morpho Protocol V1 | 2022-06 | 8 | 3 |
| Treehouse tETH | 2024-10 | 6 | 2 |

Read, by the auditors' severity: 17 high, 15 medium, 33 low, 50 informational, 10 undetermined.
Kept: 9 high, 7 medium, 3 undetermined. The Compound report is an incremental assessment that continues an earlier
numbering (findings 24 to 28), and Wormhole is mostly Go code-quality findings.

### Which findings are kept

The agent should warn about problems someone can actually use, not about every observation an
auditor made, so two gates decide what is written to `findings/` (`selection.py`):

1. **Severity**: only findings the auditors rated critical, high or medium, or marked
   undetermined (they could not rate it, so it is judged on what it describes), are considered.
   Low and informational ones (83 of 125) are excluded on severity alone.
2. **Exploit**: each of the 42 findings that pass the gate has a decision and a one-line reason in
   `exploit_review.toml`, made by reading it. A finding is an exploit if someone can use the flaw
   without being trusted by the protocol to steal or lock funds, mint or borrow beyond the limits,
   avoid paying what they owe, take control or deliberately stop a core function, or if it lets a
   privileged party take users' funds (the rug-pull case). Process best practices (two-step
   ownership), accounting that drifts on its own, an admin's possible mistake, a third party's
   configuration breaking a workflow, and documentation or test gaps are not. 23 of the 42 were
   judged not to be exploits.

Nothing is dropped silently: each excluded finding stays in its file's `excluded` section with its
ID, severity, title and reason (`below_severity`, or `not_an_exploit` plus the reviewer's reason),
so the report's table can still be matched row by row and the completeness check below covers all
of them. A finding that passes the severity gate but has no decision fails the build, and so does a
decision for a finding that does not exist. The decisions are one reader's judgements, written to be
argued with: change `exploit` for a finding and re-run `python -m knowledge parse` (needs the PDFs)
to move it in or out; the severity gate is `REVIEWED_SEVERITIES` in `selection.py`. A few
excluded ones look exploitable but were rated low (for example Balancer's pool-registration and
permit front-running), so for those the severity gate is the stricter of the two.

### What a record keeps

`protocol, auditor, date, finding_id, severity, title, affected_contract (the report's Target),
description, recommendation, page, url`, plus `category` and `difficulty` (the auditor's labels),
`page_end`, and `taxonomy` (our tags). **Kept in a record**: the description and recommendation text.
**Dropped**: Exploit Scenario sections, References sections, code listings (a
`[code listing omitted]` marker is left, the page number points to the code), page footers, and the
fix-review appendices that repeat the findings. `page` is the physical PDF page where the finding
starts, so `finding.cite_url` (the pinned PDF plus `#page=N`) opens the right page; printed page
numbers differ because of front matter.

Text from a PDF is untrusted. Every text field is validated on the way in and again when a file
is loaded: no control, invisible or bidi characters, no leftover decode failures, bounded length,
fixed vocabularies for severity, a pattern for IDs. A record is quoted evidence; whoever puts one
into a prompt must present it as data, never as instructions.

## How a record is known to be complete

The parser (`parse/tob.py`, rule-based, no model) reads three independent counts out of each
report, over **all** its findings kept or not, and a report is only written if they agree
(`parse/check.py`; enforced again every time a file is loaded, where kept and excluded findings
together must account for every table row and every stated severity total):

1. the rows of the report's own findings table (each with its severity),
2. the findings read from the detailed section, each matched to its row by number and severity,
3. the per-severity totals the report states near the front (an exposure table, or "Total
   High-Severity Issues 5" in the older template).

It also fails on page footers or undecodable characters left in the text, empty descriptions or
recommendations, and numbering gaps. A report that does not follow the structure at all (for
example a summary report with a table but no detailed findings) is rejected with a message. The
OpenType ligature glyphs some fonts do not map ("Insu?cient") are repaired from words seen
elsewhere in the same document, and anything that cannot be repaired fails the check.

Two things the check cannot see are the **hand spot-check** (below) and wording inside a finding:
one known artifact is the split word "u se" in `uniswap-v4-core-tob-2024-09:TOB-UNI4-2`, which is
in the PDF's own text runs.

### Spot-check by hand

`python -m knowledge spotcheck` writes `spotcheck.html` (git-ignored). Open it in a browser: the
record is on the left with a tick box per property, and the PDF opens at the record's page on the
right. "Copy results" gives a text summary. The spec asks for 20 records; with 19 kept, the sheet
shows all 19 (a sample is one finding from every report that has one, then random ones from a fixed
seed, so it is not picked by whoever wants it to pass). The first record was checked in the app's
browser: the page renders, handles text safely (page text is set with `textContent`, never as
HTML) and the PDF pane opened at the right page. In the app, open it with the `knowledge-spotcheck`
preview entry (`.claude/launch.json` runs `python3 -m http.server` for the `knowledge/` folder on
port 8766, then go to `http://localhost:8766/spotcheck.html`). Opened straight from disk the app's
preview shows only a static snapshot with an empty PDF pane; an ordinary browser works, and there is
an "open it in a tab" link above the pane.

### How well the parser generalises

The parser was built against 8 reports (Uniswap v4, Aave V3, Balancer v3, Frax, Maple, Reserve,
Wormhole, Treehouse). Seven passed all three checks on their first run; Aave V3 uses an older
template (type sizes, "Findings Summary" and "Total ... Issues" wording) which was then supported.
Four reports were set aside as a blind test, run once after the parser was final:

- **Morpho V1**: passed on the first run, nothing changed.
- **Compound 2019**: failed the first run on three things, none of which was a misread finding
  (its 5 findings were extracted correctly): a numbering rule that wrongly required findings to
  start at 1 (this report continues an earlier assessment's numbering), a wording variant of the
  stated totals, and one unmapped ligature. All three were fixed afterwards, so this report is
  no longer blind.
- **Aave V4** was run once by accident during development (a file pattern included it); it passed
  with no change, but the footer handling was generalised after its output was seen.
- **PYUSD (Paxos)** was likewise seen once, as a parse error, and then dropped: it is a "summary
  report" that lists its findings in a table but has no descriptions or recommendations, so no
  complete record can be made. It is not in the manifest. A report about a token with supply and
  freeze controls would be a good replacement; adding one is a manifest entry.

So: one clean blind pass, one blind pass that exposed three fixable gaps, two reports that were
not blind. The manifest's `held_out` flag marks the two reports that stayed blind until the test.

## The taxonomy

Every entry is a way to lose funds or hand over control. An earlier draft also had
`no-timelock-on-admin-actions` and `nonstandard-erc20`; they were removed before anything used them,
because they describe housekeeping and integration hazards rather than exploits (the first folds
into `single-key-admin`, and a token that deviates from ERC-20 is now a hint under
`honeypot-sell-blocked`).

An entry (see any file in `taxonomy/`) has: `title`, `group`, `summary` (one line, for the index),
`description` (what it is and why it matters to someone deciding whether to approve a contract),
`applies_to` (identify kinds, or `any`; a search filter that never hides anything when a
contract's kind is unknown), `visible_in`, `signals`, `opcodes`, `what_to_check` (next steps with
the tools), `look_for_in_code` (the checklist for reading code: the pattern, what makes it
vulnerable, the safe form, false alarms; at most 250 words), optional `source_keywords` (hints,
never a gate), optional `example_vulnerable` and `example_safe` (the shape of the risky code and its
fix; fragments only, never an attacker contract or attack steps), and `references` (stable
standards and documentation only; each link was checked once with a HEAD request).

- `signals` **route**: they are the words the tools emit (`risk.kind=mint`,
  `sell.verdict=sell_blocked`, `identify.summary=lookalike`, `registry.status=...`,
  `identify.kind=...`, `identify.proxy=...`, `match.quality=...`, and fields such as
  `sell.sell_tax_bps`), so `entries_for_signals()` is an exact lookup, not a search.
  `tests/test_knowledge_taxonomy.py` reads the tools' own type definitions (`vocabulary.py`) and
  fails if any signal is a word no tool can say, and checks that every risk signal kind and the
  blocked verdicts lead to some entry.
- `visible_in` says where the problem itself can be **confirmed**: `tools`, `bytecode`, `source`,
  or `external` (data the tools do not fetch today, such as who holds the pool's LP tokens or who
  owns an admin address). About half the entries are not visible to any tool yet: for those the
  agent must report "not checked", never "not found". The `opcodes` lists name what a bytecode scan
  would look for (ORIGIN, CALLCODE, CREATE2, block-data opcodes); today's scan emits only
  DELEGATECALL and SELFDESTRUCT, so the rest are specifications for a later extension.
- IDs are the file name. They never change; to retire an entry, keep the file and say so in it.

### Cross-check against the OWASP Smart Contract Top 10

The taxonomy was compared once with the [OWASP Smart Contract Top 10
(2025)](https://owasp.org/projects/smart-contract-top-10) to check that no major class of common
exploit was missing. Eight of the ten were already covered; `logic-errors` (SC03) and
`flash-loan-attacks` (SC07) were added. Nothing in the data refers to OWASP: it is not a field and
not a test, only a check on how complete the list is.

### Entries with no audit example yet

`python -m knowledge coverage` lists the entries that no kept finding is tagged with: 20 of 35 today.
Nine are token tricks (honeypots, hidden taxes, blacklists, ...), which audits of honest projects
rarely cover. The rest are classes where the next reports should be chosen to include examples. Of
OWASP's ten, **price oracle manipulation (SC02), flash loan attacks (SC07) and insecure randomness
(SC09) have none**, and neither do front-running, governance takeover, bridge message validation,
ERC-4626 share inflation, delegatecall, storage layout, tx.origin and external dependencies. Tags
come from keyword rules, so a gap can also be a rule that missed; `python -m knowledge tag
--untagged` lists the kept findings with no tag.

### Tags on findings

`tagging_rules.toml` holds plain patterns (title and text; the auditor's category for one rule)
that tag a finding with taxonomy entries. 17 of the 19 kept findings carry a tag. Tags favour
precision over coverage and are a hint for retrieval, never evidence. Re-run with
`python -m knowledge tag`; no PDF is needed. Tags were reviewed by eye, not measured.

## Growing the corpus

The plan is to process all Trail of Bits audits and keep every high and medium issue, and later
other firms' reports. What is already in place, and what each step needs:

- **Adding a Trail of Bits report** is one manifest entry (file, size, git blob hash; `fetch --record`
  fills the SHA-256) followed by `fetch` and `parse`. The `reviews/` folder of the publications
  repository holds about 460 PDFs, but that includes fix reviews, letters of attestation, threat
  models and non-blockchain work, so the list of smart-contract reports has to be chosen, and this
  is a decision per report. The commit pin means the set never changes under you.
- **A new layout fails loudly, not quietly.** Two layouts are handled (the template from
  2022 onward, and an older one seen in 2019 and 2021 reports in two wordings); a summary report is rejected; any report whose table, detailed findings and stated
  totals disagree is not written. Expect a few reports to need a parser tweak each, as Aave V3 and
  Compound 2019 did.
- **The exploit review is the part that does not scale by hand.** Every finding that passes the
  severity gate needs a decision in `exploit_review.toml`, and a missing one fails the build, so a
  new report cannot slip in unread. At a few hundred reports that is thousands of decisions. The
  options are triage rules for the obvious non-exploits (two-step ownership, documentation,
  events), my review of the rest, or keeping every high and medium finding and storing the exploit
  decision as a flag to filter on instead of dropping findings. That choice is open.
- **Other firms** (for example Spearbit, Code4rena, OpenZeppelin, ConsenSys Diligence) each need
  their licence checked before anything is committed (Spearbit's repository states none) and their
  own parser, because every firm lays its reports out differently. `parse/check.py` and the
  findings model are firm-independent; `parse/tob.py` is not. IDs already include the auditor in the
  slug (`...-tob-...`), so reports from several firms can sit side by side.
- **Size**: findings files are about 10 KB per report and the PDFs are git-ignored, so storage is
  not the constraint; the PDFs for all of the publications repository would be a few hundred MB.

## Limits worth knowing

- One auditor, one writing style. Audit reports are about honest projects, so they cannot show what
  a honeypot looks like: the token-trick entries rest on the taxonomy text and on what the tools
  measure, and the findings feed the code-bug, admin-power and upgradeability entries.
- Nineteen findings is a small corpus, and four of the eleven reports (Uniswap v4, Compound 2019,
  Frax, Wormhole) contribute none, which is the filter working as asked. Adding reports with more
  high-severity findings is a manifest entry each. The numbers above are corpus facts, not quality
  measures; retrieval metrics come from Component 4 and the evals.
- `evals/results.md` does not exist on this branch; the blind-test results above are the only
  experiment logged here and should become a row there when Component 6 creates the file.
