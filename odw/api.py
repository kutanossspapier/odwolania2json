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

from . import feedback, settings
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
    """Czy serwer LLM odpowiada? Szybki test GET /models (z kluczem API, jeśli jest).

    Odpowiedź 401/403 oznacza, że serwer żyje, ale klucz jest zły - to też zgłaszamy jako
    niedostępny, bo aplikacja nie mogłaby z niego skorzystać.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(settings.LLM_BASE_URL.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=3):
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)      # np. 404 = brak /models, ale serwer działa
    except Exception:
        return False


def _result_payload(path: Path, display_name: str, llm: bool = False, llm_only: bool = False, review: bool = False) -> dict:
    t0 = time.perf_counter()
    classes = load_classes()
    llm = llm or llm_only
    res = process(path, classes, use_llm=llm, llm_only=llm_only, llm_review=review and llm)
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
        "llm_only": llm_only,
        "llm_review": review and llm,
        "json": res.json,
        "llm_fields": res.llm_fields,
        "text": [{"page": p.number, "text": p.text} for p in res.doc.pages],
        "timing_ms": ms,
    }


@app.get("/api/status")
def api_status():
    reachable = _llm_reachable()
    return {"llm_enabled": settings.LLM_ENABLED, "llm_model": settings.LLM_MODEL,
            "llm_reachable": reachable, "llm_url": settings.LLM_BASE_URL,
            "llm_provider": settings.LLM_PROVIDER, "llm_label": settings.LLM_PROVIDER_LABEL,
            "llm_region": settings.LLM_REGION,
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
def api_process_sample(id: str, llm: bool = False, llm_only: bool = False, review: bool = False):
    return _result_payload(_sample_path(id), id.split("/")[-1], llm, llm_only, review)


@app.post("/api/process")
async def api_process(file: UploadFile = File(...), llm: bool = False, llm_only: bool = False, review: bool = False):
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "To nie wygląda na plik PDF")
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "upload.pdf"
        p.write_bytes(data)
        return _result_payload(p, file.filename or "plik.pdf", llm, llm_only, review)


@app.post("/api/feedback")
def api_feedback(payload: dict = Body(...)):
    """Ręczna korekta pola: diagnoza + propozycja poprawki YAML (bez LLM, a opcjonalnie z LLM)."""
    try:
        client = None
        use_llm = bool(payload.get("use_llm"))
        if use_llm:
            from . import llm
            client = llm.make_client()
        diag = feedback.diagnose(payload["class_name"], payload["field"], payload.get("pages") or [],
                                 payload.get("expected", ""), payload.get("reason", ""), use_llm, client)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    text = "\n".join(p.get("text", "") for p in payload.get("pages") or [])
    diag["id"] = None if diag["already_ok"] else feedback.save_correction(diag, payload.get("doc_name", ""), text, bool(payload.get("as_example")))
    return diag


@app.post("/api/feedback/apply")
def api_feedback_apply(payload: dict = Body(...)):
    """Wdraża zweryfikowany wzorzec z poprawki do pamięci (config/learned/patterns.yaml)."""
    try:
        return feedback.apply_correction(int(payload["id"]), int(payload.get("proposal", 0)), bool(payload.get("force")))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/corrections/delete")
def api_corrections_delete(payload: dict = Body(...)):
    try:
        feedback.delete_correction(int(payload["id"]))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/learned")
def api_learned():
    from . import learned
    return learned.listing()


@app.post("/api/learned/remove")
def api_learned_remove(payload: dict = Body(...)):
    try:
        feedback.revoke(payload["class_name"], payload["field"], int(payload["index"]))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/corrections")
def api_corrections():
    return feedback.list_corrections()


@app.post("/api/crosscheck")
def api_crosscheck(docs: list[dict] = Body(...)):
    """docs: [{name, class_name, json}] -> wyniki walidacji między dokumentami."""
    return run_checks(docs)


class FreshStaticFiles(StaticFiles):
    """Strona zawsze świeża: przeglądarka nie trzyma starej wersji UI po aktualizacji aplikacji."""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if not path.startswith("vendor/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", FreshStaticFiles(directory=WEB, html=True), name="web")
