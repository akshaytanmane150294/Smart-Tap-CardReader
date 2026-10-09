
# ACR1281 Card Reader - Python Web App (VENV Setup)

## 📁 Project Structure
```
acr1281_full_project/
├── venv/                  # Virtual environment (setup ke baad banega)
├── templates/
│   └── index.html         # Website UI
├── static/
├── app.py                 # Main Flask app
├── acr1281_reader.py      # ACR1281 hardware driver
├── models.py              # Database model
├── requirements.txt       # Saari libraries
├── setup_venv.sh          # Linux/Mac setup script
├── setup_venv.bat         # Windows setup script
└── .env.example
```

## 🚀 Installation - 1 Command

### Windows:
1. Double click `setup_venv.bat`
2. Fir `venv\Scripts\activate`
3. `python app.py`

### Linux / Mac:
```bash
chmod +x setup_venv.sh
./setup_venv.sh
source venv/bin/activate
python app.py
```

### Manual (Sabhi OS):
```bash
# 1. Venv banao
python -m venv venv

# 2. Activate karo
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Libraries install karo
pip install -r requirements.txt

# 4. Run karo
python app.py
```

Open: http://localhost:5000

## 📦 requirements.txt me kya hai?
- flask, flask-socketio -> Web server
- pyscard -> ACR1281 reader ke liye
- pyserial -> Serial readers ke liye
- sqlalchemy -> Database ke liye
- eventlet -> Real-time ke liye
Sab pinned version ke saath taaki error na aaye.

## 🔧 VENV kyu zaroori hai?
Bina venv ke system python kharab ho sakta hai. Venv alag folder banata hai jisme sirf is project ki libraries hoti hain.

## ❓ Problem?
- ACR1281 not found -> Driver install karo ACS site se
- pyscard error on Linux -> sudo apt install pcscd libpcsclite-dev swig
