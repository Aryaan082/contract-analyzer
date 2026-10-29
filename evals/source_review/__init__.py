"""The evaluation kit for source review (R5): free, offline, and honest about what it cannot show.

    cards.py     dataset cards (labels and references only, never third-party source text), loader
    split.py     the dev / held-out split of approach note 6.5, fixed in a file before any run
    scoring.py   pure functions over `review.reviewer.ReviewResult`s: the metrics of note 6.3
    hostile.py   the hostile suite (H1 to H22): gate 1 with a scripted reviewer, gate 2 with a model
    planted.py   planted variants: new code, one labelled issue each, a safe twin, a renamed copy
    costs.py     the T1, T2 and T2b cost estimates of note 6.6, from the numbers in 4.5
    dryrun.py    everything above end to end with the scripted reviewer
    cli.py       `python -m evals.source_review <command>`

Nothing in this package calls a model unless `hostile.run_t1` is asked to, and it refuses to be
asked without `--confirm-spend` and `--max-spend`.
"""
