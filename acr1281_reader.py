"""
ACR1281U-C1 Dual Reader - Complete Handler
- Contactless Interface: Reads NFC / MIFARE UID, data blocks
- Contact Interface: Reads Smart Cards (ATR, etc)
Works with pyscard (PC/SC)
"""

import sys
import time
from smartcard.System import readers
from smartcard.util import toHexString, toBytes
from smartcard.Exceptions import NoCardException, CardConnectionException

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

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
                is_ultralight = (len(uid_hex) > 8)
                
                # Read key memory blocks (Sector 0, 1, 2) or pages (Ultralight)
                blocks = []
                try:
                    blocks = self.read_card_preview_blocks(conn, is_ultralight=is_ultralight)
                except:
                    pass

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
                    "raw_data": data,
                    "blocks": blocks
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
        atr_clean = atr.replace(" ", "").upper()
        if uid.startswith("08") and len(uid) == 8:
            return "Random UID Card (ISO14443-A RID / High-Security)"
        if "00010000" in atr_clean:
            return "MIFARE Classic 1K"
        if "00020000" in atr_clean:
            return "MIFARE Classic 4K"
        if "00030000" in atr_clean or len(uid) == 14:
            return "MIFARE Ultralight / NTAG (7-byte UID)"
        if len(uid) == 8:
            return "MIFARE Classic 1K (4-byte UID)"
        return f"ISO14443 Type A ({len(uid)//2}-byte UID)"

    def read_mifare_block_with_keys(self, conn, block_number, keys_to_try=None):
        """Try authenticating and reading a single MIFARE Classic 1K block using 6-byte keys"""
        if keys_to_try is None:
            keys_to_try = [
                [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF],  # Factory Default (80%+ cards)
                [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5],  # MAD Key
                [0xD3, 0xF7, 0xD3, 0xF7, 0xD3, 0xF7],  # NFC Forum
                [0x00, 0x00, 0x00, 0x00, 0x00, 0x00],  # Blank / Zero Key
                [0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5],  # Philips/NXP Default
                [0x4D, 0x3A, 0x99, 0xC3, 0x51, 0xDD],  # Transport Key
                [0x1A, 0x98, 0x2C, 0x7E, 0x45, 0x9A],
                [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF]
            ]

        for key in keys_to_try:
            for key_type in [0x60, 0x61]:  # Key A (0x60), Key B (0x61)
                for key_struct in [0x00, 0x20]:  # Volatile (00), Non-volatile (20)
                    try:
                        # 1. Load 6-byte key into reader memory slot 0x00
                        LOAD_KEY = [0xFF, 0x82, key_struct, 0x00, 0x06] + key
                        _, sw1, sw2 = conn.transmit(LOAD_KEY)
                        if sw1 != 0x90:
                            continue

                        # 2. Authenticate block
                        AUTH = [0xFF, 0x86, 0x00, 0x00, 0x05, 0x01, 0x00, block_number, key_type, 0x00]
                        _, sw1, sw2 = conn.transmit(AUTH)
                        if sw1 == 0x90 and sw2 == 0x00:
                            # 3. Read 16 bytes
                            READ = [0xFF, 0xB0, 0x00, block_number, 0x10]
                            data, sw1, sw2 = conn.transmit(READ)
                            if sw1 == 0x90 and sw2 == 0x00 and len(data) == 16:
                                hex_str = ' '.join(f'{b:02X}' for b in data)
                                ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data)
                                readable = ''.join(chr(b) if 32 <= b <= 126 else '' for b in data).strip()
                                return {
                                    "block": block_number,
                                    "status": "success",
                                    "hex": hex_str,
                                    "ascii": ascii_str,
                                    "readable_text": readable,
                                    "raw": data,
                                    "key_type": "Key A" if key_type == 0x60 else "Key B"
                                }
                    except:
                        pass

        return {
            "block": block_number,
            "status": "locked",
            "hex": "-- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --",
            "ascii": "[LOCKED / CUSTOM KEY]",
            "readable_text": "",
            "raw": []
        }

    def try_read_block(self, conn, block_no, is_ultralight=False):
        """Read block/page based on card architecture (Ultralight vs Classic)"""
        if is_ultralight:
            # MIFARE Ultralight / NTAG - No Auth/Key needed, direct binary read
            # Read 4 bytes (1 page) using FF B0 00 <page> 04
            try:
                READ = [0xFF, 0xB0, 0x00, block_no, 0x04]
                data, sw1, sw2 = conn.transmit(READ)
                if sw1 == 0x90 and sw2 == 0x00 and len(data) >= 4:
                    hex_str = ' '.join(f'{b:02X}' for b in data)
                    ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data)
                    readable = ''.join(chr(b) if 32 <= b <= 126 else '' for b in data).strip()
                    return {
                        "block": block_no,
                        "status": "success",
                        "hex": hex_str,
                        "ascii": ascii_str,
                        "readable_text": readable,
                        "raw": data,
                        "key_type": "Direct Read (No Auth)"
                    }
                else:
                    return {
                        "block": block_no,
                        "status": "locked",
                        "reason": "Password Protected / End of Memory",
                        "hex": "-- -- -- --",
                        "ascii": "[LOCKED / PWD]",
                        "readable_text": "",
                        "raw": []
                    }
            except Exception as e:
                return {
                    "block": block_no,
                    "status": "locked",
                    "reason": str(e),
                    "hex": "-- -- -- --",
                    "ascii": f"[ERR: {e}]",
                    "readable_text": "",
                    "raw": []
                }
        else:
            # MIFARE Classic 1K - Key Auth required
            return self.read_mifare_block_with_keys(conn, block_no)

    def read_card_preview_blocks(self, conn, is_ultralight=False):
        """Read key blocks or pages for instant preview"""
        preview_blocks = []
        if is_ultralight:
            # MIFARE Ultralight / NTAG pages (4 bytes per page)
            target_pages = [
                (0, "Page 0 (UID Part 1)"),
                (1, "Page 1 (UID Part 2)"),
                (2, "Page 2 (Lock Bytes)"),
                (3, "Page 3 (OTP / Config)"),
                (4, "Page 4 (User Data / NDEF)"),
                (5, "Page 5 (User Data)"),
                (6, "Page 6 (User Data)"),
                (7, "Page 7 (User Data)")
            ]
            for p, lbl in target_pages:
                res = self.try_read_block(conn, p, is_ultralight=True)
                res["label"] = lbl
                preview_blocks.append(res)
        else:
            # MIFARE Classic 1K blocks (16 bytes per block)
            target_blocks = [
                (0, "Block 0 (UID / Mfg)"),
                (1, "Block 1 (Sector 0 Data)"),
                (2, "Block 2 (Sector 0 Data)"),
                (4, "Block 4 (Sector 1 Data)"),
                (5, "Block 5 (Sector 1 Data)"),
                (8, "Block 8 (Sector 2 Data)")
            ]
            for b, lbl in target_blocks:
                res = self.try_read_block(conn, b, is_ultralight=False)
                res["label"] = lbl
                preview_blocks.append(res)
        return preview_blocks

    def dump_card_memory(self):
        """Read all memory blocks/pages from the card on ACR1281"""
        try:
            reader = self.get_acr1281_reader(prefer_contactless=True)
            if not reader:
                return {"status": "error", "message": "ACR1281 contactless reader not detected."}

            conn = reader.createConnection()
            conn.connect()

            # Verify card present
            GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]
            data, sw1, sw2 = conn.transmit(GET_UID)
            if sw1 != 0x90:
                return {"status": "error", "message": "No card detected on reader. Place card on antenna."}

            uid_hex = ''.join(f'{b:02X}' for b in data)
            uid_fmt = ':'.join(f'{b:02X}' for b in data)
            is_ultralight = (len(uid_hex) > 8)
            card_type_desc = "MIFARE Ultralight / NTAG" if is_ultralight else "MIFARE Classic 1K"
            self.emit_log(f"Initiating memory dump for {card_type_desc} (UID: {uid_fmt})...", "info")

            sectors = []
            unlocked_count = 0

            if is_ultralight:
                # MIFARE Ultralight / NTAG has pages (4 bytes each).
                # Dump 16 pages (Pages 0 to 15, 64 bytes total), grouped into 4 sections
                total_blocks = 16
                for s in range(4):
                    sec_info = {
                        "sector": s,
                        "title": "System Pages (UID & Config)" if s == 0 else f"User Memory (Pages {s*4}-{s*4+3})",
                        "blocks": []
                    }
                    for b_offset in range(4):
                        page_num = s * 4 + b_offset
                        res = self.try_read_block(conn, page_num, is_ultralight=True)
                        if page_num == 0:
                            res["type"] = "Manufacturer / Serial (Page 0)"
                        elif page_num == 1:
                            res["type"] = "Manufacturer / Serial (Page 1)"
                        elif page_num == 2:
                            res["type"] = "Static Lock Bytes (Page 2)"
                        elif page_num == 3:
                            res["type"] = "OTP Memory (Page 3)"
                        else:
                            res["type"] = f"User Data (Page {page_num})"

                        if res["status"] == "success":
                            unlocked_count += 1
                        sec_info["blocks"].append(res)
                    sectors.append(sec_info)

            else:
                # MIFARE Classic 1K: 16 sectors, 4 blocks each = 64 blocks
                total_blocks = 64
                for s in range(16):
                    sector_info = {
                        "sector": s,
                        "title": "Manufacturer Sector" if s == 0 else f"Data Sector {s}",
                        "blocks": []
                    }
                    for b_offset in range(4):
                        block_num = s * 4 + b_offset
                        block_res = self.try_read_block(conn, block_num, is_ultralight=False)

                        if block_num == 0:
                            block_res["type"] = "Manufacturer Block (UID)"
                        elif b_offset == 3:
                            block_res["type"] = "Sector Trailer (Keys)"
                            # Mask key bytes for trailer
                            if block_res["status"] == "success":
                                raw = block_res["raw"]
                                acc = ' '.join(f'{b:02X}' for b in raw[6:10])
                                block_res["hex"] = f"** ** ** ** ** ** {acc} ** ** ** ** ** **"
                                block_res["ascii"] = "****** ACCESS ******"
                        else:
                            block_res["type"] = "User Data"

                        if block_res["status"] == "success":
                            unlocked_count += 1

                        sector_info["blocks"].append(block_res)

                    sectors.append(sector_info)

            self.emit_log(f"Memory dump complete: {unlocked_count}/{total_blocks} blocks unlocked", "success")

            return {
                "status": "success",
                "uid": uid_hex,
                "uid_formatted": uid_fmt,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_sectors": len(sectors),
                "unlocked_blocks": unlocked_count,
                "total_blocks": total_blocks,
                "sectors": sectors
            }
        except Exception as e:
            err_msg = str(e)
            self.emit_log(f"Card dump error: {err_msg}", "error")
            return {"status": "error", "message": f"Dump failed: {err_msg}"}

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

                print(f"\n=======================================================")
                print(f"[ACR1281] >>> CARD DETECTED!")
                print(f"  UID (Hex):    {uid_fmt}")
                print(f"  UID (Dec):    {uid_dec}")
                print(f"  Card Type:    {ctype}")
                print(f"  ATR:          {card.get('atr', 'N/A')}")
                print(f"-------------------------------------------------------")
                print(f"  Card Memory Blocks:")

                self.emit_log(f"CARD TAPPED -> UID: {uid_fmt} | {ctype}", "success")

                blocks = card.get('blocks', [])
                any_unlocked = False
                for b in blocks:
                    label = b.get('label', f"Block {b.get('block')}")
                    if b.get('status') == 'success':
                        any_unlocked = True
                        h = b.get('hex')
                        asc = b.get('ascii')
                        print(f"  [{label}] {h}  |  {asc}")
                        self.emit_log(f"{label}: {h} | \"{asc}\"", "info")
                    else:
                        print(f"  [{label}] [LOCKED / PROTECTED]")

                if not any_unlocked and blocks:
                    print(f"  [Notice] Data blocks are locked with custom vendor key.")
                    self.emit_log("Card data blocks are protected with custom key", "warning")

                print(f"=======================================================\n")

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
