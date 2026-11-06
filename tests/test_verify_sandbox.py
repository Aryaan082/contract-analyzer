"""Verifying one flag end to end with scripted models and a stub runner: no model, no forge."""

import pytest

from review.client import ModelUnavailable
from tests.verify_common import (
    COMPILE,
    FAILED,
    FILES,
    GOOD_TEST,
    PASSED,
    StubRunner,
    call,
    candidate,
    poc_reply,
    reader,
    verdict_reply,
    writer,
)
from verify import sandbox as sb
from verify.poc import hash_of_test
from verify.runner import RunResult
from verify.types import Credit

BAD_TEST = GOOD_TEST.replace(
    "assertGt(", "vm.store(address(vault), bytes32(0), bytes32(0));\n assertGt("
)


def run(w, r, runner, **kw):
    return sb.verify_candidate(
        candidate(), FILES, writer=w, reviewer=r, runner=runner, solc="0.8.20", **kw
    )


def test_a_test_that_passes_and_is_accepted_confirms_the_flag_and_names_the_test():
    w, r, runner = (
        writer([poc_reply()], cost_usd=0.01),
        reader([verdict_reply("accept")], cost_usd=0.002),
        StubRunner(PASSED),
    )
    out = run(w, r, runner)
    assert out.verdict.status == "confirmed" and out.verdict.attempts == 1
    assert (
        out.verdict.credit() == Credit("sandbox", hash_of_test(GOOD_TEST))
        and out.test_file == GOOD_TEST
    )
    assert out.calls == 2 and out.cost_usd == pytest.approx(0.012)
    project = runner.projects[0]
    assert project.test_file == GOOD_TEST and project.solc == "0.8.20" and project.files == FILES


def test_the_reviewer_must_be_from_another_company_than_the_writer():
    same = writer([poc_reply()])
    other_deepseek = type(same)([verdict_reply()], name="deepseek/deepseek-v4-pro")
    with pytest.raises(ValueError, match="another company"):
        run(same, other_deepseek, StubRunner(PASSED))


def test_a_reviewer_who_rejects_a_passing_test_leaves_the_flag_not_confirmed():
    w, r, runner = writer([poc_reply()]), reader([verdict_reply("reject")]), StubRunner(PASSED)
    out = run(w, r, runner, max_attempts=2)
    assert out.verdict.status == "not_confirmed" and out.verdict.credit() is None
    assert out.verdict.reason == "the second reader rejected the test" and len(runner.projects) == 2
    assert "second reader did not accept" in w.prompts[1].user


def test_a_test_that_breaks_a_text_rule_is_never_run_and_the_next_attempt_is_told_why():
    w = writer([poc_reply(BAD_TEST), poc_reply()])
    runner = StubRunner(PASSED)
    out = run(w, reader([verdict_reply()]), runner)
    assert out.verdict.status == "confirmed" and out.verdict.attempts == 2
    assert (
        len(runner.projects) == 1 and runner.projects[0].test_file == GOOD_TEST
    )  # only the good one ran
    assert "refused before running" in w.prompts[1].user and "uses vm.store" in w.prompts[1].user


def test_a_compile_error_is_shown_to_the_writer_on_the_next_attempt():
    w = writer([poc_reply(), poc_reply()])
    out = run(w, reader([verdict_reply()]), StubRunner(COMPILE, PASSED))
    assert out.verdict.status == "confirmed" and out.verdict.attempts == 2
    again = w.prompts[1].user
    assert "EARLIER TEST" in again and "Undeclared identifier" in again


def test_a_test_that_keeps_failing_ends_not_confirmed_after_the_attempts_allowed():
    w, runner = writer([poc_reply()]), StubRunner(FAILED)
    out = run(w, reader([verdict_reply()]), runner, max_attempts=3)
    assert (out.verdict.status, out.verdict.attempts) == ("not_confirmed", 3)
    assert out.verdict.reason == "the test failed" and len(runner.projects) == 3
    assert out.verdict.test_sha == hash_of_test(GOOD_TEST)


def test_a_writer_that_says_a_test_cannot_show_it_makes_the_flag_unverifiable():
    w = writer(['{"feasible": false, "why_not": "needs a governance vote"}'])
    runner = StubRunner(PASSED)
    out = run(w, reader([verdict_reply()]), runner)
    assert out.verdict.status == "unverifiable" and "cannot show it" in out.verdict.reason
    assert (
        not runner.projects and "governance" not in out.verdict.reason
    )  # the model's words are not kept


def test_a_writer_that_gives_no_usable_reply_makes_it_unverifiable():
    for script in (
        ["words", "more words"],
        [ModelUnavailable("no credit")],
        [call(poc_reply(), finish_reason="length")],
    ):
        out = run(writer(script), reader([verdict_reply()]), StubRunner(PASSED))
        assert (
            out.verdict.status == "unverifiable"
            and out.verdict.reason == "the writer gave no usable reply"
        )


def test_a_missing_tool_ends_it_at_once_as_unverifiable():
    runner = StubRunner(RunResult("tool_missing", "the compiler is not installed"))
    out = run(writer([poc_reply()]), reader([verdict_reply()]), runner)
    assert out.verdict.status == "unverifiable" and len(runner.projects) == 1


def test_a_flag_in_a_file_outside_the_bundle_is_unverifiable_without_a_call():
    w = writer([poc_reply()])
    out = sb.verify_candidate(
        candidate(file="src/Elsewhere.sol"), FILES, writer=w, reviewer=reader([verdict_reply()]),
        runner=StubRunner(PASSED), solc="0.8.20",
    )  # fmt: skip
    assert out.verdict.status == "unverifiable" and not w.prompts


def test_remappings_become_allowed_import_roots():
    oz = GOOD_TEST.replace('"src/Vault.sol"', '"@openzeppelin/contracts/access/Ownable.sol"')
    w = writer([poc_reply(oz)])
    runner = StubRunner(PASSED)
    out = run(w, reader([verdict_reply()]), runner, remappings=("@openzeppelin/=lib/oz/",))
    assert out.verdict.status == "confirmed" and runner.projects[0].remappings == (
        "@openzeppelin/=lib/oz/",
    )
    refused = run(
        writer([poc_reply(oz)]), reader([verdict_reply()]), StubRunner(PASSED), max_attempts=1
    )
    assert (
        refused.verdict.status == "not_confirmed"
        and refused.verdict.reason == "the test broke a rule"
    )
