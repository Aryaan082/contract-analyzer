"""A second reading of every flag the source reviewers raise (Aryaan, 2026-11-06).

A flag is only a sentence about a place. `verify.reason` re-reads it with a different model, using
Trail of Bits' definitions of a finding (`verify.definitions`): the model says whether the code
does what the claim says and how severe it is, and the harness works the verdict out from those
two answers. `verify.stage` runs a report's places through the funnel and splits the output for a
person (`review_list`); `verify.queue` holds what a person approves before anything reaches the
directory.

The funnel (`verify.funnel`) credits a flag that matches a known audit finding, then one a
deterministic rule confirms (`verify.mechanical`); everything else goes to the verifier.

Nothing is dropped: a place that is not shown stays in the output with its verdict. A model's
sentences are untrusted text and are never stored in a record of ours.
"""
