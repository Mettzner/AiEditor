# Sobe API (8000), worker e frontend (3000), cada um numa janela.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root "server\.venv\Scripts\python.exe"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\server'; & '$py' -m uvicorn app.main:app --port 8000 --reload" -WindowStyle Normal
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\server'; & '$py' -m app.worker" -WindowStyle Normal
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\web'; npm run dev" -WindowStyle Normal
Start-Sleep -Seconds 4
Start-Process "http://localhost:3000"
