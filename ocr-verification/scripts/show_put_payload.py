import copy
import json
from pathlib import Path

import httpx

from app.config import get_settings

tid = "6af5b424a051c009218ae256"
settings = get_settings()
get_url = (
    f"{settings.finance_api_base_url.rstrip('/')}"
    f"{settings.transaction_get_path.format(transaction_id=tid)}"
)
put_url = (
    f"{settings.finance_api_base_url.rstrip('/')}"
    f"{settings.transaction_update_path.format(transaction_id=tid)}"
)

raw = httpx.get(get_url, timeout=60).json()
payload = copy.deepcopy(raw)
payload[settings.verified_flag_field] = settings.verified_flag_value

out = Path("temp") / f"put_payload_{tid}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

print("GET URL:", get_url)
print("PUT URL:", put_url)
print(
    "Flag on GET:",
    settings.verified_flag_field,
    "=",
    raw.get(settings.verified_flag_field),
)
print(
    "Flag on PUT:",
    settings.verified_flag_field,
    "=",
    payload.get(settings.verified_flag_field),
)
print("Saved full PUT body:", out.resolve())
print(
    json.dumps(
        {
            "_id": payload.get("_id"),
            "amount": payload.get("amount"),
            "date": payload.get("date"),
            "narration": payload.get("narration"),
            "entryStatus": payload.get("entryStatus"),
            "voucherType": payload.get("voucherType"),
            "attachments": payload.get("attachments"),
            "isTransactionVerifyByOCR": payload.get("isTransactionVerifyByOCR"),
        },
        indent=2,
    )
)