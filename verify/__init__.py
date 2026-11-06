"""Verifying a flag by running code (Aryaan, 2026-11-05).

A flag the reviewer raised is only a sentence about a place. Here a flag is checked by running
code: a model writes a Foundry test that asserts the bad outcome, and `forge test` runs it on a
local EVM, offline, in a throwaway folder. Which flags get that treatment is decided by a funnel
(`verify.funnel`), because simulating every flag is too much:

    1. a flag that matches an auditor's finding is credited by that report, no simulation;
    2. a flag a deterministic check can confirm is credited by that check (`verify.mechanical`);
    3. a cheap model call says whether the rest are testable logic bugs (`verify.triage`);
    4. the testable ones are ranked and capped per report, and simulated (`verify.poc`,
       `verify.runner`).

Everything not simulated stays in the output, marked unverified. Verification only promotes a flag;
a failed attempt never calls it false. The test code is untrusted model output and stays OUTSIDE
the repo: what is kept is a verdict, the flag's id and the test's hash (`verify.types`).
"""
