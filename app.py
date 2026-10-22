from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from flask_cors import CORS
import threading, time, os
from acr1281_reader import ACR1281Service

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
service = ACR1281Service()


@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/status')
def status(): return jsonify(service.get_status())

@app.route('/api/scan')
def scan(): 
    return jsonify(service.scan_card() or {"status": "no_card"})


@socketio.on('connect')
def handle_connect():
    socketio.emit('reader_status', service.get_status())
    if service.last_card:
        socketio.emit('card_detected', service.last_card)

def background_loop():
    last_emitted_uid = None
    while True:
        card = service.scan_card()
        if card:
            if card['uid'] != last_emitted_uid:
                socketio.emit('card_detected', card)
                last_emitted_uid = card['uid']
        else:
            if last_emitted_uid is not None:
                socketio.emit('waiting_for_card', {})
                last_emitted_uid = None
        time.sleep(0.5)


if __name__ == '__main__':
    # Thread ek hi baar start
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        threading.Thread(target=background_loop, daemon=True).start()

    socketio.run(app, host='127.0.0.1', port=5000)

