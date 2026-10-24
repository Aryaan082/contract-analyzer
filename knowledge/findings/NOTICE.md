# Notice for the findings in this folder

The JSON files here are adapted from public security reviews by **Trail of Bits**, published in
<https://github.com/trailofbits/publications> under the **Creative Commons Attribution-ShareAlike
4.0 International** licence (CC BY-SA 4.0, <https://creativecommons.org/licenses/by-sa/4.0/>). Each
file names its source report, the pinned commit, the file and its SHA-256 in its `source` field.

Changes made: each report was split into one record per finding; only findings rated critical, high,
medium or undetermined by the auditors and judged exploitable were kept (the others are listed by ID, title and
reason in the `excluded` section of each file, see `knowledge/exploit_review.toml`); only the
description and the recommendations were kept (Exploit Scenario and References sections, code listings, page footers
and appendices were left out); text was normalised (ligatures, spacing, hidden characters); the
`taxonomy` tags are our own annotation and are not part of the original reports.

The records in this folder are shared under the same licence, CC BY-SA 4.0. The original reports
remain the authoritative source: where a record and its report differ, the report is right.
