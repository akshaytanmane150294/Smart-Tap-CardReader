# PowerShell Setup Script for ACR1281 Project
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " ACR1281 Project - Setup (Windows PowerShell)" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

# 1. Check Python
try {
    $pyVersion = python --version 2>&1
    Write-Host "[✓] Python found: $pyVersion" -ForegroundColor Green
} catch {
    Write-Host "[!] Python not found! Please install Python and check PATH." -ForegroundColor Red
    exit 1
}

# 2. Check & Start Windows Smart Card Service
Write-Host "`n[1/4] Ensuring Windows Smart Card Service (SCardSvr) is running..." -ForegroundColor Yellow
try {
    $service = Get-Service -Name SCardSvr -ErrorAction SilentlyContinue
    if ($service.Status -ne 'Running') {
        Start-Service -Name SCardSvr -ErrorAction SilentlyContinue
    }
    Write-Host "[✓] Smart Card Service status: Running" -ForegroundColor Green
} catch {
    Write-Host "[i] Note: Run PowerShell as Administrator if service fails to start." -ForegroundColor Gray
}

# 3. Create Windows Virtual Environment
Write-Host "`n[2/4] Setting up Windows Virtual Environment..." -ForegroundColor Yellow
if (Test-Path "venv\bin") {
    Write-Host "[i] Found Linux venv, replacing with Windows venv..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "venv"
}

if (-not (Test-Path "venv\Scripts\Activate.ps1")) {
    py -3.12 -m venv venv
    Write-Host "[✓] Created new venv with Python 3.12" -ForegroundColor Green
} else {
    Write-Host "[✓] Existing Windows venv found" -ForegroundColor Green
}

# 4. Activate & Upgrade pip
Write-Host "`n[3/4] Activating venv and upgrading pip..." -ForegroundColor Yellow
& .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# 5. Install requirements
Write-Host "`n[4/4] Installing requirements from requirements.txt..." -ForegroundColor Yellow
pip install -r requirements.txt

Write-Host "`n===================================================" -ForegroundColor Green
Write-Host "  Setup Complete! " -ForegroundColor Green
Write-Host "===================================================" -ForegroundColor Green
Write-Host "`nTo activate venv in PowerShell:`n  .\venv\Scripts\Activate.ps1`n" -ForegroundColor Cyan
Write-Host "To run the app:`n  python3 app.py`n" -ForegroundColor Cyan
