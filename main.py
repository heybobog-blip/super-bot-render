import telebot
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import pytz
import time
import threading
import os
import json
from flask import Flask

# --- 1. ส่วน Server หลอกๆ (กันหลับบน Render) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running OK!"

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- 2. ตั้งค่าตัวแปร ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
GROUP_ID_MONTHLY = str(os.environ.get('GROUP_ID_MONTHLY'))
GROUP_ID_ADMIN = str(os.environ.get('GROUP_ID_ADMIN'))
SHEET_NAME = os.environ.get('SHEET_NAME', 'Members')
PAYMENT_SHEET_NAME = "VVIP_Data"

bot = telebot.TeleBot(BOT_TOKEN)

# --- 3. ฟังก์ชันเวลาไทย ---
def get_thai_time():
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.datetime.now(tz)

def format_date(date_obj):
    return date_obj.strftime("%Y-%m-%d %H:%M:%S")

# --- 4. เชื่อมต่อ Google Sheets ---
def get_sheets():
    try:
        creds_json = os.environ.get('GOOGLE_KEY_JSON')
        if not creds_json: return None, None
        
        try:
            creds_dict = json.loads(creds_json)
        except:
            fixed_json = creds_json.replace('\n', '\\n')
            creds_dict = json.loads(fixed_json)

        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        
        s_main = client.open(SHEET_NAME).worksheet('Members')
        try:
            s_pay = client.open(PAYMENT_SHEET_NAME).sheet1
        except:
            s_pay = None
        
        print("✅ Google Sheet Connected!")
        return s_main, s_pay
    except Exception as e:
        print(f"❌ Connect Error: {e}")
        return None, None

sheet, sheet_payment = get_sheets()

# --- 5. เช็ค VVIP ---
def check_is_vvip(user_id):
    global sheet_payment
    if sheet_payment is None: _, sheet_payment = get_sheets()
    if sheet_payment is None: return False
    try:
        records = sheet_payment.get_all_records()
        for record in records:
            r_uid = str(record.get('User ID', '')).strip()
            r_amount = record.get('Amount', 0)
            if r_uid == str(user_id):
                try:
                    if float(str(r_amount).replace(',', '')) >= 999: return True
                except: continue
        return False
    except: return False

# --- 6. Event: คนเข้ากลุ่ม (ของจริง) ---
@bot.chat_member_handler()
def on_member_change(update):
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        # ถ้าเป็นการเข้ากลุ่มใหม่ (member/admin/creator)
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            # เช็คว่าเดิมไม่ได้อยู่ (กันซ้ำ)
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                
                user = update.new_chat_member.user
                if user.is_bot: return

                now_thai = get_thai_time()
                is_permanent = check_is_vvip(user.id)
                
                if is_permanent:
                    expiry_str, status_str = "-", "Permanent"
                    msg = f"✅ ลูกค้าใหม่ (ถาวร 999+): {user.first_name}\nสถานะ: ถาวรตลอดชีพ"
                else:
                    expiry = now_thai + datetime.timedelta(days=30)
                    expiry_str, status_str = format_date(expiry), "Active"
                    msg = f"✅ ลูกค้าใหม่ (รายเดือน): {user.first_name}\nหมดอายุ: {expiry_str}"

                # บันทึก
                global sheet
                if sheet is None: sheet, _ = get_sheets()
                if sheet:
                    try:
                        sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str])
                        bot.send_message(GROUP_ID_ADMIN, msg)
                    except Exception as e:
                        print(f"Save Error: {e}")

# --- 7. คำสั่ง Test Join (จำลอง 1 นาที) ---
@bot.message_handler(commands=['test_join'])
def test_simulation(message):
    if str(message.chat.id) == GROUP_ID_ADMIN:
        user = message.from_user
        now_thai = get_thai_time()
        
        is_permanent = check_is_vvip(user.id)
        
        if is_permanent:
             expiry_str = "-"
             status_str = "Permanent"
             resp = "✅ (Test) พบยอด 999+ (ลงชีทแบบถาวร)"
        else:
             # ตั้งเวลาหมดอายุแค่ 1 นาที (เพื่อนับถอยหลังรอเตะ)
             expiry = now_thai + datetime.timedelta(minutes=1)
             expiry_str = format_date(expiry)
             status_str = "Active"
             resp = f"✅ (Test) ไม่พบยอด (ลงชีทแบบ 1 นาที)\n💀 จะหมดอายุตอน: {expiry_str}"

        global sheet
        if sheet is None: sheet, _ = get_sheets()
        if sheet:
            try:
                sheet.append_row([str(user.id), user.first_name + " (TEST)", format_date(now_thai), expiry_str, status_str])
                bot.reply_to(message, resp)
            except Exception as e: 
                bot.reply_to(message, f"Error: {e}")

# --- 8. Loop เช็ควันหมดอายุ (เตะคน) ---
def check_expiry_loop():
    print("⏳ Auto-Kick Loop Started...")
    while True:
        try:
            global sheet
            if sheet is None: sheet, _ = get_sheets()
            if sheet:
                records = sheet.get_all_records()
                now = get_thai_time().replace(tzinfo=None) # ตัด Timezone เพื่อเทียบกับ String ในชีท
                
                for i, record in enumerate(records, start=2):
                    # ข้ามพวกถาวร หรือพวกที่หมดอายุไปแล้ว
                    if record['Status'] != 'Active' or record['Expiry Date'] == "-" or record['Expiry Date'] == "":
                        continue

                    try:
                        exp_date = datetime.datetime.strptime(record['Expiry Date'], "%Y-%m-%d %H:%M:%S")
                        
                        # ถ้าเวลาปัจจุบัน เลยเวลาหมดอายุแล้ว
                        if now > exp_date:
                            uid = str(record['User ID'])
                            name = record['Name']
                            
                            # เช็ค VVIP อีกรอบเผื่อเขาเติมเงินเพิ่ม (Auto Upgrade)
                            if check_is_vvip(uid):
                                sheet.update_cell(i, 5, 'Permanent')
                                sheet.update_cell(i, 4, '-')
                                bot.send_message(GROUP_ID_ADMIN, f"👑 อัปเกรดคุณ {name} เป็นถาวร (เจอยอดใหม่)")
                                continue
                            
                            # ถ้าไม่เจอยอด -> เตะ!
                            print(f"🚫 Kicking: {name}")
                            try:
                                bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                                bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                                sheet.update_cell(i, 5, 'Expired') # เปลี่ยนสถานะในชีท
                                bot.send_message(GROUP_ID_ADMIN, f"🧹 หมดเวลา: {name} (เตะเรียบร้อย)")
                            except Exception as e:
                                print(f"Kick Error: {e}")
                    except: continue

            time.sleep(60) # เช็คทุก 1 นาที
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(60)

# --- 9. เริ่มทำงาน ---
if __name__ == "__main__":
    # แยก Thread 1: ลูปเตะคน
    t1 = threading.Thread(target=check_expiry_loop)
    t1.daemon = True
    t1.start()
    
    # แยก Thread 2: Web Server (กันหลับ)
    t2 = threading.Thread(target=run_web_server)
    t2.daemon = True
    t2.start()
    
    # งานหลัก: บอท
    print("🚀 Bot Started...")
    # allowed_updates เพื่อให้มั่นใจว่ารับ event เข้าออกได้ชัวร์
    bot.infinity_polling(allowed_updates=['chat_member', 'message', 'my_chat_member'])
