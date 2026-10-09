"""
ACR1281U-C1 Dual Reader - Complete Handler
- Contactless Interface: Reads NFC / MIFARE UID, data blocks
- Contact Interface: Reads Smart Cards (ATR, etc)
Works with pyscard (PC/SC)
"""

import time
from smartcard.System import readers
from smartcard.util import toHexString, toBytes
from smartcard.Exceptions import NoCardException, CardConnectionException

class ACR1281Service:
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.last_uid = None
        self.last_atr = None
        self.log_history = []
        self.max_logs = 100

    def emit_log(self, message, level="info"):
        """Emit real-time log event to frontend and record history"""
        entry = {
            "timestamp": time.strftime("%H:%M:%S"),
            "message": message,
            "level": level  # 'info', 'success', 'warning', 'error'
        }
        self.log_history.append(entry)
        if len(self.log_history) > self.max_logs:
            self.log_history.pop(0)
        if self.socketio:
            self.socketio.emit('log_event', entry)

    def list_readers(self):
        """List available smart card readers (deduplicated)"""
        try:
            r = readers()
            seen = set()
            result = []
            for reader in r:
                name = str(reader)
                if name not in seen:
                    seen.add(name)
                    result.append(name)
            return result
        except Exception as e:
            print(f"[ACR1281] PCSC error: {e}")
            return []

    def get_status_payload(self):
        """Connection status specifically for ACR1281"""
        all_readers = self.list_readers()
        acr_readers = [r for r in all_readers if "ACR128" in r.upper() or "ACS" in r.upper()]
        is_acr1281 = len(acr_readers) > 0

        if is_acr1281:
            picc = next((r for r in acr_readers if "PICC" in r.upper()), acr_readers[0])
            device_name = picc
            status_msg = "ACR1281 Ready"
        elif all_readers:
            # Other device found (like internal Broadcom)
            device_name = f"{all_readers[0]} (ACR1281 Not Connected)"
            status_msg = "Connect ACR1281 Reader"
        else:
            device_name = "ACR1281 Not Found - Check Connection"
            status_msg = "Connect ACR1281 Reader"

        return {
            "connected": is_acr1281,  # Only TRUE if ACR1281 is connected!
            "is_acr1281": is_acr1281,
            "device": device_name,
            "readers": acr_readers if is_acr1281 else all_readers,
            "message": status_msg
        }

    def get_acr1281_reader(self, prefer_contactless=True):
        """Get ACR1281 reader handle specifically (PICC for contactless, ICC for contact)"""
        try:
            all_readers = readers()
            contactless = None
            contact = None
            for r in all_readers:
                name = str(r).upper()
                if "ACR128" in name or "ACS" in name:
                    # Ignore internal SAM slot
                    if "SAM" in name:
                        continue
                    if "PICC" in name or "CONTACTLESS" in name:
                        contactless = r
                    elif "ICC" in name:
                        contact = r
            if prefer_contactless:
                return contactless or contact
            else:
                return contact or contactless
        except Exception as e:
            print(f"Reader error: {e}")
            return None

    def read_contactless_uid(self):
        """Read UID from contactless card (MIFARE, NTAG, etc)"""
        try:
            reader = self.get_acr1281_reader(prefer_contactless=True)
            if not reader:
                return None
            conn = reader.createConnection()
            conn.connect()
            
            # APDU: Get UID - Works on ACR1281
            GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]
            data, sw1, sw2 = conn.transmit(GET_UID)
            
            if sw1 == 0x90 and sw2 == 0x00:
                uid_hex = ''.join(f'{b:02X}' for b in data)
                uid_formatted = ':'.join(f'{b:02X}' for b in data)
                
                # Get ATR for extra info
                atr = toHexString(conn.getATR())
                
                # Detect card type from ATR / UID length
                card_type = self.detect_card_type(uid_hex, atr)
                
                return {
                    "interface": "Contactless (PICC)",
                    "reader": str(reader),
                    "uid": uid_hex,
                    "uid_formatted": uid_formatted,
                    "uid_decimal": str(int(uid_hex, 16)) if len(uid_hex) <= 16 else uid_hex,
                    "atr": atr,
                    "type": card_type,
                    "protocol": "T=1" if "3B" in atr else "T=0",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "raw_data": data
                }
            else:
                # No card or error
                return None
                
        except NoCardException:
            return None
        except CardConnectionException:
            return None
        except Exception as e:
            # print(f"[DEBUG] Contactless read: {e}")
            return None

    def read_contact_card(self):
        """Read Contact Smart Card (if inserted in ACR1281 slot)"""
        try:
            reader = self.get_acr1281_reader(prefer_contactless=False)
            # Find contact reader (usually reader 1)
            all_r = readers()
            contact_reader = None
            for r in all_r:
                if "ACR128" in str(r).upper() and (" 1" in str(r) or "ICC" in str(r).upper()):
                    contact_reader = r
                    break
            if not contact_reader:
                return None
                
            conn = contact_reader.createConnection()
            conn.connect()
            atr = toHexString(conn.getATR())
            
            return {
                "interface": "Contact (ICC)",
                "reader": str(contact_reader),
                "uid": "N/A (Contact Card)",
                "uid_formatted": atr[:20] + "...",
                "atr": atr,
                "type": "Contact Smart Card / EMV / SIM",
                "protocol": "T=0/T=1",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        except:
            return None

    def detect_card_type(self, uid, atr):
        if len(uid) == 8: return "MIFARE Classic 1K (4-byte UID)"
        if len(uid) == 14: return "MIFARE Ultralight / NTAG (7-byte UID)"
        if "MIFARE" in atr: return "MIFARE Card"
        if "3B8F8001" in atr.replace(" ",""): return "MIFARE Classic / NTAG"
        return f"ISO14443 Type A - {len(uid)//2} byte UID"

    def read_mifare_block(self, block_number=4, key_type=0x60, key=[0xFF]*6):
        """Optional: Read data from MIFARE block (after UID)"""
        try:
            reader = self.get_acr1281_reader()
            conn = reader.createConnection()
            conn.connect()
            # Load key
            LOAD_KEY = [0xFF, 0x82, 0x00, 0x00, 0x06] + key
            data, sw1, sw2 = conn.transmit(LOAD_KEY)
            # Authenticate
            AUTH = [0xFF, 0x86, 0x00, 0x00, 0x05, 0x01, 0x00, block_number, key_type, 0x00]
            data, sw1, sw2 = conn.transmit(AUTH)
            if sw1 != 0x90:
                return None
            # Read block
            READ = [0xFF, 0xB0, 0x00, block_number, 0x10]
            data, sw1, sw2 = conn.transmit(READ)
            if sw1 == 0x90:
                return ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data)
        except Exception:
            pass
        return None

    def loop(self):
        """Main loop for webapp"""
        active_r = self.get_acr1281_reader(prefer_contactless=True)
        print(f"[ACR1281] Active Contactless Reader: {active_r}")
        print("[ACR1281] Service started - Tap card on ACR1281...")

        self.emit_log("ACR1281 Reader Service started", "info")
        if active_r:
            self.emit_log(f"Connected: {active_r}", "success")
            self.emit_log("Contactless antenna active - Waiting for card...", "info")
        else:
            self.emit_log("No ACR1281 reader found. Please check USB connection.", "error")

        last_connected_state = (active_r is not None)
        check_counter = 0

        while True:
            # Check connection status change every ~1.5 seconds
            check_counter += 1
            if check_counter >= 5:
                check_counter = 0
                status = self.get_status_payload()
                if status["connected"] != last_connected_state:
                    last_connected_state = status["connected"]
                    if self.socketio:
                        self.socketio.emit('reader_status', status)
                    if status["connected"]:
                        self.emit_log(f"ACR1281 Reconnected: {status['device']}", "success")
                        self.emit_log("Antenna ready - Waiting for card...", "info")
                    else:
                        self.emit_log("ACR1281 Disconnected! Please check USB cable.", "error")

            # Try contactless first (most common)
            card = self.read_contactless_uid()
            if not card:
                card = self.read_contact_card()
            
            if card and card.get('uid') != self.last_uid:
                self.last_uid = card.get('uid')
                uid_fmt = card.get('uid_formatted', card.get('uid'))
                uid_dec = card.get('uid_decimal', '')
                ctype = card.get('type', '')
                print(f"\n[ACR1281] >>> CARD DETECTED! UID: {uid_fmt} (Dec: {uid_dec}) | Type: {ctype}\n")
                self.emit_log(f"CARD TAPPED -> UID: {uid_fmt} (Dec: {uid_dec}) | Type: {ctype}", "success")
                if self.socketio:
                    self.socketio.emit('card_detected', card)
                time.sleep(1.5) # Debounce
            elif not card:
                if self.last_uid is not None:
                    # Card was just removed
                    self.emit_log("Card removed from reader", "warning")
                    self.last_uid = None
                if self.socketio:
                    self.socketio.emit('waiting_for_card', {})
            time.sleep(0.3)
