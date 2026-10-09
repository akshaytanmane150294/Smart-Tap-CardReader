@echo off
echo ===================================================
echo  ACR1281 Project - Setup & Service (Windows / PS)
echo ===================================================

:: Check python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH!
    echo Please install Python 3.10 / 3.11 / 3.12 from python.org and add to PATH.
    pause
    exit /b 1
)

:: Ensure Windows Smart Card service is running
echo [1/4] Checking Windows Smart Card Service (SCardSvr)...
sc start SCardSvr >nul 2>&1

:: Create virtual environment
echo [2/4] Creating virtual environment (Windows venv)...
if exist venv\bin (
    echo Detected Linux venv. Removing to create Windows venv...
    rmdir /s /q venv
)
if not exist venv\Scripts\python.exe (
    py -3.12 -m venv venv
)

:: Activate & Upgrade pip
echo [3/4] Activating venv and upgrading pip...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

:: Install requirements
echo [4/4] Installing requirements from requirements.txt...
pip install -r requirements.txt

echo.
echo ===================================================
echo  Setup Complete!
echo ===================================================
echo.
echo To activate in CMD:         venv\Scripts\activate
echo To activate in PowerShell:  .\venv\Scripts\Activate.ps1
echo To run the project:         python3 app.py
echo.
pause
