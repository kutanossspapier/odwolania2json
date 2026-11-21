"""Serwer WWW (FastAPI): strona + API.

Uruchomienie:  uruchom.bat   (albo: python -m uvicorn odw.api:app --port 8000)
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import settings
from .classes import load_classes
from .classify import HEAD_PAGES
from .crosscheck import run_checks
from .pipeline import process

app = FastAPI(title="Odwołania2json")
WEB = settings.ROOT / "web"
DANE = settings.ROOT.parent / "dane-uzp" / "dane"
TRUE = {"odwolanie.pdf": "odwolanie", "pelnomocnictwo.pdf": "pelnomocnictwo",
        "potwierdzenie_przelewu.pdf": "przelew"}
LOW_CONFIDENCE = 0.75


def _llm_reachable() -> bool:
    """Czy serwer LLM (np. Ollama) odpowiada? Szybki test GET /models."""
    import urllib.request
    try:
        with urllib.request.urlopen(settings.LLM_BASE_URL.rstrip("/") + "/models", timeout=1.5):
            return True
    except Exception:
        return False


def _result_payload(path: Path, display_name: str, llm: bool = False) -> dict:
    t0 = time.perf_counter()
    classes = load_classes()
    res = process(path, classes, use_llm=llm)
    ms = round((time.perf_counter() - t0) * 1000)
    cl = res.classification
    cls = next((c for c in classes if c.name == cl.class_name), None)
    fields = [f.to_dict() for f in res.extraction.fields] if res.extraction else []
    for f in fields:
        f["to_check"] = f["status"] == "brak" or f["confidence"] < LOW_CONFIDENCE
    return {
        "filename": display_name,
        "pages": [{"number": p.number, "width": p.width, "height": p.height, "chars": len(p.text),
                   "ocr": p.ocr, "used_for_classification": p.number <= HEAD_PAGES}
                  for p in res.doc.pages],
        "used_ocr": res.doc.used_ocr,
        "warnings": res.doc.warnings,
        "classification": {
            "class_name": cl.class_name, "title": cls.title if cls else None,
            "yaml_file": cls.file if cls else None, "confidence": cl.confidence,
            "uncertain": cl.uncertain, "reason": cl.reason, "scores": cl.scores,
            "thresholds": cl.thresholds, "details": cl.details,
            "titles": {c.name: c.title for c in classes},
        },
        "fields": fields,
        "group_labels": (cls.raw.get("group_labels") or {}) if cls else {},
        "used_llm": llm,
        "json": res.json,
        "llm_fields": res.llm_fields,
        "text": [{"page": p.number, "text": p.text[:6000]} for p in res.doc.pages[:5]],
        "timing_ms": ms,
    }


@app.get("/api/status")
def api_status():
    reachable = _llm_reachable()
    return {"llm_enabled": settings.LLM_ENABLED, "llm_model": settings.LLM_MODEL,
            "llm_reachable": reachable, "llm_url": settings.LLM_BASE_URL,
            "low_confidence": LOW_CONFIDENCE}


@app.get("/api/classes")
def api_classes():
    return [{"name": c.name, "title": c.title, "file": c.file, "description": c.description.strip(),
             "min_score": c.min_score,
             "keywords": [{"pattern": k.regex.pattern, "weight": k.weight} for k in c.keywords],
             "fields": [{"name": f.name, "path": f.path, "method": f.method, "type": f.type,
                         "description": f.description, "patterns": len(f.patterns)} for f in c.fields]}
            for c in load_classes()]


@app.get("/api/samples")
def api_samples():
    """Przykładowe komplety z danych UZP: [{id: 'przyklad_01', files: [...]}] + negatywy."""
    out = []
    for d in sorted(DANE.glob("przyklad_*")):
        files = [{"id": f"{d.name}/{p.name}", "name": p.name, "truth": TRUE.get(p.name)}
                 for p in sorted(d.glob("*.pdf"))]
        out.append({"id": d.name, "files": files})
    neg = [{"id": f"negatywne/{p.name}", "name": p.name, "truth": None}
           for p in sorted((DANE / "negatywne").glob("*.pdf"))]
    out.append({"id": "negatywne", "files": neg})
    return out


def _sample_path(rel: str) -> Path:
    p = (DANE / rel).resolve()
    if DANE.resolve() not in p.parents or p.suffix.lower() != ".pdf" or not p.is_file():
        raise HTTPException(404, "Nie ma takiego przykładu")
    return p


@app.get("/api/sample-pdf")
def api_sample_pdf(id: str):
    return FileResponse(_sample_path(id), media_type="application/pdf")


@app.get("/api/process-sample")
def api_process_sample(id: str, llm: bool = False):
    return _result_payload(_sample_path(id), id.split("/")[-1], llm)


@app.post("/api/process")
async def api_process(file: UploadFile = File(...), llm: bool = False):
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "To nie wygląda na plik PDF")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "upload.pdf"
        p.write_bytes(data)
        return _result_payload(p, file.filename or "plik.pdf", llm)


@app.post("/api/crosscheck")
def api_crosscheck(docs: list[dict] = Body(...)):
    """docs: [{name, class_name, json}] -> wyniki walidacji między dokumentami."""
    return run_checks(docs)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
