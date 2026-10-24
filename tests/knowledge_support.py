"""Helpers for the knowledge tests: made-up report pages, no real reports and no network.

`make_pdf` writes a tiny valid PDF (base-14 fonts, positioned text) so the PDF reading code runs on
something real. `Page` builds the `Line` objects the parser works on, shaped like a Trail of Bits
review: big headings, labelled blocks, bold section headings, monospaced code, page footers.
"""

from dataclasses import dataclass, field

from knowledge.parse.pdf_text import Line

HELVETICA, BOLD, COURIER = "F1", "F2", "F3"


def make_pdf(pages: list[list[tuple[float, float, str, float, str]]]) -> bytes:
    """pages: for each page a list of (x, y, font, size, text) runs."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    tree = add(b"")
    fonts = {
        HELVETICA: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        BOLD: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"),
        COURIER: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"),
    }
    page_ids = []
    for runs in pages:
        stream = "".join(
            f"BT /{font} {size} Tf {x} {y} Td ({text}) Tj ET\n" for x, y, font, size, text in runs
        ).encode()
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
        resources = "<< /Font << " + " ".join(f"/{k} {v} 0 R" for k, v in fonts.items()) + " >> >>"
        page = add(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content} 0 R /Resources {resources} >>".encode()
        )
        page_ids.append(page)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref_at,
    )
    return bytes(out)


@dataclass
class Page:
    """Builds the lines of one page, top to bottom."""

    number: int
    lines: list[Line] = field(default_factory=list)
    y: float = 700.0

    def add(
        self,
        text: str,
        *,
        size: float = 11.0,
        bold: bool = False,
        code: bool = False,
        x: float = 72.0,
        gap: float = 15.0,
    ) -> "Page":
        self.y -= gap
        self.lines.append(Line(self.number, self.y, x, text, code, bold, size))
        return self

    def footer(self, text: str = "Trail of Bits 3 Test Security Assessment") -> "Page":
        self.lines.append(Line(self.number, 48.0, 90.0, text, False, False, 9.0))
        self.lines.append(Line(self.number, 36.0, 90.0, "PUBLIC", False, True, 8.0))
        return self


def finding_block(
    page: Page,
    number: int,
    title: str,
    severity: str,
    *,
    finding_id: str | None = None,
    with_exploit: bool = True,
    code: bool = True,
) -> None:
    """One finding in the newer template."""
    fid = finding_id if finding_id is not None else f"TOB-TEST-{number}"
    page.add(f"{number}. {title}", size=12, bold=True, gap=30)
    page.add(f"Severity: {severity} Difficulty: Low")
    page.add(f"Type: Data Validation Finding ID: {fid}")
    page.add("Target: contracts/Pool.sol")
    page.add("Description", size=12, bold=True, gap=30)
    page.add(f"The function in {title.lower()} does not check its argument before using it.")
    page.add("A second sentence continues the same paragraph on the next line.")
    if code:
        page.add("function f(uint x) { y = x; }", size=9, code=True, gap=30)
        page.add("Figure 1.1: contracts/Pool.sol#L10", gap=24)
    page.add("A new paragraph follows the figure and ends the description.", gap=30)
    if with_exploit:
        page.add("Exploit Scenario", size=12, bold=True, gap=30)
        page.add("Alice calls the function with a crafted value and gains something.")
    page.add("Recommendations", size=12, bold=True, gap=30)
    page.add("Short term, add a check on the argument. Long term, add tests.")


def newer_template_report(
    severities: tuple[str, ...] = ("High", "Low"),
    *,
    exposure: dict[str, int] | None = None,
    table_severities: tuple[str, ...] | None = None,
) -> list[Line]:
    """A made-up report: cover, exposure table, findings table, detailed findings, appendix."""
    cover = Page(1)
    cover.add("Test Protocol", size=26, bold=True, gap=0)
    cover.add("Security Assessment", size=15)
    cover.add("September 5, 2024", bold=True)

    summary = Page(2)
    summary.add("EXPOSURE ANALYSIS CATEGORY BREAKDOWN", size=12, bold=True)
    counts = exposure or {s.lower(): severities.count(s) for s in set(severities)}
    for name in ("High", "Medium", "Low", "Informational", "Undetermined"):
        summary.add(f"{name} {counts.get(name.lower(), 0)} Data Validation 1", size=10, bold=True)
    summary.footer()

    table = Page(3)
    table.add("Summary of Findings", size=18, bold=True)
    table.add("The table below summarizes the findings of the review.")
    table.add("ID Title Type Severity", size=10, bold=True)
    for n, severity in enumerate(table_severities or severities, start=1):
        table.add(f"{n} Missing check number {n} Data Validation {severity}", size=10)
    table.footer()

    details = [Page(4)]
    details[0].add("Detailed Findings", size=18, bold=True)
    for n, severity in enumerate(severities, start=1):
        if n > 1:
            details.append(Page(3 + n))
        finding_block(details[-1], n, f"Missing check number {n}", severity)
        details[-1].footer()

    appendix = Page(4 + len(severities))
    appendix.add("A. Vulnerability Categories", size=18, bold=True)
    appendix.add("Severity: Informational is explained here and must not be read as a finding.")
    appendix.footer()

    pages = [cover, summary, table, *details, appendix]
    return [ln for page in pages for ln in page.lines]
