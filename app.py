from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
import threading
from acr1281_reader import ACR1281Service

app = Flask(__name__)
app.config['SECRET_KEY'] = 'acr1281-secret'
CORS(app) #CORS (Cross-Origin Resource Sharing)

# Real-Time / WebSocket communication enable 
# HTTP requests me browser request bhejta hai tabhi server response deta hai (page refresh ya AJAX se). 
# Lekin SocketIO ke zariye server khud client (browser) ko turant data bhej sakta hai bina page refresh kiye — 
# jaise hi koi ACR1281 reader par card tap kare, server turant browser ko event emit kar dega.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

reader_service = ACR1281Service(socketio)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/readers')
def api_readers():
    return jsonify(reader_service.list_readers())

@app.route('/api/read-once')
def api_read_once():
    """REST API - read card once (for normal web app without socket)"""
    # If card is currently resting on reader and already detected
    if reader_service.last_card_data and reader_service.last_uid:
        return jsonify(reader_service.last_card_data)

    card = reader_service.read_contactless_uid()
    if not card:
        card = reader_service.read_contact_card()
    if not card and reader_service.last_card_data and reader_service.last_uid:
        card = reader_service.last_card_data
    return jsonify(card or {"status": "no_card", "message": "Tap card on ACR1281"})

@socketio.on('connect')
def handle_connect():
    status = reader_service.get_status_payload()
    socketio.emit('reader_status', status)
    socketio.emit('log_history', reader_service.log_history)

@socketio.on('read_now')
def handle_read_now():
    card = reader_service.read_contactless_uid()
    if card:
        socketio.emit('card_detected', card)
    else:
        socketio.emit('no_card', {"message": "No card detected - tap card on reader"})


@app.route('/api/scosta/scan')
def api_scosta_scan():
    """Scan card file system according to SCOSTA standard (MF, DFs, EFs, Tag 46)"""
    return jsonify(reader_service.scan_scosta_files())

@socketio.on('scan_scosta')
def handle_scan_scosta():
    result = reader_service.scan_scosta_files()
    socketio.emit('scosta_scan_result', result)

if __name__ == '__main__':
    import os
    print("="*50)
    print("ACR1281 Web App Running")
    print("Open: http://localhost:5000")
    print("API: http://localhost:5000/api/read-once")
    print("API: http://localhost:5000/api/scosta/scan")
    print("="*50)

    # Start reader thread once only
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        t = threading.Thread(target=reader_service.loop, daemon=True)
        t.start()

    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    
