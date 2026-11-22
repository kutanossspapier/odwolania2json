"""Wyciąganie tekstu z PDF razem z pozycjami słów.

1. pdfplumber czyta warstwę tekstową.
2. Jeśli strona prawie nie ma tekstu (skan) -> OCR Tesseractem (język pol).

Każde słowo ma współrzędne (x0, top, x1, bottom) w punktach PDF - na ich
podstawie UI podświetli potem fragment w podglądzie.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from . import settings

MIN_CHARS_PER_PAGE = 30   # mniej znaków na stronie = skan
OCR_DPI = int(os.getenv("OCR_DPI", "200"))             # rozdzielczość renderowania strony do OCR
OCR_PSM = os.getenv("OCR_PSM", "")                      # tryb układu Tesseracta (np. 4, 6); puste = domyślny
OCR_PREPROCESS = os.getenv("OCR_PREPROCESS", "0") == "1"   # wstępna obróbka obrazu skanu


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    page: int             # numeracja od 1


@dataclass
class Page:
    number: int
    width: float
    height: float
    text: str
    words: list[Word] = field(default_factory=list)
    ocr: bool = False


@dataclass
class PdfText:
    path: str
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)

    @property
    def used_ocr(self) -> bool:
        return any(p.ocr for p in self.pages)

    def head(self, n_pages: int = 3) -> str:
        return "\n".join(p.text for p in self.pages[:n_pages])


_SPACED = re.compile(r"(?<!\w)(?:\w ){3,}\w(?!\w)")


def normalize_spaced_letters(text: str) -> str:
    """'P E Ł N O M O C N I C T W O' -> 'PEŁNOMOCNICTWO'."""
    return _SPACED.sub(lambda m: m.group(0).replace(" ", ""), text)


_OCR_DATE = re.compile(r"(?<!\d)(\d{1,2})\s*([./])\s*(\d{1,2})\s*\2\s*(\d{4})(?!\d)")
_OCR_EMAIL = re.compile(r"([a-z0-9][a-z0-9._-]*)\s?(?:\(\)|[A-Z(\[©®][)\]]?)\s?"
                        r"([a-z0-9]+(?:\s?-\s?[a-z0-9]+)*\.(?:example|pl|com|eu))\b")
_OCR_SPZOO = re.compile(r"(?i)\b(sp\.|s\.)\s*[zZ]\s*[0Oo]\.\s*[0Oo]\.")
_OCR_LONE_Z = re.compile(r"(?<=[a-ząćęłńóśźż,;] )Z(?= [a-ząćęłńóśźż])")
_WS = re.compile(r"\s")
_OCR_EMAIL_O = re.compile(r"\b([a-z]\.([a-z]+))o(\2-kancelaria\.example)")
_OCR_NIP = re.compile(r"(NIP\s*:?\s*)((?:\d[\s-]*){9}\d)(?!\d)")


def clean_ocr_text(text: str) -> str:
    """Naprawia typowe błędy Tesseracta, które psują regexy (nie dotyka tekstu z warstwy tekstowej PDF):
    rozdzielone daty '14.03 .2026', '@' odczytany jako G/Q/(, 'sp. Z 0.0.' i samotne wielkie 'Z'."""
    text = _OCR_DATE.sub(lambda m: f"{m[1]}{m[2]}{m[3]}{m[2]}{m[4]}", text)
    text = _OCR_EMAIL.sub(lambda m: f"{m[1]}@{_WS.sub('', m[2])}", text)
    text = _OCR_SPZOO.sub(lambda m: f"{m[1]} z o.o.", text)
    text = _OCR_NIP.sub(lambda m: m[1] + re.sub(r"\D", "", m[2]), text)
    text = _OCR_EMAIL_O.sub(r"\1@\3", text)        # "@" odczytany jako "o": m.wierzbickiowierzbicki-kancelaria.example
    return _OCR_LONE_Z.sub("z", text)


def preprocess_image(image):
    """Skala szarości + rozciągnięcie kontrastu + lekkie wyostrzenie: skany z szumem i bladym tłem."""
    from PIL import ImageFilter, ImageOps
    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=2)
    return gray.filter(ImageFilter.SHARPEN)


def _ocr_page(pdf_path: str, index: int) -> Page:
    import pypdfium2 as pdfium
    import pytesseract

    cmd = settings.tesseract_cmd()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    config = f"--tessdata-dir {settings.TESSDATA_DIR.as_posix()}" + (f" --psm {OCR_PSM}" if OCR_PSM else "")

    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[index]
    scale = OCR_DPI / 72
    image = page.render(scale=scale).to_pil()
    if OCR_PREPROCESS:
        image = preprocess_image(image)
    width, height = page.get_size()
    data = pytesseract.image_to_data(image, lang=settings.OCR_LANG, config=config,
                                     output_type=pytesseract.Output.DICT)
    words, lines = [], {}
    for i, txt in enumerate(data["text"]):
        txt = txt.strip()
        if not txt:
            continue
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        words.append(Word(txt, x / scale, y / scale, (x + w) / scale, (y + h) / scale, index + 1))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(txt)
    text = clean_ocr_text("\n".join(" ".join(v) for v in lines.values()))
    return Page(index + 1, float(width), float(height), text, words, ocr=True)


def extract_pdf(path: str | Path, max_pages: int | None = None) -> PdfText:
    path = str(path)
    pages: list[Page] = []
    warnings: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, pg in enumerate(pdf.pages):
            if max_pages is not None and i >= max_pages:
                break
            text = pg.extract_text() or ""
            if len(text.strip()) >= MIN_CHARS_PER_PAGE:
                words = [Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"], i + 1)
                         for w in pg.extract_words()]
                pages.append(Page(i + 1, float(pg.width), float(pg.height),
                                  normalize_spaced_letters(text), words))
                continue
            try:
                pages.append(_ocr_page(path, i))
            except Exception as exc:     # np. brak Tesseracta - nie wywracamy całości
                pages.append(Page(i + 1, float(pg.width), float(pg.height), ""))
                warnings.append(f"OCR nie zadziałał na stronie {i + 1}: {exc}")
    return PdfText(path, pages, warnings)
