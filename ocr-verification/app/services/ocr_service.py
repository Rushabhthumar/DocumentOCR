from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
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
    warnings: list[str]


class OcrService:
    """Local OCR via PaddleOCR. Lazy-loaded so API can start without models downloaded."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.image_service = ImageService()
        self._ocr = None
        self._structure = None
        self._lock = threading.Lock()
        self._init_error: Optional[str] = None

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
                # PaddleOCR 3.x API — keep preprocessor light for Windows CPU
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
            return self._structure
        with self._lock:
            if self._structure is not None:
                return self._structure
            try:
                from paddleocr import PPStructureV3

                device = "gpu" if self.settings.use_gpu else "cpu"
                self._structure = PPStructureV3(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    device=device,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("PP-StructureV3 unavailable: %s", exc)
                self._structure = False
        return self._structure if self._structure not in (None, False) else None

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

            # Older ocr() formats: list of [box, (text, score)]
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

        text = "\n".join(line.text for line in lines)
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

    def run_ocr(self, image: Image.Image, *, preprocess: bool = True) -> OcrPageResult:
        import numpy as np

        self._ensure_ocr()
        working = self.image_service.preprocess(image) if preprocess else image
        # PaddleOCR 3.x accepts numpy.ndarray or file path, not PIL Image
        if working.mode != "RGB":
            arr = np.array(working.convert("RGB"))
        else:
            arr = np.array(working)
        try:
            result = self._ocr.predict(arr)
        except Exception:
            try:
                result = self._ocr.ocr(arr)
            except TypeError:
                result = self._ocr.ocr(arr, cls=True)
        parsed = self._parse_paddle_result(result)
        if parsed.average_confidence and parsed.average_confidence < self.settings.min_ocr_confidence:
            parsed.warnings.append(
                f"Average OCR confidence {parsed.average_confidence:.2f} "
                f"below threshold {self.settings.min_ocr_confidence}"
            )
        return parsed

    def run_structure(self, image: Image.Image) -> Optional[Any]:
        pipeline = self._ensure_structure()
        if pipeline is None:
            return None
        try:
            return pipeline.predict(image)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Structure OCR failed: %s", exc)
            return None