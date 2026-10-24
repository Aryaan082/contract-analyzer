"""The hand-check page: a reproducible sample, and text from PDFs never treated as HTML."""

import json

from knowledge.findings import load_findings
from knowledge.spotcheck import choose_sample, write_sheet


def test_the_sample_is_reproducible_and_covers_every_report_that_has_findings():
    files = load_findings()
    first = choose_sample(files, 10, seed=1)
    assert [f.id for f in first] == [f.id for f in choose_sample(files, 10, seed=1)]
    assert [f.id for f in first] != [f.id for f in choose_sample(files, 10, seed=2)]
    assert len(first) == 10 and len({f.id for f in first}) == 10
    with_findings = {f.report for f in files if f.findings}
    assert {f.report for f in first} == with_findings


def test_a_sample_larger_than_the_corpus_is_the_whole_corpus():
    files = load_findings()
    everything = [f for file in files for f in file.findings]
    assert len(choose_sample(files, 500, seed=1)) == len(everything)
    assert len(choose_sample(files, 5, seed=1)) == 5


def test_the_sheet_embeds_the_records_safely(tmp_path):
    path = write_sheet(5, seed=3, output=tmp_path / "sheet.html")
    html = path.read_text()
    assert "/*DATA*/[]" not in html
    start = html.index("const DATA = ") + len("const DATA = ")
    data = json.loads(html[start : html.index(";\nconst CHECKS")].replace("<\\/", "</"))
    assert len(data) == 5
    assert all(r["pdf"].startswith("reports/") and r["page"] >= 1 for r in data)
    # the page text is only ever set with textContent: innerHTML would run markup from a PDF
    assert "innerHTML" not in html and "textContent" in html
    assert html.count("</script>") == 1
