from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pypdfium2 as pdfium
from PIL import Image

from app.config import Settings, get_settings
from app.models.document_models import PageContent, PageExtractionMode


_ALNUM_RE = re.compile(r"[A-Za-z0-9]")


@dataclass
class PdfPageResult:
    page_number: int
    text: str
    mode: PageExtractionMode
    image: Optional[Image.Image]
    width: float
    height: float
    warnings: list[str]


class PdfService:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    @staticmethod
    def is_meaningful_text(text: str, min_alnum: int = 40) -> bool:
        if not text:
            return False
        return len(_ALNUM_RE.findall(text)) >= min_alnum

    def open_document(self, path: str | Path) -> pdfium.PdfDocument:
        return pdfium.PdfDocument(str(path))

    def page_count(self, path: str | Path) -> int:
        doc = self.open_document(path)
        try:
            return len(doc)
        finally:
            doc.close()

    def extract_page_text(self, page: pdfium.PdfPage) -> str:
        textpage = page.get_textpage()
        try:
            return textpage.get_text_bounded() or ""
        finally:
            textpage.close()

    def render_page(self, page: pdfium.PdfPage, scale: float = 2.0) -> Image.Image:
        bitmap = page.render(scale=scale)
        try:
            return bitmap.to_pil()
        finally:
            bitmap.close()

    def process_pdf(self, path: str | Path) -> list[PdfPageResult]:
        doc = self.open_document(path)
        results: list[PdfPageResult] = []
        try:
            total = len(doc)
            if total > self.settings.max_pdf_pages:
                raise ValueError(
                    f"PDF has {total} pages which exceeds limit {self.settings.max_pdf_pages}"
                )
            for index in range(total):
                page = doc[index]
                width, height = page.get_size()
                warnings: list[str] = []
                text = ""
                mode = PageExtractionMode.TEXT
                image: Optional[Image.Image] = None
                try:
                    text = self.extract_page_text(page).strip()
                    if self.is_meaningful_text(text):
                        mode = PageExtractionMode.TEXT
                        image = None
                    else:
                        mode = PageExtractionMode.OCR
                        warnings.append(
                            "Insufficient selectable text; page will be OCR processed"
                        )
                        image = self.render_page(page, scale=2.0)
                except Exception as exc:  # noqa: BLE001
                    mode = PageExtractionMode.OCR
                    warnings.append(f"Text extraction failed: {exc}")
                    try:
                        image = self.render_page(page, scale=2.0)
                    except Exception as render_exc:  # noqa: BLE001
                        mode = PageExtractionMode.FAILED
                        warnings.append(f"Page render failed: {render_exc}")
                        image = None
                results.append(
                    PdfPageResult(
                        page_number=index + 1,
                        text=text,
                        mode=mode,
                        image=image,
                        width=float(width),
                        height=float(height),
                        warnings=warnings,
                    )
                )
        finally:
            doc.close()
        return results

    def to_page_contents(self, pages: list[PdfPageResult]) -> list[PageContent]:
        return [
            PageContent(
                page_number=p.page_number,
                mode=p.mode,
                text=p.text,
                width=p.width,
                height=p.height,
                warnings=p.warnings,
            )
            for p in pages
        ]