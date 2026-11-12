from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PIL import Image


def pil_to_cv(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def cv_to_pil(image: np.ndarray) -> Image.Image:
    if len(image.shape) == 2:
        return Image.fromarray(image)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def estimate_skew_angle(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
    if lines is None:
        return 0.0
    angles = []
    for rho_theta in lines[:50]:
        rho, theta = rho_theta[0]
        angle = (theta * 180 / np.pi) - 90
        if -45 < angle < 45:
            angles.append(angle)
    if not angles:
        return 0.0
    return float(np.median(angles))


def rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.3:
        return image
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def preprocess_for_ocr(
    image: Image.Image,
    *,
    max_side: int = 2200,
    deskew: bool = True,
    enhance: bool = True,
) -> Image.Image:
    cv_img = pil_to_cv(image)
    h, w = cv_img.shape[:2]
    scale = 1.0
    longest = max(h, w)
    if longest > max_side:
        scale = max_side / float(longest)
        cv_img = cv2.resize(cv_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    elif longest < 1000:
        scale = 1000 / float(longest)
        cv_img = cv2.resize(cv_img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    if deskew:
        angle = estimate_skew_angle(gray)
        gray = rotate_image(gray, angle)
    if enhance:
        gray = cv2.bilateralFilter(gray, 7, 50, 50)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    return Image.fromarray(gray)


def load_image(path: str) -> Image.Image:
    return Image.open(path).convert("RGB")