# OCR Transaction Verification Service

Local FastAPI service that verifies finance transactions against supporting documents using:

- Digital PDF text extraction (`pypdfium2`, non-AGPL)
- Local OCR for scanned pages (PaddleOCR + selective PP-StructureV3 tables + TrOCR handwriting fallback)
- Document types: Cash Memo, GRN, UTR statement, Tax Invoice, Receipt, Voucher, Bill, Challan, plus a **generic fallback** so unknown/OtherType PDFs still yield amount & date
- Amount / date / party / description comparison with `Decimal` arithmetic
- Status update **only** when decision is `VERIFIED`, by appending `OCR` to narration on the existing transaction PUT payload

Angular integration can be added later. For now, call the APIs with a transaction ID.

## License choices (free / open source)

| Library | License | Notes |
|---------|---------|-------|
| FastAPI / Uvicorn / Pydantic / httpx | MIT | OK |
| pypdfium2 | Apache-2.0 / BSD-style (PDFium) | Used instead of **PyMuPDF (AGPL)** |
| OpenCV / NumPy / Pillow | Apache / BSD / HPND | OK |
| rapidfuzz | MIT | OK |
| PaddlePaddle / PaddleOCR | Apache-2.0 | Primary OCR |
| PP-StructureV3 (`paddlex[ocr]`) | Apache-2.0 | Table/layout enrichment when totals missing |
| TrOCR (`transformers` + `torch`) | MIT model + Apache | Handwriting / noisy cash-bill line fallback |

No paid cloud OCR APIs are used.

## Windows setup

### 1) Python

Python **3.12.10** is already installed on this machine. If needed elsewhere:

```powershell
winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements
```

### 2) Create virtual environment

```powershell
cd "D:\Document OCR\ocr-verification"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 3) Install base dependencies

```powershell
pip install -r requirements.txt
```

### 4) Install PaddlePaddle CPU + PaddleOCR (required for scanned docs)

```powershell
python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddleocr[doc-parser]==3.2.0" "paddlex[ocr]==3.2.1"
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install transformers
```

First run downloads OCR / structure / TrOCR models (internet required once). After that, processing is local.

Useful `.env` flags:

```env
ENABLE_STRUCTURE_OCR=true
ENABLE_TROCR_FALLBACK=true
STRUCTURE_OCR_FOR_ALL_PAGES=false
```

### 5) Configure `.env`

Copy `.env.example` to `.env` if needed. Current defaults:

- Finance API: `https://finance.api.gurukul.ninja` (staging)
- File API: `https://files.api.gurukul.ninja` (staging)
- Auth: Angular forwards `Authorization` + `AppId`; optional `API_AUTH_TOKEN` for Swagger

### Production data testing

1. Put production hosts in `.env`:

```env
FINANCE_API_BASE_URL=https://finance.api.<your-prod-host>
FILE_API_BASE_URL=https://files.api.<your-prod-host>
UPDATE_ON_VERIFY=false
BATCH_TEST_TRANSACTION_ID=
```

2. Restart API on 8010 (`.\scripts\start_server.ps1`).
3. From Angular (logged into **production**), call batch/single verify against `http://127.0.0.1:8010` so the real Bearer token is forwarded.
4. Review decisions with `UPDATE_ON_VERIFY=false` (no narration PUT).
5. When results look good, set `UPDATE_ON_VERIFY=true` and optionally limit with `BATCH_TEST_TRANSACTION_ID=<one-id>` before a full batch.

### 6) Start API

```powershell
# Prefer the no-reload starter (avoids duplicate/stale servers):
.\scripts\start_server.ps1
```

Or manually:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Swagger UI: [http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)

Confirm the live build before testing:

```powershell
curl http://127.0.0.1:8010/health
# must include "version":"1.1.0" and "extractorBuild":"2026-11-13-amount-words-v2"
```

> If Angular still calls port **8000**, it may hit a stuck old process. Point it to **8010**, or reboot Windows to free 8000.

### 7) Useful endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/transaction-verification/batch` | Batch verify by company/period/modifiedDate |
| POST | `/api/transaction-verification/{transactionId}/verify` | Single transaction verify |
| GET | `/api/transaction-verification/{transactionId}/result` | Latest result |
| GET | `/api/transaction-verification/{transactionId}/history` | History |
| POST | `/api/ocr/extract` | Upload a file and extract |
| POST | `/api/ocr/extract-sample/{sample_name}` | Extract from `./samples` |
| GET | `/health` | Health check |

### 8) Batch verify (recommended)

```http
POST http://127.0.0.1:8000/api/transaction-verification/batch
Content-Type: application/json
Authorization: Bearer <same token as Finance Angular>
AppId: <same client id as Finance Angular>

{
  "companyId": "60654665e169c893412eae7e",
  "modifiedDate": "2026-11-01",
  "periodId": 79
}
```

Angular Transaction grid: toolbar **OCR Document Verification** button calls this endpoint with the logged-in user's bearer token.

Flow:
1. Calls Finance `POST /Transactions/GetAll` with `{ companyId, periodId, modifiedDate }` (same as Angular `getAllTransactions`)
2. For each transaction with attachments, downloads all `fileId`s via `/File/DownloadOCR/{fileId}`
3. Extracts + matches amount/date
4. PUT appends `OCR` to narration only when verified (does not set `isTransactionVerifyByOCR` for now)
5. Skips PUT on failure/error and continues
6. Emails summary to `rutvik.d@gurukul.org` using **free Gmail SMTP only** (no paid email API). If SMTP is disabled, a draft is saved under `temp/last_batch_email.txt`.

Free Gmail setup:

```env
SMTP_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your.email@gmail.com
SMTP_PASSWORD=your-16-char-app-password
MAIL_TO=rutvik.d@gurukul.org
```

Create the App Password at: Google Account → Security → 2-Step Verification → App passwords.

### 9) Single transaction verify

1. `POST http://127.0.0.1:8000/api/transaction-verification/<TRANSACTION_ID>/verify`
2. Body optional: `{ "force": false }`
3. Service loads transaction + attachment `fileId`s from Finance API, downloads files, extracts, compares, and only then may PUT:

```json
{ "...existing transaction fields...": "...", "narration": "... OCR" }
```

### 10) Run tests

```powershell
pytest -q
```

## Verification rules implemented

- Authoritative values always come from Finance API (not Angular)
- Amount comparison uses `Decimal` + configurable tolerance
- Date comparison supports `DD-MM-YYYY`, `DD/MM/YYYY`, short year, ISO
- UTR statements: select payment row by **amount + payment date** (UTR used as extra hint when present)
- Conflicting document amounts => `MANUAL_REVIEW`
- Low OCR confidence / ambiguous party or description => `MANUAL_REVIEW`
- Only `VERIFIED` appends `OCR` to narration via PUT
- Concurrent verify for same transaction is blocked
- Temp files cleaned after processing

## Sample documents in `/samples`

1. `HYD_OtherType_2026-2027_10250.pdf` — scanned Cash Memo + GRN (OCR required)
2. `HYD_Payment_UTRStatement.pdf` — digital NEFT/UTR statement (text extraction)

## Project structure

```text
ocr-verification/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── api/
│   ├── services/
│   ├── validators/
│   ├── models/
│   └── utils/
├── samples/
├── scripts/
├── tests/
├── temp/
├── requirements.txt
├── .env.example
└── README.md
```

## Notes / limitations

- OCR accuracy is not 100%, especially for handwriting.
- Transaction party name is taken from `bankTransactionDetails[].payeeName` when present; otherwise party check is skipped.
- Auth is intentionally disabled for development until you provide the token contract.
- Angular AG Grid wiring was skipped per your request; use Swagger/Postman with transaction IDs for now.