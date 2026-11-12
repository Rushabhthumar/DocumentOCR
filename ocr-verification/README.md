# OCR Transaction Verification Service

Local FastAPI service that verifies finance transactions against supporting documents using:

- Digital PDF text extraction (`pypdfium2`, non-AGPL)
- Local OCR for scanned pages (PaddleOCR / optional PP-StructureV3)
- Amount / date / party / description comparison with `Decimal` arithmetic
- Status update **only** when decision is `VERIFIED`, by setting `isTransactionVerifyByOCR: true` on the existing transaction PUT payload

Angular integration can be added later. For now, call the APIs with a transaction ID.

## License choices (free / open source)

| Library | License | Notes |
|---------|---------|-------|
| FastAPI / Uvicorn / Pydantic / httpx | MIT | OK |
| pypdfium2 | Apache-2.0 / BSD-style (PDFium) | Used instead of **PyMuPDF (AGPL)** |
| OpenCV / NumPy / Pillow | Apache / BSD / HPND | OK |
| rapidfuzz | MIT | OK |
| PaddlePaddle / PaddleOCR | Apache-2.0 | Primary OCR |
| Tesseract (optional) | Apache-2.0 | Fallback only |
| TrOCR (optional) | MIT model + Apache transformers | Handwriting fallback, disabled by default |

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
pip install "paddleocr[doc-parser]==3.0.0"
```

First run downloads OCR models (internet required once). After that, processing is local.

### 5) Configure `.env`

Copy `.env.example` to `.env` if needed. Current defaults:

- Finance API: `https://finance.api.gurukul.ninja`
- File API: `https://files.api.gurukul.ninja`
- `API_AUTH_TOKEN=` empty for local development

When auth is ready, set:

```env
API_AUTH_TOKEN=Bearer <your-token>
```

### 6) Start API

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

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

{
  "companyId": "60654665e169c893412eae7e",
  "modifiedDate": "2026-11-01",
  "periodId": 79
}
```

Flow:
1. Calls Finance `GET /Transactions?periodId=&companyId=&modifiedDate=`
2. For each transaction with attachments, downloads all `fileId`s via `/File/DownloadOCR/{fileId}`
3. Extracts + matches amount/date
4. PUT updates `isTransactionVerifyByOCR=true` and appends `OCR` to narration only when verified
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
{ "...existing transaction fields...": "...", "isTransactionVerifyByOCR": true, "narration": "... OCR" }
```

### 10) Run tests

```powershell
pytest -q
```

### 11) Demo sample PDFs

```powershell
python scripts\demo_extract_samples.py
```

## Verification rules implemented

- Authoritative values always come from Finance API (not Angular)
- Amount comparison uses `Decimal` + configurable tolerance
- Date comparison supports `DD-MM-YYYY`, `DD/MM/YYYY`, short year, ISO
- UTR statements: select payment row by **amount + payment date** (UTR used as extra hint when present)
- Conflicting document amounts => `MANUAL_REVIEW`
- Low OCR confidence / ambiguous party or description => `MANUAL_REVIEW`
- Only `VERIFIED` updates `isTransactionVerifyByOCR`
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