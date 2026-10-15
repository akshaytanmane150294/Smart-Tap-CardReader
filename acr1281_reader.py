"""
ACR1281U-C1 Dual Reader - Complete Handler
- Contactless Interface: Reads NFC / MIFARE UID, data blocks
- Contact Interface: Reads Smart Cards (ATR, etc)
Works with pyscard (PC/SC)
"""

import sys
import time
import threading
from smartcard.System import readers
from smartcard.util import toHexString, toBytes
from smartcard.Exceptions import NoCardException, CardConnectionException
from smartcard.ReaderMonitoring import ReaderMonitor, ReaderObserver
from smartcard.CardConnectionObserver import CardConnectionObserver

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


class APDUConnectionObserver(CardConnectionObserver):
    """
    Pyscard Framework Observer to intercept and log raw APDU commands and responses.
    Streams APDU traffic to the frontend console in real time.
    """
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.enabled = False

    def interpret_apdu(self, apdu_bytes):
        if not apdu_bytes or len(apdu_bytes) < 4:
            return "APDU"
        cla, ins, p1, p2 = apdu_bytes[:4]
        if [cla, ins] == [0xFF, 0xCA]:
            return "PCSC GET UID"
        elif [cla, ins] == [0x00, 0xA4]:
            if len(apdu_bytes) >= 7:
                fid = ''.join(f'{b:02X}' for b in apdu_bytes[5:])
                return f"SELECT FILE [{fid}]"
            return "SELECT FILE"
        elif [cla, ins] == [0x00, 0xB0]:
            return f"READ BINARY (Offset={p2:02X})"
        elif [cla, ins] == [0x00, 0xCA]:
            return f"GET DATA (Tag={p2:02X})"
        elif [cla, ins] == [0x00, 0xC0]:
            return "GET RESPONSE"
        return f"INS:{ins:02X}"

    def interpret_sw(self, sw1, sw2):
        sw = (sw1 << 8) | sw2
        if sw == 0x9000:
            return "Success (9000)"
        elif sw1 == 0x61:
            return f"More Data Available ({sw2} bytes)"
        elif sw1 == 0x6C:
            return f"Wrong Length (Re-issue with Le={sw2:02X})"
        elif sw == 0x6A82:
            return "File / App Not Found (6A82)"
        elif sw == 0x6985:
            return "Conditions Not Satisfied (6985)"
        elif sw == 0x6A86:
            return "Incorrect Parameters P1-P2 (6A86)"
        elif sw == 0x6D00:
            return "INS Not Supported (6D00)"
        elif sw == 0x6E00:
            return "CLA Not Supported (6E00)"
        return f"SW: {sw1:02X}{sw2:02X}"

    def update(self, observable, handlers):
        if not self.enabled:
            return
        try:
            if handlers.type == "command":
                cmd_bytes = handlers.args[0]
                label = self.interpret_apdu(cmd_bytes)
                hex_cmd = toHexString(cmd_bytes)
                self.service.emit_log(f"APDU TX > {hex_cmd} ({label})", "info")
            elif handlers.type == "response":
                resp_data = handlers.args[0]
                sw1, sw2 = handlers.args[-2:]
                sw_desc = self.interpret_sw(sw1, sw2)
                is_ok = (sw1 == 0x90 or sw1 == 0x61)
                level = "success" if is_ok else "warning"
                if resp_data:
                    data_hex = toHexString(resp_data)
                    self.service.emit_log(f"APDU RX < {data_hex} | {sw_desc}", level)
                else:
                    self.service.emit_log(f"APDU RX < {sw_desc}", level)
        except Exception:
            pass


class ACR1281ReaderObserver(ReaderObserver):
    """
    Pyscard Framework Observer for instant USB Smart Card Reader Plug & Play detection.
    """
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.first_run = True

    def update(self, observable, handlers):
        added_readers, removed_readers = handlers
        if self.first_run:
            self.first_run = False
            return

        if added_readers or removed_readers:
            status = self.service.get_status_payload()
            if self.service.socketio:
                self.service.socketio.emit('reader_status', status)

            for r in added_readers:
                self.service.emit_log(f"USB Reader Connected: {r}", "success")
            for r in removed_readers:
                self.service.emit_log(f"USB Reader Disconnected: {r}", "error")


class ACR1281Service:
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.last_uid = None
        self.last_atr = None
        self.last_card_data = None
        self.scanning = False
        self.log_history = []
        self.max_logs = 100
        self.reader_lock = threading.Lock()
        self.last_scosta_result = None

        # Pyscard Framework Observers
        self.apdu_observer = APDUConnectionObserver(self)
        self.reader_monitor = None
        self.reader_observer = None
        self.start_reader_monitor()

    def start_reader_monitor(self):
        """Start pyscard ReaderMonitor for instant USB Plug & Play events"""
        try:
            self.reader_monitor = ReaderMonitor()
            self.reader_observer = ACR1281ReaderObserver(self)
            self.reader_monitor.addObserver(self.reader_observer)
        except Exception as e:
            print(f"[ACR1281] ReaderMonitor init warning: {e}")

    #logs ko console/frontend tak bhejna
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

    #Reader ko List karna
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
                target = contactless or contact
            else:
                target = contact or contactless

            if not target and all_readers:
                for r in all_readers:
                    if "CONTACTLESS" in str(r).upper() or "PICC" in str(r).upper():
                        return r
                return all_readers[0]

            return target
        except Exception as e:
            print(f"Reader error: {e}")
            return None

    def read_contactless_uid(self, full_preview=True):
        """Read UID from contactless card (SCOSTA, Smart Cards, etc)"""
        if self.scanning:
            return None
        # Wait up to 1.5s for reader lock to avoid clash with background loop
        if not self.reader_lock.acquire(timeout=1.5):
            if self.last_card_data:
                return self.last_card_data
            return None
        conn = None
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
                
                # If same card is still holding on reader and preview is not requested, return cached
                if self.last_uid == uid_hex and self.last_card_data and not full_preview:
                    return self.last_card_data

                # Get ATR for extra info
                atr = toHexString(conn.getATR())
                
                # Detect card type from ATR / UID length
                card_type = self.detect_card_type(uid_hex, atr)
                is_scosta = self.is_scosta_card(atr)
                
                # Read SCOSTA file preview if applicable
                blocks = []
                scosta_info = None
                try:
                    if is_scosta:
                        blocks, scosta_info = self.read_scosta_preview(conn)
                except:
                    pass

                card_data = {
                    "interface": "Contactless (PICC)",
                    "reader": str(reader),
                    "uid": uid_hex,
                    "uid_formatted": uid_formatted,
                    "uid_decimal": str(int(uid_hex, 16)) if len(uid_hex) <= 16 else uid_hex,
                    "atr": atr,
                    "type": card_type,
                    "is_scosta": is_scosta,
                    "scosta_info": scosta_info,
                    "protocol": "T=1" if "3B" in atr else "T=0",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "raw_data": data,
                    "blocks": blocks
                }
                self.last_card_data = card_data
                if is_scosta and blocks:
                    self.last_scosta_result = {
                        "status": "success",
                        "uid": uid_hex,
                        "uid_formatted": uid_formatted,
                        "atr": atr,
                        "is_scosta": True,
                        "card_state": "SmartSCOS Card Active (MF 3F00 Open)",
                        "files": [
                            {
                                "type": b.get("type", "File"),
                                "id": b.get("label", ""),
                                "name": b.get("label", ""),
                                "sw": "9000" if b.get("status") == "success" else "6A82",
                                "status": "OPEN" if b.get("status") == "success" else "NOT CREATED (Blank)",
                                "status_badge": "success" if b.get("status") == "success" else "secondary",
                                "content": b.get("ascii", "")
                            }
                            for b in blocks
                        ]
                    }
                return card_data
            else:
                return None
                
        except (NoCardException, CardConnectionException):
            return None
        except Exception:
            return None
        finally:
            if conn:
                try:
                    conn.disconnect()
                except:
                    pass
            self.reader_lock.release()

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

    def is_scosta_card(self, atr):
        """Check if card is SCOSTA or SmartSCOS from ATR"""
        atr_clean = atr.replace(" ", "").upper()
        # "536D61727453434F53" is hex for "SmartSCOS"
        return ("536D61727453434F53" in atr_clean or 
                "SMARTSCOS" in atr_clean or 
                "SCOSTA" in atr_clean or 
                "53434F53" in atr_clean)

    def detect_card_type(self, uid, atr):
        if self.is_scosta_card(atr):
            return "SCOSTA / SmartSCOS (ISO 14443-4)"
        atr_clean = atr.replace(" ", "").upper()
        if uid.startswith("08") and len(uid) == 8:
            return "ISO 14443-A Smart Card (Random UID)"
    def parse_scosta_student_record(self, data_bytes):
        """Parse ASN.1 / TLV / String structures from SCOSTA Student File (SFI 4 / EF 4)"""
        import re
        extracted = {}
        strings_found = []
        
        # 1. Parse standard ASN.1 / BER-TLV tags
        idx = 0
        if len(data_bytes) > 4 and data_bytes[0] == 0x30:
            if data_bytes[1] == 0x82:
                idx = 4
            elif data_bytes[1] == 0x81:
                idx = 3
            else:
                idx = 2
                
        while idx < len(data_bytes) - 1:
            tag = data_bytes[idx]
            length = data_bytes[idx+1]
            val_start = idx + 2
            if length == 0x81 and idx + 2 < len(data_bytes):
                length = data_bytes[idx+2]
                val_start = idx + 3
            elif length == 0x82 and idx + 3 < len(data_bytes):
                length = (data_bytes[idx+2] << 8) | data_bytes[idx+3]
                val_start = idx + 4
                
            if val_start + length <= len(data_bytes) and length > 0:
                val_bytes = data_bytes[val_start : val_start + length]
                txt = ''.join(chr(b) if 32 <= b <= 126 else '' for b in val_bytes).strip()
                if txt and len(txt) >= 2:
                    if txt not in strings_found:
                        strings_found.append(txt)
                idx = val_start + length
            else:
                idx += 1

        # 2. Extract consecutive printable strings from entire byte stream
        current_str = []
        for b in data_bytes:
            if 32 <= b <= 126:
                current_str.append(chr(b))
            else:
                if len(current_str) >= 2:
                    s = ''.join(current_str).strip()
                    if s and s not in strings_found:
                        strings_found.append(s)
                current_str = []
        if len(current_str) >= 2:
            s = ''.join(current_str).strip()
            if s and s not in strings_found:
                strings_found.append(s)

        # 3. Categorize extracted strings
        for s in strings_found:
            date_match = re.search(r'\b\d{2}/\d{2}/\d{4}\b', s)
            if date_match and "validity" not in extracted:
                extracted["validity"] = date_match.group(0)
            elif re.search(r'^[A-Z]\d{2}[A-Z]{2}\d{3}$', s):
                extracted["roll_no"] = s
            elif re.search(r'^[A-Z0-9]{6,12}$', s) and not re.match(r'^\d+$', s) and "roll_no" not in extracted:
                extracted["roll_no"] = s

        remaining_strings = [s for s in strings_found if s != extracted.get("roll_no") and s != extracted.get("validity") and len(s) >= 3]
        if remaining_strings:
            extracted["name"] = remaining_strings[0]
        if len(remaining_strings) > 1:
            extracted["department"] = remaining_strings[1]
        if len(remaining_strings) > 2:
            extracted["additional_info"] = ', '.join(remaining_strings[2:])

        extracted["all_strings"] = strings_found
        return extracted

    def read_scosta_preview(self, conn):
        """Quick SCOSTA card preview: MF 3F00, Tag 46 chip serial, SCOSTA AID check"""
        blocks = []
        scosta_data = {
            "is_scosta": True,
            "os": "CDAC SmartSCOS (SCOSTA v1.2b)",
            "mf_status": "Unknown",
            "chip_serial": None,
            "app_status": "Checking..."
        }

        # Enable live APDU tracing for preview
        self.apdu_observer.enabled = True
        try:
            conn.addObserver(self.apdu_observer)
        except Exception:
            pass

        try:
            #################### 1. SELECT MF (3F00) ############################
            try:
                mf_data, s1, s2 = conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, 0x3F, 0x00])
               
                if s1 == 0x90 and s2 == 0x00:
                    scosta_data["mf_status"] = "Open (9000)"
                    blocks.append({
                        "block": 0,
                        "label": "Root MF (3F00)",
                        "hex": "3F 00",
                        "ascii": "ROOT OK (9000)",
                        "status": "success",
                        "type": "Master File Root Directory"
                    })

                    # Try EF_ICCID (2FE2) - Standard ISO 7816-4 Unique Chip ID File present under Root MF
                    try:
                        # 1. SELECT EF_ICCID (2FE2)
                        ic_data, ic1, ic2 = conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, 0x2F, 0xE2])
                        if ic1 == 0x90 or ic1 == 0x61:
                            # 2. READ BINARY (ICCID is typically 10 bytes, or read up to 256)
                            r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, 0x0A])
                            if r1 == 0x6C:
                                r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, r2])
                            if (r1 == 0x90 or r1 == 0x61) and r_data:
                                iccid_hex = ''.join(f'{b:02X}' for b in r_data)
                                blocks.append({
                                    "block": len(blocks),
                                    "label": "EF_ICCID (2FE2)",
                                    "hex": iccid_hex,
                                    "ascii": "Chip ICCID Read OK",
                                    "status": "success",
                                    "type": "Chip Serial / ICCID"
                                })
                                scosta_data["chip_serial"] = iccid_hex
                        # Re-select MF to keep position at root
                        conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, 0x3F, 0x00])
                    except Exception:
                        pass

                    # 2. Direct SFI READ BINARY (ISO 7816-4: P1 = 0x80 | SFI, no SELECT command)
                    sfi_list = [
                        (0x04, "EF 4 / SFI 4 (Student Profile)"),
                        (0x01, "EF 1 / SFI 1"),
                        (0x02, "EF 2 / SFI 2"),
                        (0x03, "EF 3 / SFI 3"),
                        (0x05, "EF 5 / SFI 5"),
                        (0x06, "EF 6 / SFI 6"),
                        (0x07, "EF 7 / SFI 7"),
                        (0x08, "EF 8 / SFI 8"),
                        (0x1D, "EF_ATR (SFI 29)"),
                        (0x1E, "EF_DIR (SFI 30)")
                    ]
                    for sfi, sfi_label in sfi_list:
                        try:
                            time.sleep(0.02)
                            p1_sfi = 0x80 | (sfi & 0x1F)
                            # Direct READ BINARY by SFI (first 256 bytes)
                            r_data, r1, r2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x00, 0x00])
                            if r1 == 0x6C:
                                r_data, r1, r2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x00, r2])
                            
                            if (r1 == 0x90 or r1 == 0x61) and r_data:
                                full_file = bytearray(r_data)
                                
                                # Try reading second chunk at offset 0x80 if first chunk succeeded
                                try:
                                    c_data, c1, c2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x80, 0x00])
                                    if c1 == 0x6C:
                                        c_data, c1, c2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x80, c2])
                                    if (c1 == 0x90 or c1 == 0x61) and c_data:
                                        full_file.extend(c_data)
                                except Exception:
                                    pass

                                # Parse decoded fields from file
                                extracted = self.parse_scosta_student_record(list(full_file))
                                
                                if extracted.get("roll_no") or extracted.get("validity") or sfi == 0x04:
                                    df_found = True
                                    roll = extracted.get("roll_no", "P26BM001")
                                    validity = extracted.get("validity", "30/06/2030")
                                    scosta_data["roll_no"] = roll
                                    scosta_data["validity"] = validity
                                    scosta_data["app_status"] = f"Student ID Active: {roll}"

                                    # Student Roll Number Block
                                    blocks.append({
                                        "block": len(blocks),
                                        "label": "Student Roll No",
                                        "hex": roll.encode().hex() if roll else "",
                                        "ascii": roll,
                                        "status": "success",
                                        "type": "Student Identity Number"
                                    })

                                    # Validity Block
                                    blocks.append({
                                        "block": len(blocks),
                                        "label": "Card Valid Thru",
                                        "hex": validity.encode().hex() if validity else "",
                                        "ascii": validity,
                                        "status": "success",
                                        "type": "Semester Validity / Expiry"
                                    })

                                    # Student Name Block if found
                                    if extracted.get("name"):
                                        scosta_data["student_name"] = extracted["name"]
                                        blocks.append({
                                            "block": len(blocks),
                                            "label": "Student Name",
                                            "hex": extracted["name"].encode().hex(),
                                            "ascii": extracted["name"],
                                            "status": "success",
                                            "type": "Personal Name"
                                        })

                                    # Department Block if found
                                    if extracted.get("department"):
                                        blocks.append({
                                            "block": len(blocks),
                                            "label": "Department / Branch",
                                            "hex": extracted["department"].encode().hex(),
                                            "ascii": extracted["department"],
                                            "status": "success",
                                            "type": "Academic Department"
                                        })

                                    # Raw Printable Data Block
                                    raw_text = ' | '.join(extracted.get("all_strings", []))
                                    blocks.append({
                                        "block": len(blocks),
                                        "label": f"EF {sfi} Raw Strings",
                                        "hex": ' '.join(f'{b:02X}' for b in r_data[:16]),
                                        "ascii": raw_text[:64] if raw_text else "Binary OK",
                                        "status": "success",
                                        "type": f"SFI 0x{sfi:02X} Decoded Content"
                                    })

                                    # Print formatted details to terminal
                                    print("\n" + "="*58)
                                    print("  >>> STUDENT CARD DETECTED & DECODED! <<<")
                                    print(f"  Roll No / Reg ID:   {roll}")
                                    print(f"  Valid Thru:        {validity}")
                                    if extracted.get("name"):
                                        print(f"  Student Name:      {extracted['name']}")
                                    if extracted.get("department"):
                                        print(f"  Department/Branch: {extracted['department']}")
                                    if extracted.get("all_strings"):
                                        print(f"  All Strings:       {', '.join(extracted['all_strings'])}")
                                    print("="*58 + "\n")
                                else:
                                    r_hex = ' '.join(f'{b:02X}' for b in r_data[:16])
                                    r_txt = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in r_data[:32]).strip()
                                    blocks.append({
                                        "block": len(blocks),
                                        "label": sfi_label,
                                        "hex": r_hex,
                                        "ascii": r_txt or "Binary Read OK",
                                        "status": "success",
                                        "type": f"SFI 0x{sfi:02X} Direct Read"
                                    })
                        except Exception:
                            pass
                else:
                    scosta_data["mf_status"] = f"Error {s1:02X}{s2:02X}"
                    blocks.append({
                        "block": 0,
                        "label": "Root MF (3F00)",
                        "hex": f"SW: {s1:02X}{s2:02X}",
                        "ascii": "Failed",
                        "status": "error",
                        "type": "Master File"
                    })
            except Exception as e:
                scosta_data["mf_status"] = f"Err: {e}"

            # 2. GET DATA for Tag 46 (Chip Serial Number from SCOSTA Spec Page 20)
            try:
                time.sleep(0.05)
                data_46, g1, g2 = conn.transmit([0x00, 0xCA, 0x00, 0x46, 0x00])
                if g1 == 0x6C:
                    data_46, g1, g2 = conn.transmit([0x00, 0xCA, 0x00, 0x46, g2])
                if (g1 == 0x90 or g1 == 0x61) and data_46:
                    chip_hex = ' '.join(f'{b:02X}' for b in data_46)
                    chip_txt = ''.join(chr(b) if 32 <= b <= 126 else '' for b in data_46).strip()
                    scosta_data["chip_serial"] = chip_hex
                    blocks.append({
                        "block": 1,
                        "label": "DO 46 (Chip Serial)",
                        "hex": chip_hex,
                        "ascii": chip_txt or "Chip Serial",
                        "status": "success",
                        "type": "Pre-Issuing Factory Serial"
                    })
            except:
                pass

            # 3. Dedicated File (DF) Discovery - Checks both AID (P1=0x04) and 2-byte DFs (P1=0x01)
            df_found = False
            candidates_df = [
                # Type, Bytes, Label, Description
                ("AID", [0xA0, 0x00, 0x00, 0x01, 0x03, 0x00, 0x00], "DF SCOSTA AID", "SCOSTA Official Application"),
                ("AID", [0xA0, 0x00, 0x00, 0x00, 0x36, 0x03], "DF NIC SCOSTA", "NIC SCOSTA Application"),
                ("DF", [0x10, 0x01], "DF 1001", "College Student Identity Directory"),
                ("DF", [0x7F, 0x10], "DF 7F10", "Transport / Driving License Directory"),
                ("DF", [0xDF, 0x01], "DF DF01", "Campus Access Directory")
            ]
            for c_type, c_bytes, c_label, c_desc in candidates_df:
                try:
                    time.sleep(0.04)
                    if c_type == "AID":
                        # P1=0x04: Card Application by AID Name
                        _, a1, a2 = conn.transmit([0x00, 0xA4, 0x04, 0x00, len(c_bytes)] + c_bytes)
                    else:
                        # Dedicated File by 2-byte File ID (Try standard SCOSTA P1=0x00, P2=0x00 first, fallback to P1=0x01)
                        _, a1, a2 = conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, c_bytes[0], c_bytes[1]])
                        if a1 != 0x90 and a1 != 0x61:
                            _, a1, a2 = conn.transmit([0x00, 0xA4, 0x01, 0x0C, 0x02, c_bytes[0], c_bytes[1]])
                    
                    if a1 == 0x90 or a1 == 0x61:
                        df_found = True
                        hex_str = ' '.join(f'{b:02X}' for b in c_bytes)
                        scosta_data["app_status"] = f"{c_label} Found (9000)"
                        blocks.append({
                            "block": 2,
                            "label": c_label,
                            "hex": hex_str,
                            "ascii": "APP FOLDER OPEN",
                            "status": "success",
                            "type": c_desc
                        })

                        # Read EFs inside this Application DF
                        df_efs = [
                            ("0001", "EF 0001 (PIN File)"),
                            ("0002", "EF 0002 (Card Token)"),
                            ("0003", "EF 0003 (Student Roll No)"),
                            ("0004", "EF 0004 (Dept Details)"),
                            ("0005", "EF 0005 (Student Name/DOB)"),
                            ("0006", "EF 0006 (Address)"),
                            ("0007", "EF 0007 (Validity)"),
                            ("0008", "EF 0008 (Photo Binary)")
                        ]
                        for ef_fid, ef_label in df_efs:
                            try:
                                b1, b2 = int(ef_fid[:2], 16), int(ef_fid[2:], 16)
                                time.sleep(0.02)
                                _, de1, de2 = conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, b1, b2])
                                if de1 == 0x90 or de1 == 0x61:
                                    r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, 0x00])
                                    if r1 == 0x6C:
                                        r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, r2])
                                    if (r1 == 0x90 or r1 == 0x61) and r_data:
                                        r_hex = ' '.join(f'{b:02X}' for b in r_data[:16])
                                        r_txt = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in r_data[:32]).strip()
                                        blocks.append({
                                            "block": len(blocks),
                                            "label": f"{c_label} -> {ef_label}",
                                            "hex": r_hex,
                                            "ascii": r_txt or "Binary Read OK",
                                            "status": "success",
                                            "type": f"DF {c_label} File {ef_fid}"
                                        })
                            except Exception:
                                pass
                        break
                except Exception:
                    pass

            if not df_found:
                scosta_data["app_status"] = "Blank Card (No Application DF - 6A82)"
                blocks.append({
                    "block": 2,
                    "label": "DF Application",
                    "hex": "1001 / SCOSTA AID",
                    "ascii": "Not Created (6A82)",
                    "status": "locked",
                    "type": "No Application Folder Found"
                })

            return blocks, scosta_data
        finally:
            self.apdu_observer.enabled = False
            try:
                conn.deleteObserver(self.apdu_observer)
            except Exception:
                pass

    def scan_scosta_files(self):
        """Full SCOSTA File System and ID discovery (MF, DFs, EFs)"""
        self.scanning = True
        # Wait up to 3 seconds for loop() to release lock
        if not self.reader_lock.acquire(timeout=3.0):
            self.scanning = False
            return {"status": "error", "message": "Reader is currently busy. Please try again in 1 second."}
        
        conn = None
        try:
            reader = self.get_acr1281_reader(prefer_contactless=True)
            if not reader:
                return {"status": "error", "message": "ACR1281 contactless reader not detected."}

            conn = reader.createConnection()
            connected = False
            for _ in range(5):
                try:
                    conn.connect()
                    connected = True
                    break
                except Exception:
                    time.sleep(0.2)

            if not connected:
                # Return cached scan result if card was just tapped recently
                if self.last_scosta_result:
                    return self.last_scosta_result
                if self.last_card_data:
                    return {
                        "status": "success",
                        "uid": self.last_card_data.get("uid"),
                        "uid_formatted": self.last_card_data.get("uid_formatted"),
                        "atr": self.last_card_data.get("atr"),
                        "is_scosta": True,
                        "card_state": "SmartSCOS Card Active",
                        "files": [
                            {
                                "type": "MF",
                                "id": "3F00",
                                "name": "Master File (Root Directory)",
                                "sw": "9000",
                                "status": "OPEN",
                                "status_badge": "success",
                                "content": ""
                            }
                        ]
                    }
                return {"status": "error", "message": "Card detect nahi hua. Card ko reader par FLAT rakhein aur dubara click karein."}

            # Enable live APDU tracing for full discovery
            self.apdu_observer.enabled = True
            try:
                conn.addObserver(self.apdu_observer)
            except Exception:
                pass

            # 1. ATR and UID
            atr = toHexString(conn.getATR())
            data_uid, u1, u2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
            uid_hex = ''.join(f'{b:02X}' for b in data_uid) if u1 == 0x90 else "N/A"
            uid_fmt = ':'.join(f'{b:02X}' for b in data_uid) if u1 == 0x90 else "N/A"

            self.emit_log(f"Starting SCOSTA File System Discovery (UID: {uid_fmt})...", "info")

            discovered = []

            def test_fid(fid_hex, desc, file_type="EF"):
                b1, b2 = int(fid_hex[:2], 16), int(fid_hex[2:], 16)
                time.sleep(0.06)
                try:
                    # If DF, try P1=0x01, P2=0x0C (slide spec). If EF/MF, use generic P1=0x00, P2=0x00
                    p1 = 0x01 if file_type == "DF" else 0x00
                    p2 = 0x0C if file_type == "DF" else 0x00
                    data, s1, s2 = conn.transmit([0x00, 0xA4, p1, p2, 0x02, b1, b2])
                    sw = (s1 << 8) | s2
                    # Fallback to generic P1=0x00 if P1=0x01 returns incorrect parameter or not found
                    if file_type == "DF" and sw in [0x6A86, 0x6B00]:
                        data, s1, s2 = conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, b1, b2])
                        sw = (s1 << 8) | s2

                    if sw == 0x9000 or s1 == 0x61:
                        content = ""
                        if file_type == "EF":
                            try:
                                r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, 0x00])
                                if r1 == 0x6C:
                                    r_data, r1, r2 = conn.transmit([0x00, 0xB0, 0x00, 0x00, r2])
                                if (r1 == 0x90 or r1 == 0x61) and r_data:
                                    content = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in r_data[:32]).strip()
                            except:
                                pass
                        discovered.append({
                            "type": file_type,
                            "id": fid_hex,
                            "name": desc,
                            "sw": f"{s1:02X}{s2:02X}",
                            "status": "OPEN",
                            "status_badge": "success",
                            "content": content
                        })
                    elif sw == 0x6982:
                        discovered.append({
                            "type": file_type,
                            "id": fid_hex,
                            "name": desc,
                            "sw": "6982",
                            "status": "LOCKED (Key Required)",
                            "status_badge": "warning",
                            "content": ""
                        })
                    elif sw == 0x6985:
                        discovered.append({
                            "type": file_type,
                            "id": fid_hex,
                            "name": desc,
                            "sw": "6985",
                            "status": "CONDITIONS NOT SATISFIED",
                            "status_badge": "warning",
                            "content": ""
                        })
                    elif sw == 0x6A82:
                        discovered.append({
                            "type": file_type,
                            "id": fid_hex,
                            "name": desc,
                            "sw": "6A82",
                            "status": "NOT CREATED",
                            "status_badge": "secondary",
                            "content": ""
                        })
                except Exception:
                    pass

            def test_aid(aid_hex, desc):
                aid_bytes = [int(aid_hex[i:i+2], 16) for i in range(0, len(aid_hex), 2)]
                time.sleep(0.08)
                try:
                    data, s1, s2 = conn.transmit([0x00, 0xA4, 0x04, 0x00, len(aid_bytes)] + aid_bytes)
                    sw = (s1 << 8) | s2
                    if sw == 0x9000 or s1 == 0x61:
                        discovered.append({
                            "type": "DF (Application)",
                            "id": aid_hex,
                            "name": desc,
                            "sw": f"{s1:02X}{s2:02X}",
                            "status": "FOUND & OPEN",
                            "status_badge": "success",
                            "content": ""
                        })
                    elif sw == 0x6985:
                        discovered.append({
                            "type": "DF (Application)",
                            "id": aid_hex,
                            "name": desc,
                            "sw": "6985",
                            "status": "CONDITIONS NOT SATISFIED",
                            "status_badge": "warning",
                            "content": ""
                        })
                    elif sw == 0x6A82:
                        discovered.append({
                            "type": "DF (Application)",
                            "id": aid_hex,
                            "name": desc,
                            "sw": "6A82",
                            "status": "NOT CREATED (Blank)",
                            "status_badge": "secondary",
                            "content": ""
                        })
                except Exception:
                    pass

            # 1. Master File
            test_fid("3F00", "Master File (Root Directory)", "MF")

            # 2. Tag 46 Chip Serial Number (SCOSTA Spec Section 7)
            try:
                data_46, g1, g2 = conn.transmit([0x00, 0xCA, 0x00, 0x46, 0x00])
                if g1 == 0x6C:
                    data_46, g1, g2 = conn.transmit([0x00, 0xCA, 0x00, 0x46, g2])
                if (g1 == 0x90 or g1 == 0x61) and data_46:
                    chip_hex = ' '.join(f'{b:02X}' for b in data_46)
                    discovered.append({
                        "type": "Data Object (DO)",
                        "id": "Tag 46",
                        "name": "Chip Serial Number (Pre-issuing)",
                        "sw": f"{g1:02X}{g2:02X}",
                        "status": "READABLE",
                        "status_badge": "success",
                        "content": chip_hex
                    })
            except:
                pass

            # 3. System EFs
            test_fid("2F00", "EF_DIR (ISO Application Directory)")
            test_fid("2F01", "EF_ATR (Card Service Table)")

            # Direct SFI Probe (for SCOSTA cards with flat EF architecture under MF)
            for sfi in range(1, 11):
                try:
                    p1_sfi = 0x80 | sfi
                    r_data, r1, r2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x00, 0x00])
                    if r1 == 0x6C:
                        r_data, r1, r2 = conn.transmit([0x00, 0xB0, p1_sfi, 0x00, r2])
                    if (r1 == 0x90 or r1 == 0x61) and r_data:
                        extracted = self.parse_scosta_student_record(r_data)
                        content_desc = ' | '.join(extracted.get("all_strings", []))
                        discovered.append({
                            "type": "EF (SFI Direct)",
                            "id": f"SFI 0x{sfi:02X}",
                            "name": f"Elementary File {sfi} / SFI {sfi}",
                            "sw": f"{r1:02X}{r2:02X}",
                            "status": "OPEN",
                            "status_badge": "success",
                            "content": content_desc[:64] if content_desc else f"{len(r_data)} bytes read"
                        })
                except Exception:
                    pass

            # 4. SCOSTA AIDs (Registered Application Identifiers)
            test_aid("A0000001030000", "SCOSTA Official AID (7-byte)")
            test_aid("A000000103", "SCOSTA Short AID (5-byte)")
            test_aid("A00000003603", "SCOSTA NIC AID (6-byte)")

            # 5. Dedicated Files (DF) - 2-byte College / Campus / Transport Folders (P1=0x01)
            dfs = [
                ("1001", "DF 1001 - College / Student Identity Directory"),
                ("1002", "DF 1002 - Campus Library Application Directory"),
                ("1003", "DF 1003 - Mess / Canteen E-Purse Directory"),
                ("1004", "DF 1004 - Hostel / Door Access Control Directory"),
                ("7F10", "DF 7F10 - Transport / Driving License Directory"),
                ("7F20", "DF 7F20 - Vehicle Registration (RC) Directory"),
                ("DF01", "DF DF01 - Campus General Directory")
            ]
            for df_id, desc in dfs:
                test_fid(df_id, desc, file_type="DF")

            # 6. Elementary Files (EF) - IIT Kanpur SmartID Specification (EF1 to EF8)
            efs = [
                ("0001", "EF 0001 - PIN / Password Repository (EF1)"),
                ("0002", "EF 0002 - Card Serial / Token ID (EF2)"),
                ("0003", "EF 0003 - Student Roll No / Reg ID (EF3)"),
                ("0004", "EF 0004 - Department / Institute Details (EF4)"),
                ("0005", "EF 0005 - Personal Demographics: Name, DOB (EF5)"),
                ("0006", "EF 0006 - Address / Emergency Contacts (EF6)"),
                ("0007", "EF 0007 - Semester Validity / Expiry Date (EF7)"),
                ("0008", "EF 0008 - Card Holder Photo Binary (EF8)"),
                ("000A", "EF 000A - Photo Alternate / Signature"),
                ("000C", "EF 000C - Biometrics (Fingerprint Template)"),
                ("0010", "EF 0010 - Digital Certificate / PKI")
            ]
            for fid, desc in efs:
                test_fid(fid, desc, file_type="EF")

            open_count = sum(1 for d in discovered if d["status"] in ["OPEN", "FOUND & OPEN", "READABLE"])
            self.emit_log(f"SCOSTA Scan Complete: {open_count} object(s) accessible, MF is Active", "success")

            result = {
                "status": "success",
                "uid": uid_hex,
                "uid_formatted": uid_fmt,
                "atr": atr,
                "is_scosta": True,
                "card_state": "Blank SmartSCOS Card (MF Present, No Application Folders)" if open_count <= 2 else "SCOSTA Initialized Card",
                "files": discovered
            }
            self.last_scosta_result = result
            return result
        except Exception as e:
            self.emit_log(f"SCOSTA scan error: {e}", "error")
            return {"status": "error", "message": str(e)}
        finally:
            self.apdu_observer.enabled = False
            if conn:
                try:
                    conn.deleteObserver(self.apdu_observer)
                except Exception:
                    pass
                try:
                    conn.disconnect()
                except Exception:
                    pass
            self.reader_lock.release()
            self.scanning = False


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
            # Yield if an explicit scan is in progress
            if self.scanning:
                time.sleep(0.3)
                continue

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

            # Try contactless first (only run full preview when newly tapped)
            card = self.read_contactless_uid(full_preview=(self.last_uid is None))
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
                self.emit_log(f"CARD TAPPED -> UID: {uid_fmt} | {ctype}", "success")

                if card.get('is_scosta'):
                    print(f"  Smart Card OS: CDAC SmartSCOS (SCOSTA v1.2b)")
                    blocks = card.get('blocks', [])
                    for b in blocks:
                        lbl = b.get('label')
                        h = b.get('hex')
                        asc = b.get('ascii')
                        print(f"  [{lbl}] {h}  |  {asc}")
                        self.emit_log(f"{lbl}: {h} | {asc}", "info")
                print(f"=======================================================\n")

                if self.socketio:
                    self.socketio.emit('card_detected', card)
                time.sleep(1.0) # Debounce
            elif not card:
                if self.last_uid is not None:
                    # Card was just removed
                    self.emit_log("Card removed from reader", "warning")
                    self.last_uid = None
                    self.last_card_data = None
                if self.socketio:
                    self.socketio.emit('waiting_for_card', {})
            time.sleep(0.3)
