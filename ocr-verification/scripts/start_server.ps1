# Single clean OCR API process (NO --reload).
# Port 8000 is currently occupied by a stuck old process that cannot be killed
# without reboot — so we serve on 8010. Point Angular to http://127.0.0.1:8010
$ErrorActionPreference = "Stop"
Set-Location "D:\Document OCR\ocr-verification"

$port = 8010

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match "uvicorn.*app\.main") } |
  ForEach-Object { taskkill /F /PID $_.ProcessId /T 2>$null }

Start-Sleep -Seconds 1

Write-Host "Starting OCR API on http://127.0.0.1:$port (extractorBuild must appear on /health)"
& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
