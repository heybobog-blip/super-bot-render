import telebot
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import pytz
import time
import threading
import os
import json
import html
from flask import Flask

# --- 1. Server กันหลับ ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running OK (Final Production)!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- 2. ตั้งค่าตัวแปร ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
GROUP_ID_MONTHLY = str(os.environ.get('GROUP_ID_MONTHLY'))
GROUP_ID_ADMIN = str(os.environ.get('GROUP_ID_ADMIN'))

# ชื่อไฟล์
SHEET_MEMBERS_FILE = os.environ.get('SHEET_NAME', 'Members') 
SHEET_PAYMENT_FILE = os.environ.get('SHEET_JARERN', 'JaroenPorn_DB')

bot = telebot.TeleBot(BOT_TOKEN)

# --- 3. เวลาไทย ---
def get_thai_time():
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.datetime.now(tz)

def format_date(date_obj):
    return date_obj.strftime("%Y-%m-%d %H:%M:%S")

# --- 4. เชื่อมต่อ Google Sheets ---
def get_sheets():
    try:
        cred_json = os.environ.get('GOOGLE_KEY_JSON')
        if not cred_json: return None, None, None
        try: creds_dict = json.loads(cred_json)
        except: creds_dict = json.loads(cred_json.replace('\n', '\\n'))

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        # 1. ไฟล์สมาชิก (Members)
        sh_mem = client.open(SHEET_MEMBERS_FILE)
        ws_legacy = sh_mem.worksheet('Members')   # หน้าเก่า
        ws_active = sh_mem.worksheet('Members2')  # หน้าใหม่

        # 2. ไฟล์จ่ายเงิน (JaroenPorn_DB)
        ws_pay = None
        try:
            ws_pay = client.open(SHEET_PAYMENT_FILE).sheet1
            print("✅ Connected to Payment Sheet")
        except Exception as e:
            print(f"⚠️ Cannot connect to Payment sheet: {e}")

        print("✅ Google Sheets Connected")
        return ws_legacy, ws_active, ws_pay
    except Exception as e:
        print(f"❌ Connect Error: {e}")
        return None, None, None

# โหลด Sheet ครั้งแรก
sheet_legacy, sheet_active, sheet_payment = get_sheets()

# --- 5. ฟังก์ชันเช็คยอดเงิน ---
def check_new_payment(user_id):
    global sheet_legacy, sheet_active, sheet_payment
    if sheet_payment is None: _, _, sheet_payment = get_sheets()
    
    max_amount = 0
    try:
        records = sheet_payment.get_all_records()
        for record in records:
            r_uid = str(record.get('User ID', '')).strip()
            # 🛡️ ป้องกัน Human Error: แปลงเป็นตัวเล็กก่อนเทียบ (active/Active)
            status = str(record.get('สถานะ', '')).strip().lower()
            
            if r_uid == str(user_id) and status == 'active':
                try:
                    # 🛡️ ป้องกัน Error ช่องว่าง: ถ้าไม่มีเลขให้ข้าม
                    amount_str = str(record.get('ยอดเงิน', 0)).replace(',', '').strip()
                    if not amount_str: continue 
                    
                    val = float(amount_str)
                    if val > max_amount: max_amount = val
                except: continue
    except Exception as e:
        print(f"Check Payment Error: {e}")
    return max_amount

# --- 6. ฟังก์ชันเช็คข้อมูลเก่า ---
def check_legacy_data(user_id):
    global sheet_legacy
    if sheet_legacy is None: sheet_legacy, _, _ = get_sheets()
    try:
        cell = sheet_legacy.find(str(user_id))
        if cell:
            row = cell.row
            data = sheet_legacy.row_values(row)
            return {
                'found': True,
                'join_date': data[2] if len(data) > 2 else '-',
                'expiry_date': data[3] if len(data) > 3 else '-',
                'status': data[4] if len(data) > 4 else '-'
            }
    except: pass
    return {'found': False}

# --- 7. ฟังก์ชันหาแถวใน Members2 ---
def find_active_row_data(user_id):
    global sheet_active
    if sheet_active is None: _, sheet_active, _ = get_sheets()
    try:
        cell = sheet_active.find(str(user_id))
        if cell:
            status = sheet_active.cell(cell.row, 5).value # Column E
            return {'row': cell.row, 'status': status}
    except: pass
    return None

# --- 8. Event คนเข้า (โหมด Debug: ฟ้องทุกอย่าง) ---
@bot.chat_member_handler()
def on_member_change(update):
    # 1. ปริ้น ID ของกลุ่มที่เกิดเรื่องออกมาดูเลย
    incoming_group_id = str(update.chat.id)
    target_group_id = str(GROUP_ID_MONTHLY).strip()
    
    print(f"--------------------------------------------------")
    print(f"📡 EVENT DETECTED!")
    print(f"   - Event Type: chat_member")
    print(f"   - Group ID (Incoming): {incoming_group_id}")
    print(f"   - Group ID (Target):   {target_group_id}")
    
    # 2. เช็คว่า ID ตรงกันไหม
    if incoming_group_id != target_group_id: 
        print(f"❌ MISMATCH: เลขกลุ่มไม่ตรงกัน! บอทจึงไม่ทำงาน")
        print(f"   --> ให้เอาเลข {incoming_group_id} ไปแก้ใน Environment Variable")
        return

    # 3. ดูสถานะคนเข้า
    new_status = update.new_chat_member.status
    old_status = update.old_chat_member.status
    user = update.new_chat_member.user
    
    print(f"👤 User: {user.first_name} (ID: {user.id})")
    print(f"   - Status Change: {old_status} -> {new_status}")

    # เช็คเงื่อนไขคนเข้าใหม่
    is_joining = new_status in ['member', 'administrator', 'creator']
    was_outsider = old_status not in ['member', 'administrator', 'creator']

    if not (is_joining and was_outsider):
        print(f"⚠️ SKIPPING: ไม่ใช่การเข้ากลุ่มใหม่ (อาจเป็นการแก้สิทธิ์ หรือ Re-join แบบ cache)")
        return
        
    if user.is_bot: 
        print("🤖 User is a bot. Skip.")
        return
        
    print(f"✅ PASS: เงื่อนไขถูกต้อง เริ่มกระบวนการตรวจสอบ...")
    
    # --- เริ่ม Logic เดิม ---
    now_thai = get_thai_time()
    final_amount = 0
    is_legacy_migration = False
    legacy_info = {}
    
    renewal_row = None
    is_renewal = False

    # STEP 1: เช็คสถานะใน Members2
    existing_data = find_active_row_data(user.id)
    if existing_data:
        current_status = str(existing_data['status']).strip().lower()
        print(f"   - Found in Members2 with status: {current_status}") # Debug
        if current_status in ['permanent', 'active']:
            print(f"✅ User {user.first_name} is Active/Permanent. Skip.")
            return
        else:
            print(f"🔄 User {user.first_name} is Expired. Checking for Renewal...")
            renewal_row = existing_data['row']
            is_renewal = True
    else:
        print(f"   - Not found in Members2") # Debug

    # STEP 2: เช็คยอดเงินใหม่
    payment_amount = check_new_payment(user.id)
    print(f"   - Payment Check Result: {payment_amount}") # Debug
    
    if payment_amount > 0:
        final_amount = payment_amount
        print(f"💰 Found New Payment: {final_amount}")
    else:
        # STEP 3: เช็คสิทธิ์เก่า
        if not is_renewal:
            legacy_data = check_legacy_data(user.id)
            if legacy_data['found']:
                status = legacy_data['status']
                expiry_str = legacy_data['expiry_date']
                is_valid_legacy = False
                
                if status == 'Permanent':
                    is_valid_legacy = True
                    final_amount = 2500
                elif status == 'Active':
                    try:
                        exp_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                        exp_date = exp_date.replace(tzinfo=None)
                        now_no_tz = now_thai.replace(tzinfo=None)
                        if exp_date > now_no_tz:
                            is_valid_legacy = True
                            final_amount = 100
                    except: pass
                
                if is_valid_legacy:
                    is_legacy_migration = True
                    legacy_info = legacy_data
                    print(f"♻️ Valid Legacy User Found: {status}")
            else:
                print(f"   - Legacy Data Not Found") # Debug

    # STEP 4: ตัดสินใจ
    if final_amount == 0 and not is_legacy_migration:
        print(f"🚫 DECISION: KICKING {user.first_name}")
        try:
            bot.send_message(user.id, "❌ ไม่พบยอดเงิน หรือ สิทธิ์เดิมหมดอายุแล้ว\nกรุณาแจ้งสลิปใหม่")
            bot.ban_chat_member(GROUP_ID_MONTHLY, user.id)
            bot.unban_chat_member(GROUP_ID_MONTHLY, user.id)
            print(f"   - Kick Command Sent")
        except Exception as e: 
            print(f"   - Kick Failed: {e}")
        return

    # คำนวณวันหมดอายุ
    save_expiry_str = "-"
    save_status = "Active"
    msg_plan = ""

    if is_legacy_migration:
        save_expiry_str = legacy_info['expiry_date']
        save_status = legacy_info['status']
        msg_plan = f"Migrated ({save_status})"
    else:
        if final_amount >= 2499:
            save_expiry_str = "-"
            save_status = "Permanent"
            msg_plan = "VVIP ถาวร"
        elif final_amount >= 1299:
            expiry = now_thai + datetime.timedelta(days=90)
            save_expiry_str = format_date(expiry)
            msg_plan = "90 วัน"
        else:
            expiry = now_thai + datetime.timedelta(days=30)
            save_expiry_str = format_date(expiry)
            msg_plan = "30 วัน"

    # บันทึก
    global sheet_active
    if sheet_active is None: _, sheet_active, _ = get_sheets()

    try:
        if is_renewal and renewal_row:
            sheet_active.update(f'D{renewal_row}:G{renewal_row}', [[save_expiry_str, save_status, "", ""]])
            bot.send_message(GROUP_ID_ADMIN, f"✅ ต่ออายุสมาชิก: {user.first_name}\nPlan: {msg_plan}")
            print(f"Updated renewal for {user.first_name}")
        else:
            join_date_save = legacy_info['join_date'] if is_legacy_migration else format_date(now_thai)
            sheet_active.append_row([
                str(user.id),
                user.first_name,
                join_date_save,
                save_expiry_str,
                save_status,
                "", ""
            ])
            bot.send_message(GROUP_ID_ADMIN, f"✅ สมาชิกใหม่/ย้ายบ้าน: {user.first_name}\nPlan: {msg_plan}")
            print(f"Appended new user {user.first_name}")
        
    except Exception as e:
        print(f"❌ Save Error: {e}")

# --- 9. Loop เช็ควันหมดอายุ ---
def check_expiry_loop():
    print("⏳ Auto-Kick Loop Started...")
    while True:
        try:
            global sheet_active
            if sheet_active is None: _, sheet_active, _ = get_sheets()
            
            records = sheet_active.get_all_records()
            now = get_thai_time().replace(tzinfo=None)
            
            for i, record in enumerate(records, start=2):
                status = str(record.get('Status', '')).strip()
                expiry_str = record.get('Expiry Date')
                
                # 🛡️ ป้องกัน Human Error: แปลงเป็นตัวเล็กก่อนเช็ค
                if status.lower() != 'active' or expiry_str in ['-', '']: continue
                
                try:
                    exp_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    uid = str(record.get('User ID'))
                    name = record.get('Name')
                    remaining = exp_date - now
                    
                    msg_id_str = str(record.get('Message ID', '')).strip()
                    is_notified = str(record.get('Notified', '')).strip()

                    # 1. เตะคนหมดอายุ
                    if now > exp_date:
                        try:
                            print(f"🔨 Kicking {name}...")
                            bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                            bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                            
                            if msg_id_str:
                                try: bot.delete_message(GROUP_ID_MONTHLY, int(msg_id_str))
                                except: pass

                            sheet_active.update_cell(i, 5, 'Expired')
                            sheet_active.update_cell(i, 7, "")
                            bot.send_message(GROUP_ID_ADMIN, f"🧹 เตะแล้ว: {name} (หมดอายุ)")
                            
                            time.sleep(1)
                        except Exception as e:
                            print(f"Kick Error: {e}")
                            
                    # 2. แจ้งเตือน (< 2 วัน)
                    elif datetime.timedelta(days=0) < remaining <= datetime.timedelta(days=2):
                        if is_notified != 'Yes':
                            # 🛡️ ใช้ html.escape เพื่อกันชื่อที่มีอักขระแปลกๆ
                            safe_name = html.escape(name)
                            msg = (f"📢 <b>แจ้งเตือนใกล้หมดอายุ</b>\nถึง <a href='tg://user?id={uid}'>คุณ {safe_name}</a>\n"
                                   f"เหลือเวลา {remaining.days} วัน {int(remaining.seconds/3600)} ชม.\n"
                                   f"หมดอายุ: {expiry_str}\n<i>ติดต่อแอดมินเพื่อต่ออายุครับ</i>")
                            try:
                                sent = bot.send_message(GROUP_ID_MONTHLY, msg, parse_mode='HTML')
                                sheet_active.update(f'F{i}:G{i}', [['Yes', str(sent.message_id)]])
                                time.sleep(1)
                            except: pass
                            
                except ValueError: continue 
                except Exception as inner_e: print(f"Row Error: {inner_e}")
                
            time.sleep(60)
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    t1 = threading.Thread(target=check_expiry_loop)
    t1.daemon = True
    t1.start()
    t2 = threading.Thread(target=run_web_server)
    t2.daemon = True
    t2.start()
    print("🚀 Bot Started...")
    bot.infinity_polling(allowed_updates=['chat_member', 'message', 'my_chat_member'])
