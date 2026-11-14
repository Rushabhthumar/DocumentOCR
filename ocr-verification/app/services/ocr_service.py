from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from PIL import Image

from app.config import Settings, get_settings
from app.services.image_service import ImageService

logger = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    confidence: float
    bbox: Optional[list[list[float]]] = None


@dataclass
class OcrPageResult:
    text: str
    lines: list[OcrLine]
    average_confidence: float
    engine: str
    warnings: list[str] = field(default_factory=list)
    table_text: str = ""
    structure_text: str = ""


class OcrService:
    """Local OCR via PaddleOCR + optional PP-StructureV3 tables + TrOCR fallback."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.image_service = ImageService()
        self._ocr = None
        self._structure = None
        self._trocr_processor = None
        self._trocr_model = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None
        self._structure_error: Optional[str] = None
        self._trocr_error: Optional[str] = None

    @property
    def available(self) -> bool:
        try:
            self._ensure_ocr()
            return self._ocr is not None
        except Exception:  # noqa: BLE001
            return False

    def _ensure_ocr(self):
        if self._ocr is not None or self._init_error:
            if self._init_error and self._ocr is None:
                raise RuntimeError(self._init_error)
            return
        with self._lock:
            if self._ocr is not None or self._init_error:
                if self._init_error and self._ocr is None:
                    raise RuntimeError(self._init_error)
                return
            try:
                from paddleocr import PaddleOCR

                device = "gpu" if self.settings.use_gpu else "cpu"
                self._ocr = PaddleOCR(
                    lang=self.settings.ocr_lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    device=device,
                )
                logger.info("PaddleOCR initialized on %s", device)
            except Exception as exc:  # noqa: BLE001
                self._init_error = (
                    "PaddleOCR is not available. Install paddlepaddle + paddleocr "
                    f"as described in README. Underlying error: {exc}"
                )
                logger.exception("Failed to initialize PaddleOCR")
                raise RuntimeError(self._init_error) from exc

    def _ensure_structure(self):
        if not self.settings.enable_structure_ocr:
            return None
        if self._structure is not None:
            return self._structure if self._structure is not False else None
        if self._structure_error:
            return None
        with self._lock:
            if self._structure is not None:
                return self._structure if self._structure is not False else None
            try:
                from paddleocr import PPStructureV3

                device = "gpu" if self.settings.use_gpu else "cpu"
                self._structure = PPStructureV3(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    device=device,
                )
                logger.info("PP-StructureV3 initialized on %s", device)
            except Exception as exc:  # noqa: BLE001
                self._structure_error = str(exc)
                logger.warning("PP-StructureV3 unavailable: %s", exc)
                self._structure = False
        return self._structure if self._structure not in (None, False) else None

    def _ensure_trocr(self):
        if not self.settings.enable_trocr_fallback:
            return None
        if self._trocr_model is not None:
            return self._trocr_model
        if self._trocr_error:
            return None
        with self._lock:
            if self._trocr_model is not None:
                return self._trocr_model
            try:
                from transformers import TrOCRProcessor, VisionEncoderDecoderModel

                model_name = "microsoft/trocr-base-handwritten"
                self._trocr_processor = TrOCRProcessor.from_pretrained(model_name)
                self._trocr_model = VisionEncoderDecoderModel.from_pretrained(model_name)
                self._trocr_model.eval()
                logger.info("TrOCR handwriting model loaded")
            except Exception as exc:  # noqa: BLE001
                self._trocr_error = str(exc)
                logger.warning("TrOCR unavailable: %s", exc)
                self._trocr_model = False
        return self._trocr_model if self._trocr_model not in (None, False) else None

    def _extract_result_dict(self, item: Any) -> dict[str, Any]:
        """Normalize PaddleOCR 3.x OCRResult / dict payloads."""
        data = item
        if hasattr(item, "json"):
            raw_json = item.json
            data = raw_json() if callable(raw_json) else raw_json
        if isinstance(data, dict) and "res" in data and isinstance(data["res"], dict):
            return data["res"]
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _bbox_sort_key(line: OcrLine) -> tuple[float, float]:
        if not line.bbox:
            return (0.0, 0.0)
        try:
            ys = [float(p[1]) for p in line.bbox]
            xs = [float(p[0]) for p in line.bbox]
            # Bucket by ~row height so left-to-right within a visual row
            y = min(ys)
            x = min(xs)
            return (round(y / 18.0) * 18.0, x)
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    def _lines_to_text(self, lines: list[OcrLine]) -> str:
        ordered = sorted(lines, key=self._bbox_sort_key)
        return "\n".join(line.text for line in ordered if line.text)

    def _parse_paddle_result(self, result: Any) -> OcrPageResult:
        lines: list[OcrLine] = []
        warnings: list[str] = []

        payloads = result if isinstance(result, list) else [result]
        for item in payloads:
            data = self._extract_result_dict(item)
            texts = data.get("rec_texts")
            scores = data.get("rec_scores") or []
            boxes = data.get("rec_polys") or data.get("dt_polys") or []
            if texts:
                for idx, text in enumerate(texts):
                    conf = float(scores[idx]) if idx < len(scores) else 0.0
                    bbox = boxes[idx] if idx < len(boxes) else None
                    if isinstance(bbox, list):
                        try:
                            bbox = [[float(p[0]), float(p[1])] for p in bbox]
                        except Exception:  # noqa: BLE001
                            bbox = None
                    if text:
                        lines.append(OcrLine(text=str(text), confidence=conf, bbox=bbox))
                continue

            if isinstance(item, list):
                for row in item:
                    try:
                        if isinstance(row, dict):
                            text = str(row.get("text") or row.get("transcription") or "")
                            conf = float(row.get("score") or row.get("confidence") or 0.0)
                            bbox = row.get("box") or row.get("bbox")
                        elif isinstance(row, (list, tuple)) and len(row) >= 2:
                            bbox = row[0]
                            text_info = row[1]
                            if isinstance(text_info, (list, tuple)):
                                text = str(text_info[0])
                                conf = float(text_info[1]) if len(text_info) > 1 else 0.0
                            else:
                                text = str(text_info)
                                conf = 0.0
                        else:
                            continue
                        if text:
                            lines.append(OcrLine(text=text, confidence=conf, bbox=bbox))
                    except Exception:  # noqa: BLE001
                        warnings.append("Skipped unparsable OCR line")

        text = self._lines_to_text(lines)
        avg = sum(line.confidence for line in lines) / len(lines) if lines else 0.0
        if not lines:
            warnings.append("OCR returned no text lines")
        return OcrPageResult(
            text=text,
            lines=lines,
            average_confidence=avg,
            engine="paddleocr",
            warnings=warnings,
        )

    def _html_tables_to_text(self, html_chunks: list[str]) -> str:
        rows: list[str] = []
        for html in html_chunks:
            # Keep cell separators so "Grand Total | 7670" stays parseable
            cleaned = re.sub(r"(?i)</t[dh]>", " | ", html)
            cleaned = re.sub(r"(?i)</tr>", "\n", cleaned)
            cleaned = re.sub(r"(?i)<br\s*/?>", " ", cleaned)
            cleaned = re.sub(r"<[^>]+>", " ", cleaned)
            cleaned = re.sub(r"[ \t]+", " ", cleaned)
            for line in cleaned.splitlines():
                line = line.strip(" |")
                if line:
                    rows.append(line)
        return "\n".join(rows)

    def _parse_structure_payload(self, result: Any) -> tuple[str, str]:
        """Return (structure_text, table_text) from PP-StructureV3 output."""
        html_chunks: list[str] = []
        md_chunks: list[str] = []
        text_chunks: list[str] = []

        payloads = result if isinstance(result, list) else [result]
        for item in payloads:
            data = self._extract_result_dict(item)
            for key in ("table_html", "html", "pred_html"):
                val = data.get(key)
                if isinstance(val, str) and "<" in val:
                    html_chunks.append(val)
                elif isinstance(val, list):
                    html_chunks.extend(str(v) for v in val if isinstance(v, str) and "<" in v)
            for key in ("markdown", "parsing_res_list", "overall_ocr_res"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    md_chunks.append(val)
                elif isinstance(val, list):
                    for entry in val:
                        if isinstance(entry, str) and entry.strip():
                            md_chunks.append(entry)
                        elif isinstance(entry, dict):
                            for nested_key in ("content", "text", "html", "markdown"):
                                nested = entry.get(nested_key)
                                if isinstance(nested, str) and nested.strip():
                                    if "<table" in nested.lower() or nested_key == "html":
                                        html_chunks.append(nested)
                                    else:
                                        md_chunks.append(nested)
            # Nested layout blocks
            for block_key in ("layout_det_res", "table_res_list", "structure"):
                blocks = data.get(block_key)
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    for nested_key in ("html", "table_html", "pred_html", "text", "content"):
                        nested = block.get(nested_key)
                        if isinstance(nested, str) and nested.strip():
                            if "html" in nested_key or "<table" in nested.lower():
                                html_chunks.append(nested)
                            else:
                                text_chunks.append(nested)

        table_text = self._html_tables_to_text(html_chunks)
        structure_text = "\n".join(
            chunk.strip() for chunk in (*md_chunks, *text_chunks, table_text) if chunk and chunk.strip()
        )
        # Strip residual HTML from markdown dumps
        structure_text = re.sub(r"<[^>]+>", " ", structure_text)
        structure_text = re.sub(r"[ \t]+", " ", structure_text)
        structure_text = re.sub(r"\n{3,}", "\n\n", structure_text).strip()
        return structure_text, table_text

    def run_structure(self, image: Image.Image) -> Optional[dict[str, str]]:
        pipeline = self._ensure_structure()
        if pipeline is None:
            return None
        try:
            import numpy as np

            working = image.convert("RGB") if image.mode != "RGB" else image
            arr = np.array(working)
            result = pipeline.predict(arr)
            structure_text, table_text = self._parse_structure_payload(result)
            if not structure_text and not table_text:
                return None
            return {"structure_text": structure_text, "table_text": table_text}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Structure OCR failed: %s", exc)
            return None

    def _trocr_recognize(self, crop: Image.Image) -> Optional[str]:
        model = self._ensure_trocr()
        if model is None or self._trocr_processor is None:
            return None
        try:
            import torch

            rgb = crop.convert("RGB")
            inputs = self._trocr_processor(images=rgb, return_tensors="pt")
            with torch.no_grad():
                generated = model.generate(**inputs, max_new_tokens=64)
            text = self._trocr_processor.batch_decode(generated, skip_special_tokens=True)[0]
            text = (text or "").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("TrOCR crop failed: %s", exc)
            return None

    def _looks_handwritten_or_noisy(self, parsed: OcrPageResult) -> bool:
        text = parsed.text or ""
        if re.search(r"(?i)\bcash\s*(?:bill|memo)\b", text):
            return True
        if parsed.average_confidence and parsed.average_confidence < self.settings.min_ocr_confidence:
            return True
        low = sum(1 for line in parsed.lines if line.confidence < 0.55)
        if parsed.lines and (low / len(parsed.lines)) >= 0.35:
            return True
        # Amount-ish OCR garbage near TOTAL/BAL
        if re.search(r"(?i)\b(?:total|bal|amount)\b.{0,20}\d+[oOlI]\d*", text):
            return True
        # Devanagari / heavily fragmented scans (Hindi vegetable bills etc.)
        if re.search(r"[\u0900-\u097F]", text) or text.count("\n") >= 25 and len(text) < 900:
            return True
        return False

    def _ocr_crop_text(self, crop: Image.Image) -> str:
        """Light OCR on a date/total crop (no nested structure/TrOCR recursion)."""
        try:
            result = self.run_ocr(crop, preprocess=True, use_structure=False, use_trocr=False)
            text = result.text or ""
            # Optional TrOCR on tiny amount/date crops when digits still missing
            if self.settings.enable_trocr_fallback and not re.search(r"\d{3,}", text):
                alt = self._trocr_recognize(crop)
                if alt:
                    text = f"{text}\n{alt}".strip()
            return text
        except Exception as exc:  # noqa: BLE001
            logger.warning("Region crop OCR failed: %s", exc)
            return ""

    def enrich_with_region_crops(self, image: Image.Image, parsed: OcrPageResult) -> OcrPageResult:
        """
        Re-OCR header (dates) and bottom-right (totals) crops.
        Full-page English OCR often misses handwritten totals on Hindi/cash bills;
        these focused crops recover values like 7790 / 13465 / 6-Apr-26.
        """
        if image is None:
            return parsed
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        w, h = rgb.size
        if w < 80 or h < 80:
            return parsed

        regions = {
            "header": (0, 0, w, int(h * 0.30)),
            "total_br": (int(w * 0.45), int(h * 0.55), w, h),
            "date_tr": (int(w * 0.50), 0, w, int(h * 0.28)),
        }
        extras: list[str] = []
        for name, box in regions.items():
            left, top, right, bottom = box
            if right - left < 40 or bottom - top < 30:
                continue
            crop = rgb.crop((left, top, right, bottom))
            text = self._ocr_crop_text(crop)
            if not text or not text.strip():
                continue
            body = text.strip()
            # Help amount extractors: bare handwritten totals often lack a TOTAL label
            if name.startswith("total") and re.search(r"\d{3,}", body):
                if not re.search(r"(?i)\btotal\b", body):
                    body = f"TOTAL {body}"
            extras.append(f"[{name}]\n{body}")

        if not extras:
            return parsed

        block = "\n\n--- REGION_OCR ---\n" + "\n\n".join(extras)
        # Avoid duplicating identical merges
        if "--- REGION_OCR ---" in (parsed.text or ""):
            return parsed
        parsed.text = (parsed.text or "") + block
        parsed.engine = f"{parsed.engine}+regions"
        parsed.warnings.append(f"Merged {len(extras)} date/total region OCR crop(s)")
        return parsed

    @staticmethod
    def _is_better_ocr_replacement(original: str, alternate: str) -> bool:
        """Accept TrOCR only when it preserves or improves date/amount structure."""
        orig = (original or "").strip()
        alt = (alternate or "").strip()
        if not alt:
            return False
        date_re = re.compile(r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}")
        amount_re = re.compile(r"\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?")
        if date_re.search(orig) and not date_re.search(alt):
            return False
        if amount_re.search(orig) and not amount_re.search(alt) and re.search(r"(?i)total|amount|bal", orig):
            return False
        # Prefer alternate when original is clearly garbled and alt has digits
        if re.search(r"[^\w\s.,/:\-₹]", orig) and re.search(r"\d", alt):
            return True
        if len(alt) >= 2 and alt.lower() != orig.lower():
            return True
        return False

    def _apply_trocr_on_low_confidence(
        self,
        image: Image.Image,
        parsed: OcrPageResult,
    ) -> OcrPageResult:
        if not self.settings.enable_trocr_fallback:
            return parsed
        if self._ensure_trocr() is None:
            parsed.warnings.append("TrOCR fallback enabled but model unavailable")
            return parsed

        # Prioritize amount/date lines — full-page TrOCR is too slow on CPU
        candidates: list[tuple[int, OcrLine]] = []
        for idx, line in enumerate(parsed.lines):
            important = bool(re.search(r"(?i)\b(total|balance|bal\.?|amount|date|rs\.?|grand)\b", line.text))
            noisy = bool(re.search(r"[oOlI]|[^\w\s.,/:\-₹]", line.text)) or line.confidence < 0.62
            if line.bbox and (important or (noisy and line.confidence < 0.55)):
                priority = 0 if important else 1
                candidates.append((priority, idx, line))
        candidates.sort(key=lambda row: (row[0], row[2].confidence))
        retry_idxs = {idx for _, idx, _ in candidates[:8]}

        improved: list[OcrLine] = list(parsed.lines)
        changed = 0
        width, height = image.size
        for idx in retry_idxs:
            line = parsed.lines[idx]
            try:
                xs = [float(p[0]) for p in line.bbox or []]
                ys = [float(p[1]) for p in line.bbox or []]
                if not xs or not ys:
                    continue
                pad = 4
                left = max(0, int(min(xs)) - pad)
                top = max(0, int(min(ys)) - pad)
                right = min(width, int(max(xs)) + pad)
                bottom = min(height, int(max(ys)) + pad)
                if right - left < 8 or bottom - top < 8:
                    continue
                crop = image.crop((left, top, right, bottom))
                alt = self._trocr_recognize(crop)
                if not alt or alt.lower() == line.text.lower():
                    continue
                # Do not replace a readable date/amount with worse handwriting guess
                if self._is_better_ocr_replacement(line.text, alt):
                    improved[idx] = OcrLine(
                        text=alt,
                        confidence=max(line.confidence, 0.7),
                        bbox=line.bbox,
                    )
                    changed += 1
            except Exception:  # noqa: BLE001
                continue

        if changed:
            parsed.warnings.append(f"TrOCR re-read {changed} low-confidence line(s)")
            parsed.lines = improved
            parsed.text = self._lines_to_text(improved)
            parsed.average_confidence = (
                sum(line.confidence for line in improved) / len(improved) if improved else 0.0
            )
            parsed.engine = f"{parsed.engine}+trocr"
        return parsed

    def run_ocr(
        self,
        image: Image.Image,
        *,
        preprocess: bool = True,
        use_structure: Optional[bool] = None,
        use_trocr: Optional[bool] = None,
    ) -> OcrPageResult:
        import numpy as np

        self._ensure_ocr()
        working = self.image_service.preprocess(image) if preprocess else image
        if working.mode != "RGB":
            arr = np.array(working.convert("RGB"))
            rgb_image = working.convert("RGB")
        else:
            arr = np.array(working)
            rgb_image = working

        try:
            result = self._ocr.predict(arr)
        except Exception:
            try:
                result = self._ocr.ocr(arr)
            except TypeError:
                result = self._ocr.ocr(arr, cls=True)
        parsed = self._parse_paddle_result(result)

        want_structure = (
            self.settings.enable_structure_ocr if use_structure is None else use_structure
        )
        if want_structure:
            structure = self.run_structure(rgb_image)
            if structure:
                parsed.structure_text = structure.get("structure_text") or ""
                parsed.table_text = structure.get("table_text") or ""
                extras = []
                if parsed.table_text:
                    extras.append(parsed.table_text)
                if parsed.structure_text and parsed.structure_text not in parsed.text:
                    extras.append(parsed.structure_text)
                if extras:
                    parsed.text = parsed.text + "\n\n--- STRUCTURE ---\n" + "\n".join(extras)
                    parsed.engine = f"{parsed.engine}+structure"
                    parsed.warnings.append("Merged PP-StructureV3 table/layout text")

        want_trocr = self.settings.enable_trocr_fallback if use_trocr is None else use_trocr
        if want_trocr and self._looks_handwritten_or_noisy(parsed):
            # TrOCR works better on lightly enhanced RGB crops from original page
            trocr_image = self.image_service.preprocess(image, for_handwriting=True)
            if trocr_image.mode != "RGB":
                trocr_image = trocr_image.convert("RGB")
            parsed = self._apply_trocr_on_low_confidence(trocr_image, parsed)

        if parsed.average_confidence and parsed.average_confidence < self.settings.min_ocr_confidence:
            parsed.warnings.append(
                f"Average OCR confidence {parsed.average_confidence:.2f} "
                f"below threshold {self.settings.min_ocr_confidence}"
            )
        return parsed
