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

# --- 1. ตั้งค่าตัวแปรจาก Render ---
TOKEN = os.environ.get('BOT_TOKEN')
GROUP_ID_ADMIN = str(os.environ.get('GROUP_ID_ADMIN'))
GROUP_ID_MONTHLY = str(os.environ.get('GROUP_ID_MONTHLY'))
SHEET_NAME = os.environ.get('SHEET_NAME', 'Members')
PAYMENT_SHEET_NAME = "VVIP_Data"

# สร้างบอทและเซิร์ฟเวอร์หลอกๆ (Flask) เพื่อกันหลับ
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# --- 2. ฟังก์ชันเวลาไทย ---
def get_thai_time():
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.datetime.now(tz)

def format_date(date_obj):
    return date_obj.strftime("%Y-%m-%d %H:%M:%S")

# --- 3. เชื่อมต่อ Google Sheet ---
def get_sheets():
    try:
        creds_json = os.environ.get('GOOGLE_KEY_JSON')
        if not creds_json:
            print("❌ Error: ไม่พบรหัส Google Key")
            return None, None
        
        try:
            creds_dict = json.loads(creds_json)
        except:
            # แก้ปัญหาถ้า Key มีการขึ้นบรรทัดใหม่ผิดเพี้ยน
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
        
        print("✅ Google Sheet Connected Success!")
        return s_main, s_pay
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None, None

# โหลด Sheet ครั้งแรก
sheet, sheet_payment = get_sheets()

# --- 4. ฟังก์ชันเตะคนอัตโนมัติ (Auto Kick) ---
def run_expiry_check():
    global sheet
    if sheet is None: sheet, _ = get_sheets()
    if sheet is None: return

    try:
        records = sheet.get_all_records()
        now = get_thai_time().replace(tzinfo=None) # เวลาปัจจุบัน
        
        # เริ่มเช็คทีละคน (เริ่มแถว 2)
        for i, record in enumerate(records, start=2):
            status = record.get('Status', '')
            expiry_str = record.get('Expiry Date', '')
            uid = str(record.get('User ID', ''))
            name = record.get('Name', 'Unknown')

            # ถ้าสถานะ Active และมีวันหมดอายุ
            if status == 'Active' and expiry_str and expiry_str != '-':
                try:
                    exp_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    
                    # ถ้าหมดอายุแล้ว (เวลาปัจจุบัน เลยกำหนดแล้ว)
                    if now > exp_date:
                        print(f"🚫 กำลังเตะ: {name} (ID: {uid})")
                        try:
                            # 1. เตะออก
                            bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                            # 2. ปลดแบนทันที (เพื่อให้เข้าใหม่วันหลังได้)
                            bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                            # 3. อัปเดตชีทเป็น Expired
                            sheet.update_cell(i, 5, 'Expired')
                            
                            # แจ้งแอดมิน
                            bot.send_message(GROUP_ID_ADMIN, f"🧹 **ระบบเตะอัตโนมัติ**\nเตะคุณ: {name}\nเหตุผล: หมดอายุสมาชิก")
                        except Exception as e:
                            print(f"❌ เตะพลาด ({name}): {e}")
                except: continue
    except Exception as e:
        print(f"❌ Error Checking Expiry: {e}")

# ลูปทำงานตลอดเวลา (เช็คทุก 60 วินาที)
def auto_kick_loop():
    print("⏳ Auto-Kick System Started...")
    while True:
        try:
            run_expiry_check()
            time.sleep(60) # พัก 1 นาที แล้วเช็คใหม่
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- 5. เช็คยอดเงิน VVIP (999+) ---
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

# --- 6. เมื่อมีคนเข้ากลุ่ม (Logic หลัก) ---
@bot.chat_member_handler()
def on_member_change(update):
    # ปริ้นท์บอกใน Logs ว่าเกิดอะไรขึ้น
    print(f"⚡ Event in Room: {update.chat.id}")

    if str(update.chat.id) == GROUP_ID_MONTHLY:
        user = update.new_chat_member.user
        if user.is_bot: return
        
        # เงื่อนไข: ถ้าเป็นสมาชิกใหม่ (เข้ามาแล้ว)
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            # กันซ้ำ (ถ้าเดิมก็อยู่อยู่แล้ว ไม่ต้องทำอะไร)
            if update.old_chat_member.status in ['member', 'administrator', 'creator']:
                return 

            print(f"📝 New Member: {user.first_name}")
            now_thai = get_thai_time()
            
            # เช็คว่าเป็น VVIP ไหม
            is_perm = check_is_vvip(user.id)
            if is_perm:
                expiry_str, status_str = "-", "Permanent"
                msg = f"✅ ลูกค้าใหม่ (ถาวร 999+): {user.first_name}\nสถานะ: ถาวรตลอดชีพ"
            else:
                expiry = now_thai + datetime.timedelta(days=30)
                expiry_str, status_str = format_date(expiry), "Active"
                msg = f"✅ ลูกค้าใหม่ (รายเดือน): {user.first_name}\nหมดอายุ: {expiry_str}"

            # บันทึกลง Sheet
            global sheet
            if sheet is None: sheet, _ = get_sheets()
            if sheet:
                try:
                    sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str])
                    print("💾 Saved to Sheet Successfully")
                    bot.send_message(GROUP_ID_ADMIN, msg)
                except Exception as e:
                    print(f"❌ Save Error: {e}")

# --- 7. คำสั่งเช็คสถานะ (Test) ---
@bot.message_handler(commands=['test_join', 'run_check'])
def admin_commands(message):
    if str(message.chat.id) == GROUP_ID_ADMIN:
        if message.text.startswith('/test_join'):
            is_perm = check_is_vvip(message.from_user.id)
            res = "✅ พบยอด 999+ (ถาวร)" if is_perm else "❌ ไม่พบยอด (รายเดือน)"
            bot.reply_to(message, f"🤖 Bot Online (Render)\n🔍 Check VVIP: {res}")
        
        elif message.text.startswith('/run_check'):
            bot.reply_to(message, "⏳ กำลังสั่งเช็ควันหมดอายุเดี๋ยวนี้...")
            run_expiry_check()
            bot.reply_to(message, "✅ ตรวจสอบเสร็จสิ้น")

# --- 8. Server กันหลับ (Flask) ---
@app.route('/')
def index():
    return "Bot is Alive on Render!"

def run_flask():
    # ใช้ Port จาก Render หรือ Default 5000
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# --- 9. เริ่มต้นทำงาน (Main) ---
if __name__ == "__main__":
    # แยกงาน 1: เปิด Server หลอกๆ (กันหลับ)
    t1 = threading.Thread(target=run_flask)
    t1.start()
    
    # แยกงาน 2: เปิดระบบเตะคน (เช็คทุกนาที)
    t2 = threading.Thread(target=auto_kick_loop)
    t2.start()

    # งานหลัก: รันบอท Telegram
    print("🚀 Bot started...")
    bot.infinity_polling()
