from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
import threading
from acr1281_reader import ACR1281Service

app = Flask(__name__)
app.config['SECRET_KEY'] = 'acr1281-secret'
CORS(app)
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
    card = reader_service.read_contactless_uid()
    if not card:
        card = reader_service.read_contact_card()
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

@socketio.on('dump_card')
def handle_dump_card():
    dump = reader_service.dump_card_memory()
    socketio.emit('dump_result', dump)

@app.route('/api/dump')
def api_dump():
    return jsonify(reader_service.dump_card_memory())

if __name__ == '__main__':
    # Background thread for continuous reading
    t = threading.Thread(target=reader_service.loop, daemon=True)
    t.start()
    print("="*50)
    print("ACR1281 Web App Running")
    print("Open: http://localhost:5000")
    print("API: http://localhost:5000/api/read-once")
    print("="*50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
