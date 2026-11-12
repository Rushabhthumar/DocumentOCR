from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image

from app.utils.image_utils import load_image, preprocess_for_ocr


class ImageService:
    def load(self, path: str | Path) -> Image.Image:
        return load_image(str(path))

    def preprocess(self, image: Image.Image, *, for_handwriting: bool = False) -> Image.Image:
        return preprocess_for_ocr(
            image,
            max_side=2400 if for_handwriting else 2200,
            deskew=not for_handwriting,
            enhance=True,
        )

    def save_temp(self, image: Image.Image, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return str(path)