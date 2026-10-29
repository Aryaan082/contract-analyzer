import sys

from evals.source_review.cli import main

if sys.argv[1:2] == ["compare"]:  # the head-to-head has its own parser: compare_cli.py
    from evals.source_review.compare_cli import main as compare_main

    sys.exit(compare_main(sys.argv[2:]))
sys.exit(main())
