"""The shared review types: golden files, JSON Schema files and the rules the models enforce.

The golden JSON is what the web viewer's tests read, so a change to a model that is not mirrored in
the golden files and schema files fails here first.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from knowledge.findings import all_findings
from knowledge.taxonomy import load_taxonomy
from review import types as t

GOLDEN = Path(__file__).parent / "fixtures" / "review" / "golden"
SCHEMAS = GOLDEN / "schema"
VAULT = (GOLDEN / "Vault.sol").read_text()
COMPILER = "v0.8.20+commit.a1b79de6"

GOLDEN_FILES: dict[str, type[BaseModel]] = {
    "source_span.json": t.SourceSpan,
    "source_span_confirmed.json": t.SourceSpan,
    "source_span_scan.json": t.SourceSpan,
    "precedent_card.json": t.PrecedentCard,
    "source_file_view.json": t.SourceFileView,
    "review_summary_ran.json": t.ReviewSummary,
    "review_summary_refused.json": t.ReviewSummary,
    "reviewer_reply.json": t.ReviewerReply,
    "reviewer_reply_empty.json": t.ReviewerReply,
}


def load(name: str) -> dict[str, Any]:
    return json.loads((GOLDEN / name).read_text())


def span(**changes: Any) -> dict[str, Any]:
    """The golden reviewer span with some fields replaced, as the dict a consumer would send."""
    data = load("source_span.json")
    for key, value in changes.items():
        data[key] = value
    return data


# ---- golden files and schema files ------------------------------------------------------------


@pytest.mark.parametrize(("name", "model"), GOLDEN_FILES.items())
def test_golden_file_parses_and_round_trips_unchanged(name, model):
    data = load(name)
    assert t.to_json(model.model_validate(data)) == data


def test_every_golden_json_file_is_covered_by_this_test():
    on_disk = {p.name for p in GOLDEN.glob("*.json")}
    assert on_disk == set(GOLDEN_FILES)


@pytest.mark.parametrize("name", t.PUBLIC_MODELS)
def test_schema_file_matches_the_model(name):
    on_disk = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    assert on_disk == t.schema_for(name), (
        "a model changed: regenerate with `uv run python -m review.types "
        "tests/fixtures/review/golden/schema` and update the golden JSON"
    )


def test_no_stale_schema_files():
    assert {p.name for p in SCHEMAS.glob("*.json")} == {f"{n}.schema.json" for n in t.PUBLIC_MODELS}


def test_wire_keys_are_the_documented_ones_not_the_python_field_names():
    view = load("source_file_view.json")
    assert {"schema", "from", "to"} <= set(view)
    assert not {"schema_version", "from_line", "to_line"} & set(view)
    assert load("source_span.json")["schema"] == t.SCHEMA_VERSION


def test_golden_hashes_agree_with_the_demo_source():
    data = load("source_span.json")
    lines = VAULT.splitlines()
    assert data["file"]["sha256"] == t.sha256_hex(VAULT)
    # lines 8 to 10 hold no comment or string, so the stripped lines are the published lines
    assert data["range"]["span_sha256"] == t.span_sha256(lines[7:10])
    files = [
        t.SourceFile(
            file_id="F1", path="contracts/Vault.sol", content=VAULT, sha256=t.sha256_hex(VAULT)
        )
    ]
    assert data["subject"]["bundle_sha256"] == t.bundle_sha256(files, "solidity", COMPILER)


def test_golden_view_lines_are_the_demo_source_and_its_highlight_is_the_golden_span():
    view = t.SourceFileView.model_validate(load("source_file_view.json"))
    assert [x.text for x in view.lines] == VAULT.splitlines()
    [highlight] = view.highlights
    span_range = t.SourceSpan.model_validate(load("source_span.json")).range
    assert (highlight.start_line, highlight.end_line) == (
        span_range.start_line,
        span_range.end_line,
    )
    assert view.sha256 == t.sha256_hex(VAULT)


def test_golden_ids_name_real_records():
    findings = {f.id for f in all_findings()}
    taxonomy = load_taxonomy()
    entries = {e.id: e for e in taxonomy.values()}
    for name in ("source_span.json", "source_span_confirmed.json"):
        data = load(name)
        assert data["match"]["entry"] in entries
        assert {p["finding_id"] for p in data["precedents"]} <= findings
    assert load("precedent_card.json")["finding_id"] in findings
    [highlight] = load("source_file_view.json")["highlights"]
    assert entries[highlight["entry"]].group == highlight["group"]


# ---- what the models refuse ---------------------------------------------------------------------


def test_high_severity_needs_a_confirming_second_pass():
    data = load("source_span_confirmed.json")
    data["match"]["confirmed_by"] = None
    with pytest.raises(ValidationError, match="confirmed_by"):
        t.SourceSpan.model_validate(data)


def test_a_cap_can_lower_a_severity_never_raise_it():
    data = load("source_span.json")
    data["match"]["severity_claimed"] = "low"
    with pytest.raises(ValidationError, match="never raise"):
        t.SourceSpan.model_validate(data)


def test_a_span_cannot_confirm_itself():
    data = load("source_span_confirmed.json")
    data["match"]["confirmed_by"] = data["id"]
    with pytest.raises(ValidationError, match="confirm itself"):
        t.SourceSpan.model_validate(data)


def test_a_span_needs_its_match_explanation_and_reviewer():
    for field in ("match", "explanation", "reviewer"):
        with pytest.raises(ValidationError, match="needs match, explanation and reviewer"):
            t.SourceSpan.model_validate(span(**{field: None}))


def test_a_scan_record_carries_a_scan_and_nothing_a_model_wrote():
    scan = load("source_span_scan.json")
    scan["explanation"] = load("source_span.json")["explanation"]
    with pytest.raises(ValidationError, match="no match, precedents or explanation"):
        t.SourceSpan.model_validate(scan)
    scan = load("source_span_scan.json")
    scan["scan"] = None
    with pytest.raises(ValidationError, match="needs scan"):
        t.SourceSpan.model_validate(scan)
    with pytest.raises(ValidationError, match="carries no scan"):
        t.SourceSpan.model_validate(span(scan={"flag": "non_ascii", "count": 1}))


def test_at_most_three_precedents_and_ids_only():
    ref = load("source_span.json")["precedents"][0]
    with pytest.raises(ValidationError):
        t.SourceSpan.model_validate(span(precedents=[ref] * 4))
    with pytest.raises(ValidationError, match="url"):  # a model-named link is not a field
        t.SourceSpan.model_validate(span(precedents=[{**ref, "url": "https://example.com"}]))


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        (("id",), "S0"),
        (("id",), "T1"),
        (("file", "file_id"), "F0"),
        (("file", "file_id"), "../etc"),
        (("subject", "address"), "0x123"),
        (("subject", "code_hash"), "0x" + "AB" * 32),
        (("file", "sha256"), "xyz"),
        (("range", "function"), "init ialize"),
        (("range", "function"), "a" * 41),
        (("match", "entry"), "unprotected-initializer"),
        (("match", "confidence"), "certain"),
        (("explanation", "text"), "x" * 201),
        (("file", "path"), "p" * 201),
    ],
)
def test_bounded_fields_refuse_values_outside_their_shape(path, bad):
    data = load("source_span.json")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(ValidationError):
        t.SourceSpan.model_validate(data)


def test_a_range_cannot_end_before_it_starts_or_start_before_line_one():
    data = load("source_span.json")
    data["range"]["end_line"] = 3
    with pytest.raises(ValidationError, match="before start_line"):
        t.SourceSpan.model_validate(data)
    data["range"].update(start_line=0, end_line=3)
    with pytest.raises(ValidationError):
        t.SourceSpan.model_validate(data)


def test_unknown_fields_are_refused_everywhere():
    with pytest.raises(ValidationError):
        t.SourceSpan.model_validate({**load("source_span.json"), "url": "https://example.com"})
    item = load("reviewer_reply.json")["items"][0]
    with pytest.raises(ValidationError, match="url"):
        t.ReviewerItem.model_validate({**item, "url": "https://example.com"})


def test_excerpt_is_one_short_sentence_and_links_are_https():
    card = load("precedent_card.json")
    assert len(card["credit"]["excerpt"]) <= t.MAX_EXCERPT_CHARS
    for field, bad in (("excerpt", "x" * 281), ("link", "http://example.com"), ("date", "2022")):
        broken = json.loads(json.dumps(card))
        broken["credit"][field] = bad
        with pytest.raises(ValidationError):
            t.PrecedentCard.model_validate(broken)


# ---- inside the system: files, bundles, views --------------------------------------------------


def source_file(file_id: str, path: str, content: str) -> t.SourceFile:
    return t.SourceFile(file_id=file_id, path=path, content=content, sha256=t.sha256_hex(content))


def test_a_source_file_must_carry_the_hash_of_its_content():
    with pytest.raises(ValidationError, match="not the hash"):
        t.SourceFile(file_id="F1", path="a.sol", content="x", sha256=t.sha256_hex("y"))


def make_bundle(files: list[t.SourceFile], **changes: Any) -> t.Bundle:
    fields: dict[str, Any] = {
        "chain": "ethereum",
        "chain_id": 1,
        "address": "0x" + "ab" * 20,
        "block": 1,
        "code_hash": "0x" + "cd" * 32,
        "language": "solidity",
        "compiler": COMPILER,
        "files": tuple(files),
        "bundle_sha256": t.bundle_sha256(files, "solidity", COMPILER),
    }
    return t.Bundle(**{**fields, **changes})


def test_a_bundle_is_fixed_by_its_files_and_ignores_order_and_file_ids():
    a, b = source_file("F1", "a.sol", "contract A {}"), source_file("F2", "b.sol", "contract B {}")
    reordered = [
        source_file("F1", "b.sol", "contract B {}"),
        source_file("F2", "a.sol", "contract A {}"),
    ]
    assert make_bundle([a, b]).bundle_sha256 == make_bundle(reordered).bundle_sha256


def test_the_bundle_hash_covers_language_and_compiler():
    files = [source_file("F1", "a.sol", "contract A {}")]
    base = t.bundle_sha256(files, "solidity", COMPILER)
    assert t.bundle_sha256(files, "yul", COMPILER) != base
    assert t.bundle_sha256(files, "solidity", None) != base


def test_a_bundle_refuses_a_wrong_hash_and_duplicate_file_ids():
    a = source_file("F1", "a.sol", "contract A {}")
    with pytest.raises(ValidationError, match="does not match the files"):
        make_bundle([a], bundle_sha256="0" * 64)
    with pytest.raises(ValidationError, match="not unique"):
        make_bundle([a, source_file("F1", "b.sol", "contract B {}")])


def test_the_stripped_view_has_one_class_entry_per_line():
    t.View(file_id="F1", text="a\nb\n", line_classes=((), (), ()))
    with pytest.raises(ValidationError, match="one entry per line"):
        t.View(file_id="F1", text="a\nb\n", line_classes=((), ()))


def test_line_classes_must_be_sorted_inside_and_not_overlap():
    t.Line(n=1, text="abcdef", classes=((0, 2, "comment"), (3, 6, "string")))
    for classes in (((3, 6, "string"), (0, 2, "comment")), ((0, 4, "comment"), (3, 6, "string"))):
        with pytest.raises(ValidationError, match="sorted"):
            t.Line(n=1, text="abcdef", classes=classes)
    with pytest.raises(ValidationError, match="sorted"):
        t.Line(n=1, text="abc", classes=((0, 9, "comment"),))


def test_a_file_view_range_must_be_consecutive_lines_inside_the_file():
    data = load("source_file_view.json")
    data["lines"] = data["lines"][:-1]
    with pytest.raises(ValidationError, match="consecutive"):
        t.SourceFileView.model_validate(data)
    data = load("source_file_view.json")
    data["to"] = data["line_count"] + 1
    with pytest.raises(ValidationError, match="inside the file"):
        t.SourceFileView.model_validate(data)
    data = load("source_file_view.json")
    data["highlights"][0]["end_line"] = data["line_count"] + 1
    with pytest.raises(ValidationError, match="highlight"):
        t.SourceFileView.model_validate(data)


# ---- coverage ------------------------------------------------------------------------------------


def summary(**changes: Any) -> dict[str, Any]:
    return {**load("review_summary_ran.json"), **changes}


def test_a_review_that_ran_covered_all_the_new_definitions():
    with pytest.raises(ValidationError, match="covered all the new definitions"):
        t.ReviewSummary.model_validate(summary(definitions_new=3))


def test_a_review_that_did_not_fully_run_must_say_why():
    with pytest.raises(ValidationError, match="needs a reason"):
        t.ReviewSummary.model_validate(summary(status="refused", definitions_reviewed=0))


def test_a_refused_review_covered_nothing_and_a_partial_one_covered_part():
    with pytest.raises(ValidationError, match="covered nothing"):
        t.ReviewSummary.model_validate(summary(status="refused", reason="over the ceiling"))
    partial = summary(status="partial", reason="a reviewer call failed twice", definitions_new=3)
    with pytest.raises(ValidationError, match="some, not all"):
        t.ReviewSummary.model_validate({**partial, "definitions_reviewed": 3})
    assert (
        t.ReviewSummary.model_validate({**partial, "definitions_reviewed": 2}).status == "partial"
    )


def test_coverage_cannot_claim_more_than_was_fetched_or_than_is_new():
    with pytest.raises(ValidationError, match="more than was fetched"):
        t.ReviewSummary.model_validate(summary(files_reviewed=99))
    with pytest.raises(ValidationError, match="more definitions than are new"):
        t.ReviewSummary.model_validate(summary(definitions_reviewed=2))


# ---- the reviewer's reply -----------------------------------------------------------------------


def test_the_reviewer_schema_is_flat_closed_and_bounded():
    assert "$ref" not in json.dumps(t.REVIEWER_SCHEMA) and "$defs" not in t.REVIEWER_SCHEMA
    assert t.REVIEWER_SCHEMA["additionalProperties"] is False
    items = t.REVIEWER_SCHEMA["properties"]["items"]
    assert items["maxItems"] == t.MAX_REVIEWER_ITEMS
    assert items["items"]["additionalProperties"] is False
    assert set(items["items"]["properties"]) == set(t.ReviewerItem.model_fields)
    assert items["items"]["properties"]["severity"]["enum"] == ["low", "medium", "high"]


def test_schemas_carry_rules_not_our_docstrings():
    assert "description" not in json.dumps(t.REVIEWER_SCHEMA)
    for name in t.PUBLIC_MODELS:
        assert "fixture" not in json.dumps(t.schema_for(name))


def test_a_reviewer_reply_holds_at_most_the_item_cap():
    item = load("reviewer_reply.json")["items"][0]
    t.ReviewerReply.model_validate({"items": [item] * t.MAX_REVIEWER_ITEMS})
    with pytest.raises(ValidationError):
        t.ReviewerReply.model_validate({"items": [item] * (t.MAX_REVIEWER_ITEMS + 1)})
