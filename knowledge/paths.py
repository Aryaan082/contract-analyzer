"""Where the knowledge base keeps its files."""

from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = KNOWLEDGE_DIR / "manifest.json"
# The PDFs: git-ignored, fetched by `python -m knowledge fetch`.
REPORTS_DIR = KNOWLEDGE_DIR / "reports"
FINDINGS_DIR = KNOWLEDGE_DIR / "findings"
TAXONOMY_DIR = KNOWLEDGE_DIR / "taxonomy"
