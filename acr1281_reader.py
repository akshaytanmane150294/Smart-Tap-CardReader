"""
ACR1281U-C1 Dual Reader - Complete Handler
- Contactless Interface: Reads NFC / MIFARE UID, data blocks
- Contact Interface: Reads Smart Cards (ATR, etc)
Works with pyscard (PC/SC)
"""

import sys
import time
import threading
from smartcard.System import readers # List Readers
from smartcard.util import toHexString, toBytes # Convert Bytes
from smartcard.Exceptions import NoCardException, CardConnectionException
from smartcard.ReaderMonitoring import ReaderMonitor, ReaderObserver #USB reader plug/unplug hone ko automatically detect karte hain.
from smartcard.CardConnectionObserver import CardConnectionObserver # APDU commands aur responses ko intercept (pakadne) ke liye

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


class APDUConnectionObserver(CardConnectionObserver):
    """
    # APDUConnectionObserver - Har APDU ka hisab rakhega

    Pyscard Framework Observer to intercept and log raw APDU commands and responses.
    Streams APDU traffic to the frontend console in real time.
    """
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.enabled = False

    def interpret_apdu(self, apdu_bytes):
        # Tum jo bytes bhejte ho 00 A4 00 00 02 3F00, isko ye readable banata hai
        # Basically, ye code APDU commands ko read karne layak banata hai.
        # APDU = Application Protocol Data Unit (Commands and Responses exchanged between reader and card)

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
        # Card jo jawab deta hai 90 00, 6A82 usko human banata hai -> Success, File Not Found
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
                hex_cmd = toHexString(cmd_bytes) # cmd_bytes = [0xFF, 0xCA, 0x00, 0x00, 0x00] convert to "FF CA 00 00 00"

                # Jab bhi conn.transmit() hota hai, pyscard isko auto call karta hai. 
                # Tumhara emit_log() se ye frontend pe live dikhta hai:
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
    # Ye class Plug & Play monitor hai:
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.first_run = True

    def update(self, observable, handlers):
        # Jese hi reader plug-in ya unplug hota hai, pyscard ye method call karta hai.
        # added_readers: Agar user ne USB cable lagayi toh frontend ko event bhejta hai: "USB Reader Connected".
        # removed_readers: Agar reader unplug kiya toh event bhejta hai: "USB Reader Disconnected".
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
    # Ye main controller class hai jo poore smart card lifecycle ko handle karti hai
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.last_uid = None # Pichle card ka record taaki ek hi card tap rehne par baar-baar repeat na ho.
        self.last_atr = None # ATR (Answer to Reset) - Card ki identity aur capabilities batata hai.
        self.last_card_data = None # Card se read kiya gaya latest data (bytes/hex)
        self.scanning = False # Agar true ho toh card data continuously scan hota rahega.
        self.log_history = [] # Yahan har log entry save hogi (like a notepad).
        self.max_logs = 100 # Sirf last 100 logs rakhega, purane delete kar deta hai.
        self.reader_lock = threading.Lock() # Ye lock ensure karta hai ki do threads ek saath reader ko access na karein (race condition se bachata hai).
        self.last_scosta_result = None
        self.last_userdata = None

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
        # ACR1281U-C1 me 2 interfaces hote hain: PICC (Contactless) and ICC (Contact)
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
            GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00] #ACR1281 standard command se card ka UID fetch karta hai.
            data, sw1, sw2 = conn.transmit(GET_UID)
            
            if sw1 == 0x90 and sw2 == 0x00:
                uid_hex = ''.join(f'{b:02X}' for b in data)
                uid_formatted = ':'.join(f'{b:02X}' for b in data)
                
                # If same card is still holding on reader and preview is not requested, return cached
                if self.last_uid == uid_hex and self.last_card_data and not full_preview:
                    return self.last_card_data

                # Get ATR for extra info
                atr = toHexString(conn.getATR()) #Card ka ATR (Answer To Reset) leta hai jisse card ki technology pata chalti hai.
                
                # Detect card type from ATR / UID length
                card_type = self.detect_card_type(uid_hex, atr)
                is_scosta = self.is_scosta_card(atr)
                
                # Read SCOSTA file preview if applicable
                scosta_info = None
                try:
                    if is_scosta:
                        scosta_info = self.read_scosta_preview(conn)
                except Exception:
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
                    "raw_data": data
                }
                self.last_card_data = card_data
                if is_scosta and scosta_info:
                    self.last_scosta_result = {
                        "status": "success",
                        "uid": uid_hex,
                        "uid_formatted": uid_formatted,
                        "atr": atr,
                        "is_scosta": True,
                        "card_state": "SmartSCOS Card Active (MF 3F00 Open)",
                        "files": [
                            {
                                "type": "Master File Root Directory",
                                "id": "Root MF (3F00)",
                                "name": "Root MF (3F00)",
                                "sw": "9000",
                                "status": "OPEN",
                                "status_badge": "success",
                                "content": "ROOT OK (9000)"
                            },
                            {
                                "type": "Elementary File (EF)",
                                "id": "EF 4 / SFI 4",
                                "name": "EF 4 / SFI 4 (Student Profile)",
                                "sw": "9000",
                                "status": "OPEN",
                                "status_badge": "success",
                                "content": f"{scosta_info.get('student_name', 'Student')} ({scosta_info.get('roll_no', 'Active')})" if scosta_info else "Student Profile Active"
                            }
                        ]
                    }
                    if scosta_info:
                        doj = scosta_info.get("date_of_joining") or scosta_info.get("DateofJoining") or scosta_info.get("doj")
                        self.last_userdata = {
                            "status": "success",
                            "uid": uid_formatted,
                            "atr": atr,
                            "userdata": {
                                "roll_no": scosta_info.get("roll_no"),
                                "name": scosta_info.get("student_name") or scosta_info.get("name"),
                                "department": scosta_info.get("department"),
                                "validity": scosta_info.get("validity"),
                                "dob": scosta_info.get("dob"),
                                "doj": doj,
                                "date_of_joining": doj,
                                "DateofJoining": doj,
                                "mobile": scosta_info.get("mobile"),
                                "blood_group": scosta_info.get("blood_group"),
                                "gender": scosta_info.get("gender"),
                                "user_id": scosta_info.get("user_id"),
                                "authority": scosta_info.get("authority")
                            }
                        }
                        card_data["userdata"] = self.last_userdata["userdata"]
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

    def parse_scosta_student_record(self, data, level=0):
        """Parse ASN.1 / TLV / String structures from SCOSTA Student File (SFI 4 / EF 4)"""
        index = 0
        extracted_results = []
        data_bytes = bytes(data)
        
        while index < len(data_bytes):
            if index + 1 >= len(data_bytes):
                break
                
            # 1. Tag Nikalna
            tag = data_bytes[index]
            index += 1
            
            # 2. Length Nikalna (Handling Short and Long Forms)
            length_byte = data_bytes[index]
            index += 1
            
            if length_byte & 0x80:  # Agar long form length hai (jaise 0x82)
                num_length_bytes = length_byte & 0x7F
                if index + num_length_bytes > len(data_bytes):
                    break
                length = int.from_bytes(data_bytes[index : index + num_length_bytes], byteorder='big')
                index += num_length_bytes
            else:                   # Agar short form length hai (0 se 127)
                length = length_byte
                
            is_constructed = bool(tag & 0x20)
            if index + length > len(data_bytes):
                if is_constructed:
                    value = data_bytes[index:]
                    index = len(data_bytes)
                else:
                    break
            else:
                value = data_bytes[index : index + length]
                index += length
            
            if not is_constructed:
                try:
                    text_val = value.decode('utf-8')
                    if text_val.isprintable() and len(text_val) > 1:
                        extracted_results.append(text_val)
                except Exception:
                    pass
            else:
                sub_results = self.parse_scosta_student_record(value, level + 1)
                extracted_results.extend(sub_results)

        if level == 0:
            return {
                "roll_no": extracted_results[0] if len(extracted_results) > 0 else None,
                "validity": extracted_results[1] if len(extracted_results) > 1 else None,
                "name": extracted_results[2] if len(extracted_results) > 2 else None,
                "dob": extracted_results[3] if len(extracted_results) > 3 else None,
                "user_id": extracted_results[4] if len(extracted_results) > 4 else None,
                "mobile": extracted_results[5] if len(extracted_results) > 5 else None,
                "blood_group": extracted_results[6] if len(extracted_results) > 6 else None,
                "authority": extracted_results[7] if len(extracted_results) > 7 else None,
                "gender": extracted_results[8] if len(extracted_results) > 8 else None,
                "DateofJoining": extracted_results[9] if len(extracted_results) > 9 else None,
                "department": extracted_results[10] if len(extracted_results) > 10 else None,
                "all_strings": extracted_results
            }

        return extracted_results




    def read_scosta_preview(self, conn):
        """Quick SCOSTA card preview: MF 3F00 and EF 4 Student Record"""
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
                    
                    sfi = 0x04
                    try:
                        p1_sfi = 0x80 | (sfi & 0x1F)
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
                                roll = extracted.get("roll_no") or "N/A"
                                validity = extracted.get("validity") or "N/A"
                                name = extracted.get("name")
                                department = extracted.get("department")
                                dob = extracted.get("dob")
                                mobile = extracted.get("mobile")
                                blood_group = extracted.get("blood_group")
                                gender = extracted.get("gender")

                                doj = extracted.get("DateofJoining")

                                scosta_data["roll_no"] = roll
                                scosta_data["validity"] = validity
                                scosta_data["student_name"] = name
                                scosta_data["dob"] = dob
                                scosta_data["doj"] = doj
                                scosta_data["date_of_joining"] = doj
                                scosta_data["DateofJoining"] = doj
                                scosta_data["user_id"] = extracted.get("user_id")
                                scosta_data["mobile"] = mobile
                                scosta_data["blood_group"] = blood_group
                                scosta_data["gender"] = gender
                                scosta_data["department"] = department
                                scosta_data["authority"] = extracted.get("authority")
                                scosta_data["app_status"] = f"Student ID Active: {roll}"

                                # Print formatted details to terminal
                                print("\n" + "="*58)
                                print("  >>> STUDENT CARD DETECTED & DECODED! <<<")
                                print(f"  Roll No / Reg ID:   {roll}")
                                if name:
                                    print(f"  Student Name:      {name}")
                                if department:
                                    print(f"  Department/Branch: {department}")
                                print(f"  Valid Thru:        {validity}")
                                if dob:
                                    print(f"  Date of Birth:     {dob}")
                                if doj:
                                    print(f"  Date of Joining:   {doj}")
                                if mobile:
                                    print(f"  Mobile:            {mobile}")
                                if blood_group:
                                    print(f"  Blood Group:       {blood_group}")
                                if gender:
                                    print(f"  Gender:            {gender}")
                                print("="*58 + "\n")
                    except Exception:
                        pass
                else:
                    scosta_data["mf_status"] = f"Error {s1:02X}{s2:02X}"
            except Exception as e:
                scosta_data["mf_status"] = f"Err: {e}"
            return scosta_data
        finally:
            self.apdu_observer.enabled = False
            try:
                conn.deleteObserver(self.apdu_observer)
            except Exception:
                pass

    def scan_scosta_files(self):
        """SCOSTA File System Scan - Returns MF (3F00) and EF 4 (Student Profile)"""
        if self.last_scosta_result:
            return self.last_scosta_result

        # Agar cached nahi hai, to direct read karo
        self.read_contactless_uid(full_preview=True)
        if self.last_scosta_result:
            return self.last_scosta_result

        return {"status": "error", "message": "Card detect nahi hua. Card ko reader par rakhein."}


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
                        self.socketio.emit('card_deactivated', {
                            "status": "deactivated",
                            "message": "Card Deactivated (Lifted)",
                            "last_userdata": self.last_userdata
                        })
                elif self.socketio:
                    self.socketio.emit('waiting_for_card', {})
            time.sleep(0.3)
