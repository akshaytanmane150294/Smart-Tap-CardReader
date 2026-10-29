# acr_reader.py - Part 1
import io
from PIL import Image
from smartcard.System import readers
from smartcard.util import toHexString

class ACR1281Service:
    def __init__(self):
        self.last_uid = None
        self.last_card = None # Ek hi jagah sab data rakhenge
        self.reader = None     # Reader cache
        self.conn = None       # Active Card Connection cache

    def list_readers(self):
        try:
            return [str(r) for r in readers()]
        except:
            return []

    def get_reader(self):
        """Sirf ACR1281 dhoondo, Broadcom internal nahi"""
        # for r in readers():
        #     name = str(r).upper()
        #     if "ACR128" in name or "ACS" in name:
        #         if "SAM" not in name: # SAM slot ignore
        #             return r
        # return None

        all_r = readers()
        # Pehle PICC (Contactless tap antenna) dhoondo
        for r in all_r:
            name = str(r).upper()
            if ("ACR128" in name or "ACS" in name) and "PICC" in name:
                return r
        # Agar PICC na mile to koi bhi non-SAM ACR1281
        for r in all_r:
            name = str(r).upper()
            if ("ACR128" in name or "ACS" in name) and "SAM" not in name:
                return r
        return None


    def get_status(self):
        r = self.get_reader()
        return {
            "connected": r is not None,
            "device": str(r) if r else "ACR1281 Not Connected",
            "readers": self.list_readers()
        }


    def read_uid(self):
        reader = self.get_reader()
        if not reader:
            return None
        conn = reader.createConnection()
        try:
            conn.connect()
            # FF CA 00 00 00 = Get UID
            data, sw1, sw2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
            if sw1 == 0x90 and sw2 == 0x00:
                uid_hex = ''.join(f'{b:02X}' for b in data)
                return {
                    "uid": uid_hex,
                    "uid_formatted": ':'.join(f'{b:02X}' for b in data),
                    "atr": toHexString(conn.getATR()),
                    "conn": conn # connection open rakho agle step ke liye
                }
        except:
            pass
        finally:
            # conn yahan close mat karo, agle function me karenge
            pass
        return None

    def parse_and_extract(self, raw_bytes):
            """Tumhara bytearray -> dict + photo"""
            raw = bytes(raw_bytes)
            # 1. Saare printable strings nikalo (tumhara purana sahi method)
            strings = []
            temp = ""
            for b in raw:
                if 32 <= b <= 126: # printable ASCII
                    temp += chr(b)
                else:
                    if len(temp) > 2:
                        strings.append(temp.strip())
                    temp = ""
            # 2. Map karo - position se nahi, pattern se
            # Roll = 8-10 char, alphanumeric, P.. ya number se start
            # Date = / wala
            # Name = sabse lamba capital wala
            
            parsed = {
                "roll_no": strings[0] if len(strings) > 0 else None,
                "validity": strings[1] if len(strings) > 1 else None,
                "name": strings[2] if len(strings) > 2 else None,
                "dob": strings[3] if len(strings) > 3 else None,
                "user_id": strings[4] if len(strings) > 4 else None,
                "mobile": strings[5] if len(strings) > 5 else None,
                "blood_group": strings[6] if len(strings) > 6 else None,
                "authority": strings[7] if len(strings) > 7 else None,
                "gender": strings[8] if len(strings) > 8 else None,
                "DateofJoining": strings[9] if len(strings) > 9 else None,
                "department": strings[10] if len(strings) > 10 else None,
                "all_strings": strings
            }

            # Baaki jo bhi hai wo all_strings me rahega
            if len(strings) > 2: parsed["name"] = strings[2]
            if len(strings) > 5: parsed["mobile"] = strings[5]
            # 3. Photo nikalo FF D8 se FF D9 tak
            photo_bytes = None
            start = raw.find(b'\xff\xd8')
            if start != -1:
                end = raw.find(b'\xff\xd9', start)
                if end != -1:
                    photo_bytes = raw[start:end+2]
                else:
                    photo_bytes = raw[start:] # incomplete par bhi save
            
            return parsed, photo_bytes

    def read_scosta_file(self, conn):
            """MF 3F00 -> SFI 4"""
            
            try:
                # 1. MF Select
                conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, 0x3F, 0x00])
                full_file = bytearray()
                offset = 0

                # CHUNK = 0x80  # 128 byte ek baar me - safe hai
                CHUNK = 0xF0  # 240 byte ek baar me - safe hai
                TOTAL_SIZE = 5133 # tumhara size

                while offset < TOTAL_SIZE:
                    # P2 me offset ka low byte
                    p2 = offset & 0xFF

                    # Offset 256 se kam hai to SFI 4 se padho
                    if offset < 256:
                        p1 = 0x84  # 0x80 | 4
                        cmd = [0x00, 0xB0, p1, p2, CHUNK]
                    else:
                        # 256 ke baad SFI wala method fail hota hai
                        # Isliye bina SFI ke offset se padho
                        # P1 = offset ka high byte, P2 = low byte
                        p1 = (offset >> 8) & 0xFF
                        cmd = [0x00, 0xB0, p1, p2, CHUNK]
                    data, sw1, sw2 = conn.transmit(cmd)
                    # 6C = sahi length sw2 me hai
                    if sw1 == 0x6C:
                        cmd[-1] = sw2 # Le ko sahi karo
                        data, sw1, sw2 = conn.transmit(cmd)
                    # 90 00 = OK, 62 82 = file khatam
                    if sw1 == 0x90 or sw1 == 0x62:
                        full_file.extend(data)
                        offset += len(data)
                        # print(f"Read {len(data)} bytes, total {offset}/{TOTAL_SIZE}")
                        # Agar data CHUNK se kam aaya matlab file khatam
                        if len(data) < CHUNK:
                            break
                    else:
                        print(f"Read fail at offset {offset}: {sw1:02X} {sw2:02X}")
                        break
                    # hatana hai, isse loop ruk jayega
                print(f"Final size: {len(full_file)} bytes")
                
                return full_file
            except Exception as e:
                print(f"Read error: {e}")
                return None
                
    
    # def scan_all_EFs(self, conn):
    #     all_files = {}
    #     # 1. MF Select
    #     conn.transmit([0x00, 0xA4, 0x00, 0x00, 0x02, 0x3F, 0x00])
    #     print("\n--- Scanning All SFIs (1 to 30) ---")
        
    #     for sfi in range(1, 31): # 1 se 30 tak har SFI check karo
    #         full_file = bytearray()
    #         offset = 0
    #         p1_sfi = 0x80 | sfi  # SFI 1 = 0x81, SFI 4 = 0x84
    #         try:
    #             while offset < 1024:
    #                 cmd = [0x00, 0xB0, p1_sfi, offset, 0x00] # Le 0
    #                 data, sw1, sw2 = conn.transmit(cmd)
    #                 if sw1 == 0x6C: # Le galat hai to sahi Le se dobara
    #                     cmd[-1] = sw2
    #                     data, sw1, sw2 = conn.transmit(cmd)
    #                 if sw1 == 0x90 and len(data) > 0:
    #                     full_file.extend(data)
    #                     offset += len(data)
    #                     if len(data) < 0x80:
    #                         break
    #                 elif sw1 == 0x6A and (sw2 == 0x82 or sw2 == 0x83):
    #                     break
    #                 else:
    #                     break
    #             if len(full_file) > 10:
    #                 all_files[f"SFI_{sfi}"] = full_file
    #                 print(f"[+] SFI {sfi} mila: {len(full_file)} bytes")
    #                 printable = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in full_file[:100])
    #                 print(f"    Data: {printable}...")
    #         except Exception:
    #             pass
    #     return all_files


    def scan_card(self):
        # 1. Reader cache karo (agar pehle se nahi hai)
        if not self.reader:
            self.reader = self.get_reader()
            if self.reader:
                print(f"[ACR1281] Reader Connected: {self.reader}")
            else:
                return None

        # 2. Agar Card pehle se connected hai, to sirf check karo ki card abhi bhi rakha hai ya nahi
        if self.conn and self.last_card:
            try:
                # Lightweight ping (Get UID) - RF field reset nahi hogi
                data, sw1, sw2 = self.conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
                if sw1 == 0x90 and sw2 == 0x00:
                    return self.last_card  # Wahi card abhi bhi rakha hai, instant return (0ms delay)
            except Exception:
                # Transmit fail hua matlab user ne card utha liya
                print(f"[ACR1281] Card Removed from reader.")
                try: self.conn.disconnect()
                except: pass
                self.conn = None
                self.last_uid = None
                self.last_card = None
                return None

        # 3. Naya card tap hua hai ya reader idle hai
        conn = self.reader.createConnection()
        try:
            conn.connect()
            data, sw1, sw2 = conn.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
            if sw1 != 0x90:
                conn.disconnect()
                return None

            uid = ':'.join(f'{b:02X}' for b in data)
            atr = toHexString(conn.getATR())

            print("\n" + "="*50)
            print(f"[ACR1281] >>> CARD TAPPED!")
            print(f"  UID: {uid}")
            print(f"  ATR: {atr}") 

            # SCOSTA File read karo
            raw_file = self.read_scosta_file(conn)
            
            parsed, photo = None, None
            if raw_file:
                parsed, photo = self.parse_and_extract(raw_file)
                if photo:
                    import base64
                    # photo_base64 ko parsed ke andar daal diya, ab socket me khud chala jayega
                    parsed["photo_base64"] = base64.b64encode(photo).decode()
                    parsed["photo_size"] = len(photo)
                if parsed and parsed.get('name'):
                    print(f"  Student Name: {parsed.get('name')}")
                    print(f"  Roll No:      {parsed.get('roll_no')}")
            print("="*50 + "\n")

            
            card_data = {
                "uid": uid,
                "atr": atr,
                "userdata": parsed,
                "photo_size": len(photo) if photo else 0,
                "timestamp": __import__('time').strftime("%H:%M:%S")
            }

            # State save karo aur connection OPEN rakho
            self.last_uid = uid
            self.last_card = card_data
            self.conn = conn  # Connection open rakha, disconnect nahi kiya!
            return card_data

        except Exception:
            # Card nahi hai (Idle state)
            try: conn.disconnect()
            except: pass
            self.conn = None
            self.last_uid = None
            self.last_card = None
            return None


