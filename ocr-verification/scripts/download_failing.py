import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx

from app.config import get_settings

s = get_settings()
out_dir = Path("temp")
out_dir.mkdir(exist_ok=True)
for fid in ["11656", "11657", "11659", "11663", "11660", "11661"]:
    url = f"{s.file_api_base_url.rstrip('/')}{s.file_download_path.format(file_id=fid)}"
    try:
        r = httpx.get(url, timeout=60, headers=s.auth_headers())
        print(fid, r.status_code, r.headers.get("content-type"), len(r.content))
        if r.status_code == 200 and len(r.content) > 1000:
            out = out_dir / f"live_{fid}.pdf"
            out.write_bytes(r.content)
            print(" saved", out)
        else:
            print(" body", r.text[:200])
    except Exception as e:
        print(fid, "ERR", e)
