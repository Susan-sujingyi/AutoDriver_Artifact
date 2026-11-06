"""Mechanical PDF views only; no model, summarization, or annotation."""
import subprocess
from pathlib import Path

from benchmark.common.api import digest, inventory, save


def prepare_views(agent):
    agent = Path(agent)
    documents = agent / "documents"
    if not documents.exists():
        return None
    destination = documents / "views"
    destination.mkdir(exist_ok=False)
    sources = {}
    for index, pdf in enumerate(sorted(documents.glob("*.pdf"))):
        out = destination / f"{index:02d}-{pdf.stem}"
        out.mkdir()
        sources[pdf.name] = digest(pdf)
        for command in (["pdftotext", "-layout", str(pdf), str(out / "text.txt")],
                        ["pdftoppm", "-png", "-r", "100", str(pdf), str(out / "page")]):
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError("Mechanical PDF conversion failed: " + result.stderr[-1000:])
    manifest = {"schema": "autodriver.document-views/v1", "source_pdfs": sources,
                "render_dpi": 100, "files": inventory(destination),
                "scope": "Mechanical text extraction and page rendering; no added interpretation"}
    save(destination / "manifest.json", manifest)
    return manifest
