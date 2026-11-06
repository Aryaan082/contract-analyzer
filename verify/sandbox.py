"""Verifying one flag: write a test, check its text, run it, have a second model read it
(Aryaan, 2026-11-05). The models and the runner are passed in, so this is tested with scripts.

Up to `max_attempts` rounds. In each: the writer model is asked for a test (the second round on
shows it the earlier test and what forge said); a reply that is unreadable is asked again once; a
test whose text breaks a rule (`verify.poc.check_test_text`) never runs; a test that compiles and
passes goes to the reviewer model, a model from another company than the writer's, and only an
`accept` confirms the flag.

The verdict only ever promotes. `not_confirmed` is "no working test in the attempts allowed", never
"the flag is false". The test's text is returned to the caller, who keeps it OUTSIDE the repo; the
verdict keeps only its hash.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from review.client import ModelUnavailable, ReviewerCall, ReviewerClient, company_of, retry_prompt
from review.pack import Prompt
from verify.poc import (
    POC_SCHEMA,
    REVIEW_SCHEMA,
    check_test_text,
    hash_of_test,
    parse_poc,
    parse_review,
    poc_prompt,
    review_prompt,
)
from verify.runner import ForgeRunner, Project
from verify.source import function_source, inheritance_files, outline
from verify.types import Candidate, Verdict

DEFAULT_ATTEMPTS = 3
WRITER_TOKENS = 6_000
REVIEW_TOKENS = 2_000  # room for a model that thinks before it answers


@dataclass
class Outcome:
    verdict: Verdict
    cost_usd: float = 0.0
    calls: int = 0
    test_file: str = ""  # the last test written; the caller keeps it outside the repo


def same_company(a: ReviewerClient, b: ReviewerClient) -> bool:
    return company_of(a.name) == company_of(b.name)


def _ask(client: ReviewerClient, prompt: Prompt, schema: dict, tokens: int, parse, out: Outcome):
    """One request, and one more if the reply is not the shape asked for. The parsed reply, or
    None if the model could not answer or answered badly twice."""
    for request in (prompt, retry_prompt(prompt)):
        try:
            reply: ReviewerCall = client.review(request, schema, max_tokens=tokens)
        except ModelUnavailable:
            return None
        out.calls += 1
        out.cost_usd += reply.cost_usd
        if reply.finish_reason != "length" and (got := parse(reply.text)) is not None:
            return got
    return None


def verify_candidate(
    candidate: Candidate,
    files: Mapping[str, str],
    *,
    writer: ReviewerClient,
    reviewer: ReviewerClient,
    runner: ForgeRunner,
    solc: str,
    remappings: tuple[str, ...] = (),
    deps: Path | None = None,
    max_attempts: int = DEFAULT_ATTEMPTS,
    evm_version: str = "",
    writer_tokens: int = WRITER_TOKENS,
    review_tokens: int = REVIEW_TOKENS,
) -> Outcome:
    """The verdict on one flag. Refuses to run with a reviewer from the writer's company: two
    models of one company share mistakes, which is what the second reading is there to catch."""
    if same_company(writer, reviewer):
        raise ValueError("the reviewer must be from another company than the writer")
    if candidate.file not in files:
        return Outcome(Verdict("unverifiable", "the flag's file is not in the bundle"))
    out = Outcome(Verdict("not_confirmed", "no working test in the attempts allowed"))
    roots = ("forge-std/", *(m.split("=", 1)[0] for m in remappings))
    others = outline(files, skip=candidate.file)
    bases = inheritance_files(files, candidate)
    function_code = function_source(files, candidate)
    previous: tuple[str, str] | None = None
    for attempt in range(1, max_attempts + 1):
        prompt = poc_prompt(candidate, files[candidate.file], others, remappings=remappings,
                            previous=previous, bases=bases)  # fmt: skip
        poc = _ask(writer, prompt, POC_SCHEMA, writer_tokens, parse_poc, out)
        if poc is None:
            out.verdict = Verdict("unverifiable", "the writer gave no usable reply", attempt)
            return out
        if not poc.feasible:
            out.verdict = Verdict("unverifiable", "the writer says a test cannot show it", attempt)
            return out
        out.test_file = poc.test_file
        sha = hash_of_test(poc.test_file)
        problems = check_test_text(poc.test_file, project_paths=set(files), import_roots=roots)
        if problems:
            previous = (
                poc.test_file,
                "The test was refused before running: " + "; ".join(problems),
            )
            out.verdict = Verdict("not_confirmed", "the test broke a rule", attempt, sha)
            continue
        result = runner.run(Project(files, poc.test_file, solc, remappings, deps, evm_version))
        if result.status == "tool_missing":
            out.verdict = Verdict("unverifiable", "a tool is missing on this machine", attempt, sha)
            return out
        if result.status != "passed":
            previous = (poc.test_file, result.tail or result.detail)
            out.verdict = Verdict("not_confirmed", f"the test {result.status.replace('_', ' ')}",
                                  attempt, sha)  # fmt: skip
            continue
        asked = review_prompt(candidate, poc.test_file, function_code, poc.harm)
        accepted = _ask(reviewer, asked, REVIEW_SCHEMA, review_tokens, parse_review, out)
        if accepted:
            out.verdict = Verdict("confirmed", "a test passed and a second model accepted it",
                                  attempt, sha)  # fmt: skip
            return out
        previous = (poc.test_file, "The test passed, but a second reader did not accept that it "
                    "shows the claim through a path open to an outsider.")  # fmt: skip
        reason = "no second reading" if accepted is None else "the second reader rejected the test"
        out.verdict = Verdict("not_confirmed", reason, attempt, sha)
    return out
