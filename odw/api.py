"""Serwer WWW (FastAPI): strona podglądu + API.

Uruchomienie:  uruchom.bat   (albo: python -m uvicorn odw.api:app --port 8000)
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings
from .classes import load_classes
from .classify import HEAD_PAGES, classify
from .extract import extract_pdf

app = FastAPI(title="Odwołania2json")
WEB = settings.ROOT / "web"
DANE = settings.ROOT.parent / "dane-uzp" / "dane"
TRUE = {"odwolanie.pdf": "odwolanie", "pelnomocnictwo.pdf": "pelnomocnictwo",
        "potwierdzenie_przelewu.pdf": "przelew"}


def _analyze_path(path: Path, display_name: str) -> dict:
    t0 = time.perf_counter()
    doc = extract_pdf(path)
    t1 = time.perf_counter()
    classes = load_classes()
    res = classify(doc, classes)
    t2 = time.perf_counter()
    by_name = {c.name: c for c in classes}
    cls = by_name.get(res.class_name)
    return {
        "filename": display_name,
        "pages": [{"number": p.number, "chars": len(p.text), "ocr": p.ocr,
                   "width": p.width, "height": p.height, "words": len(p.words),
                   "used_for_classification": p.number <= HEAD_PAGES} for p in doc.pages],
        "used_ocr": doc.used_ocr,
        "warnings": doc.warnings,
        "text": [{"page": p.number, "text": p.text[:6000]} for p in doc.pages[:5]],
        "classification": {
            "class_name": res.class_name,
            "title": cls.title if cls else None,
            "yaml_file": cls.file if cls else None,
            "confidence": res.confidence,
            "uncertain": res.uncertain,
            "reason": res.reason,
            "scores": res.scores,
            "thresholds": res.thresholds,
            "details": res.details,
            "titles": {c.name: c.title for c in classes},
        },
        "timing_ms": {"extract": round((t1 - t0) * 1000), "classify": round((t2 - t1) * 1000)},
    }


@app.get("/api/classes")
def api_classes():
    return [{"name": c.name, "title": c.title, "file": c.file, "description": c.description.strip(),
             "min_score": c.min_score, "keywords": [{"pattern": k.regex.pattern, "weight": k.weight}
                                                   for k in c.keywords]}
            for c in load_classes()]


@app.get("/api/samples")
def api_samples():
    out = []
    for pdf in sorted(DANE.glob("przyklad_*/*.pdf")) + sorted((DANE / "negatywne").glob("*.pdf")):
        rel = pdf.relative_to(DANE).as_posix()
        out.append({"id": rel, "label": rel, "truth": TRUE.get(pdf.name),
                    "negative": pdf.parent.name == "negatywne" or pdf.name not in TRUE})
    return out


def _sample_path(rel: str) -> Path:
    p = (DANE / rel).resolve()
    if DANE.resolve() not in p.parents or p.suffix.lower() != ".pdf" or not p.is_file():
        raise HTTPException(404, "Nie ma takiego przykładu")
    return p


@app.get("/api/sample-pdf")
def api_sample_pdf(id: str):
    return FileResponse(_sample_path(id), media_type="application/pdf")


@app.get("/api/analyze-sample")
def api_analyze_sample(id: str):
    return _analyze_path(_sample_path(id), id)


@app.post("/api/analyze")
async def api_analyze(file: UploadFile = File(...)):
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "To nie wygląda na plik PDF")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "upload.pdf"
        p.write_bytes(data)
        return _analyze_path(p, file.filename or "plik.pdf")


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
