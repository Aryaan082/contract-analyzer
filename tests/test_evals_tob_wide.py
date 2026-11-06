"""The wide key: the Low and Informational findings as a secondary, post-hoc view of the dev side.
No judge and no model is called anywhere here; the pairs and verdicts are put in a cache by hand."""

import argparse
import dataclasses
from types import SimpleNamespace

import pytest

from evals.judge.cache import JudgeCache
from evals.source_review import compare as cmp
from evals.source_review import tob_eval_cli as cli
from evals.source_review import tob_items as ti
from evals.source_review import tob_judge as tj
from evals.source_review import tob_report as tp
from evals.source_review import tob_run as tr
from evals.source_review import tob_wide as tw
from evals.source_review.compare import Source
from evals.source_review.hostile import make_bundle
from tests.test_evals_tob_eval import (
    CODE,
    REF,
    J,
    build,
    flag,
    label,
    put,
    reports,
    run_cli,
    screened_cache,
)


@pytest.fixture(scope="module")
def rubric() -> tj.Rubric:
    return tj.load_rubric()


def two_file_source() -> Source:
    return Source(make_bundle([("src/Vault.sol", CODE), ("src/Other.sol", CODE)]), None)


def wide_setup():
    """One report with a pre-registered finding and a Low one (both in src/Vault.sol) and two
    flags: one in src/Vault.sol (F1) and one in src/Other.sol (F2)."""
    orig, extra = label("T-1"), label("L-1", severity="low")
    refs = {lab.id: tj.Reference(lab.id, "t", lab.files, "d") for lab in (orig, extra)}
    record = {
        "item": "r",
        "complete": True,
        "flags": [flag(note="in", file_id="F1"), flag(note="out", file_id="F2")],
    }
    srcs = {"r": two_file_source()}
    return orig, extra, refs, {"r": [orig, extra]}, {"r": record}, srcs


@pytest.mark.parametrize(
    ("severity", "scoped"),
    [
        ("high", False),
        ("medium", False),
        ("undetermined", False),
        ("low", True),
        ("Informational", True),
    ],
)
def test_only_low_and_informational_labels_are_judged_against_their_own_files(severity, scoped):
    assert label("T-1", severity=severity).file_scoped is scoped


def test_an_extra_finding_is_paired_only_with_flags_in_its_files(rubric):
    orig, extra, refs, labels_of, records, srcs = wide_setup()
    jobs = tr.judge_jobs(rubric, J[0], refs, labels_of, records, srcs, "pipeline")
    by_label = {lab.id: [j for j in jobs if j.meta["label"] == lab.id] for lab in (orig, extra)}
    assert len(by_label[orig.id]) == 2  # the pre-registered key: every flag, as before
    assert len(by_label[extra.id]) == 1  # the extra finding: the flag in its file only


def test_an_out_of_scope_pair_is_neither_a_gap_nor_a_find(rubric):
    orig, extra, refs, labels_of, records, srcs = wide_setup()
    claims = tr.claims_of(records["r"], srcs["r"])
    in_file = next(c for c in claims if c.file == "src/Vault.sol")
    cache = JudgeCache()
    for lab in (orig, extra):
        for c in claims:
            if lab is extra and c is not in_file:
                continue  # never judged, by design
            for judge in J:
                put(cache, rubric, refs[lab.id], c, judge, "same" if c is in_file else "unrelated")
    v = tr.verdicts(rubric, J, refs, labels_of, records, srcs, cache)
    assert v[("r", extra.id)].found and v[("r", extra.id)].unjudged == 0
    assert v[("r", orig.id)].found and v[("r", orig.id)].unjudged == 0


def test_a_missing_answer_is_still_a_gap_for_a_pre_registered_finding(rubric):
    orig, extra, refs, labels_of, records, srcs = wide_setup()
    claims = tr.claims_of(records["r"], srcs["r"])
    cache = JudgeCache()
    for judge in J:
        put(cache, rubric, refs[orig.id], claims[0], judge, "unrelated")  # claims[1] unanswered
    v = tr.verdicts(rubric, J, refs, {"r": [orig]}, records, srcs, cache)
    assert v[("r", orig.id)].unjudged == 1


def test_unmatched_flags_ignore_extra_findings_outside_their_files(rubric):
    orig, extra, refs, labels_of, records, srcs = wide_setup()
    claims = tr.claims_of(records["r"], srcs["r"])
    cache = JudgeCache()
    for c in claims:
        for judge in J:
            put(cache, rubric, refs[orig.id], c, judge, "unrelated")
            if c.file == "src/Vault.sol":
                put(cache, rubric, refs[extra.id], c, judge, "unrelated")
    flags, unmatched = tr.unmatched_flags(
        rubric, J, refs, labels_of, records["r"], srcs["r"], cache
    )
    assert (flags, unmatched) == (
        2,
        2,
    )  # the flag in src/Other.sol is not held up by the extra finding


def raw(fid, severity, target, number=1, page="12"):
    return SimpleNamespace(
        finding_id=fid, number=number, severity=severity, title=f"title {fid}", target=target,
        category="Data Validation", difficulty="Low", page=page, description=f"description {fid}",
    )  # fmt: skip


def fake_pdf(monkeypatch, findings):
    monkeypatch.setattr(
        ti, "read_pdf", lambda path: SimpleNamespace(report=SimpleNamespace(findings=findings))
    )


def test_extra_labels_are_the_low_and_informational_findings_that_name_a_file_of_the_code(
    monkeypatch, tmp_path
):
    fake_pdf(
        monkeypatch,
        [
            raw("TOB-R-1", "High", "src/Vault.sol", 1),
            raw("TOB-R-2", "Low", "contracts/src/Vault.sol", 2),  # a path: the file name matches
            raw("TOB-R-3", "Informational", "", 3),  # no target: nothing to match against
            raw("TOB-R-4", "Low", "test/Vault.t.sol", 4),  # not a file of the bundle
            raw("TOB-R-5", "Informational", "src/Other.sol, src/Vault.sol", 5, page=""),
        ],
    )
    labels, skipped = tw.extra_labels(
        tmp_path, reports("r"), {"r": ["src/Vault.sol", "src/Other.sol"]}
    )
    assert [lab.id for lab in labels] == ["r:TOB-R-2", "r:TOB-R-5"]
    assert skipped == ["r:TOB-R-3", "r:TOB-R-4"]
    first, second = labels
    assert (first.severity, first.files, first.page) == ("low", ("src/Vault.sol",), 12)
    assert second.files == ("src/Vault.sol", "src/Other.sol") and second.page == 0
    assert all(lab.file_scoped for lab in labels)


def test_widen_adds_the_extra_findings_to_labels_and_references(monkeypatch, tmp_path):
    fake_pdf(monkeypatch, [raw("TOB-R-2", "Low", "src/Vault.sol", 2)])
    monkeypatch.setattr(
        tr, "descriptions", lambda cache, rs: {"r:TOB-R-2": "what the auditors said"}
    )
    ctx = cli.Context(tmp_path, "dev", reports("r"), {"r": [label("T-1")]},
                      {"r": two_file_source()}, {"r:T-1": REF})  # fmt: skip
    wide = tw.widen(ctx)
    assert [lab.id for lab in wide.labels_of["r"]] == ["r:T-1", "r:TOB-R-2"]
    assert wide.refs["r:TOB-R-2"].description == "what the auditors said"
    assert wide.refs["r:T-1"] is REF and wide.key == "wide"
    assert [lab.id for lab in ctx.labels_of["r"]] == ["r:T-1"]  # the original is untouched


def test_the_held_out_side_has_no_wide_key(monkeypatch, tmp_path):
    ctx = cli.Context(tmp_path, "held_out", reports("r"), {}, {}, {})
    with pytest.raises(ValueError, match="dev side only"):
        tw.widen(ctx)
    for command in ("judge", "report"):
        code, text = run_cli(
            monkeypatch, tmp_path,
            [command, "--cache", str(tmp_path), "--side", "held_out", "--key", "wide",
             "--mode", "quick"],
        )  # fmt: skip
        assert code == 2 and "dev side only" in text


def test_the_held_out_wide_key_is_judged_only_with_the_explicit_exploratory_option(
    monkeypatch, tmp_path
):
    fake_pdf(monkeypatch, [raw("TOB-R-2", "Low", "src/Vault.sol", 2)])
    monkeypatch.setattr(tr, "descriptions", lambda cache, rs: {"r:TOB-R-2": "text"})
    ctx = cli.Context(tmp_path, "held_out", reports("r"), {"r": [label("T-1")]},
                      {"r": two_file_source()}, {"r:T-1": REF})  # fmt: skip
    wide = tw.widen(ctx, exploratory_held_out=True)
    assert [lab.id for lab in wide.labels_of["r"]] == ["r:T-1", "r:TOB-R-2"]
    with pytest.raises(ValueError, match="dev side only"):
        tw.widen(ctx)
    args = argparse.Namespace(side="held_out", key="wide")
    assert "dev side only" in cli.wide_problem(args)
    assert cli.wide_problem(argparse.Namespace(side="held_out", key="wide",
                                               exploratory_held_out=True)) == ""  # fmt: skip
    assert cli.wide_problem(argparse.Namespace(side="held_out", key="orig")) == ""
    # `report` has no such option (argparse exits), and without it the held-out wide key is refused
    with pytest.raises(SystemExit):
        run_cli(
            monkeypatch, tmp_path,
            ["report", "--cache", str(tmp_path), "--side", "held_out", "--key", "wide",
             "--mode", "quick", "--exploratory-held-out"],
        )  # fmt: skip


def test_a_wide_report_says_so_names_its_file_so_and_files_no_run_records(monkeypatch, tmp_path):
    screened_cache(tmp_path)
    out_dir = tmp_path / "out"
    real = tp.write_report
    monkeypatch.setattr(tp, "write_report", lambda report, side: real(report, side, out_dir))
    monkeypatch.setattr(cmp, "RESULTS_DIR", out_dir)
    monkeypatch.setattr(
        tw, "widen",
        lambda ctx: dataclasses.replace(ctx, key="wide", wide_skipped=("r:TOB-R-3",)),
    )  # fmt: skip
    argv = ["report", "--cache", str(tmp_path), "--side", "dev", "--mode", "quick",
            "--key", "wide", "--allow-partial"]  # fmt: skip
    code, text = run_cli(monkeypatch, tmp_path, argv)
    assert code == 0 and "WIDE KEY (post-hoc, dev only" in text and "r:TOB-R-3" in text
    assert sorted(p.name for p in out_dir.iterdir() if p.suffix == ".md")[0].endswith("-wide.md")
    assert not list(out_dir.glob("*.jsonl"))  # the run records are the original report's


def test_the_wide_report_file_never_overwrites_the_original(rubric, tmp_path):
    report = build(rubric)
    js0, _ = tp.write_report(report, "dev", tmp_path)
    wide = {**report, "meta": {**report["meta"], "key": "wide", "wide_skipped": []}}
    js1, md1 = tp.write_report(wide, "dev", tmp_path)
    assert js0.name == "tob-dev-2026-11-04.json" and js1.name == "tob-dev-2026-11-04-wide.json"
    assert js0.exists() and "WIDE KEY" in md1.read_text()
    assert md1.read_text().index("WIDE KEY") < md1.read_text().index("## Headline")
    assert "WIDE KEY" not in tp.results_row(report) and "WIDE KEY" in tp.results_row(wide)
