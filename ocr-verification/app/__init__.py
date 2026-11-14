"""Local OCR + transaction verification service."""

__version__ = "1.1.0"
# Bump when extraction logic changes — exposed on /health so we can confirm the live server.
EXTRACTOR_BUILD = "2026-11-14-upi-generated-on-v5"