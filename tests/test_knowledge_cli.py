"""The command line, on the committed data."""

from knowledge.cli import main


def test_check_passes_on_the_committed_taxonomy_and_findings(capsys):
    assert main(["check"]) == 0
    out = capsys.readouterr().out
    assert "0 problems" in out and "35 taxonomy entries" in out and "19 findings" in out
