#!/bin/bash
set -e

echo "======================================"
echo " ACR1281 Project - Setup (Linux/WSL)"
echo "======================================"

# 1. Install System Dependencies
echo "[1/5] Installing system dependencies (SmartCard, USB, SWIG)..."
sudo apt update
sudo apt install -y pcscd libpcsclite-dev swig python3-dev python3.12-venv

# 2. Start SmartCard Daemon
echo "[2/5] Starting pcscd service..."
sudo service pcscd start 2>/dev/null || sudo systemctl start pcscd 2>/dev/null || true

# 3. Create virtual environment
echo "[3/5] Creating virtual environment..."
python3.12 -m venv venv

# 4. Activate & Upgrade pip
echo "[4/5] Activating venv & upgrading pip..."
source venv/bin/activate
pip install --upgrade pip

# 5. Install requirements
echo "[5/5] Installing libraries from requirements.txt..."
pip install -r requirements.txt

echo ""
echo "✅ Setup Complete!"
echo "To activate venv next time: source venv/bin/activate"
echo "To run project: python3 app.py"

