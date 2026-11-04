"""Leitura de PDFs: texto nato-digital quando existe, OCR quando a página é imagem."""
import io
from dataclasses import dataclass
from typing import Iterator

import pymupdf as fitz  # PyMuPDF

from ..config import Settings
from .quality import is_degraded, repair_cyrillic


@dataclass
class PageResult:
    number: int  # 1-based
    text: str
    method: str  # "text" | "ocr" | "ocr_unavailable" | "empty" | "text_degraded"


def ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _ocr_page(page: "fitz.Page", settings: Settings) -> str:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=settings.ocr_dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=settings.ocr_lang)


def iter_pages(path: str, settings: Settings) -> Iterator[PageResult]:
    """Gera uma página por vez (não carrega milhares de páginas em memória)."""
    has_ocr = ocr_available()
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if len(text) >= settings.min_text_chars and not is_degraded(text):
                yield PageResult(i, text, "text")
                continue
            if len(text) >= settings.min_text_chars:  # há texto, mas corrompido: refaz o OCR da imagem
                if has_ocr:
                    try:
                        ocr_text = _ocr_page(page, settings).strip()
                        if ocr_text:
                            yield PageResult(i, ocr_text, "ocr")
                            continue
                    except Exception:
                        pass
                yield PageResult(i, repair_cyrillic(text), "text_degraded")
                continue
            # página sem texto útil (digitalizada): OCR
            if not has_ocr:
                yield PageResult(i, text, "ocr_unavailable" if page.get_images() else "empty")
                continue
            try:
                ocr_text = _ocr_page(page, settings).strip()
            except Exception:
                yield PageResult(i, text, "ocr_unavailable")
                continue
            yield PageResult(i, (text + "\n" + ocr_text).strip(), "ocr" if ocr_text else "empty")


def page_count(path: str) -> int:
    with fitz.open(path) as doc:
        return doc.page_count
