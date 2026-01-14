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

# --- 1. ตั้งค่าตัวแปร ---
TOKEN = os.environ.get('BOT_TOKEN')
GROUP_ID_ADMIN = str(os.environ.get('GROUP_ID_ADMIN'))
GROUP_ID_MONTHLY = str(os.environ.get('GROUP_ID_MONTHLY'))
SHEET_NAME = os.environ.get('SHEET_NAME', 'Members')
PAYMENT_SHEET_NAME = "VVIP_Data"

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
        return s_main, s_pay
    except Exception as e:
        print(f"❌ Connect Error: {e}")
        return None, None

sheet, sheet_payment = get_sheets()

# --- 4. ฟังก์ชันเตะคน (Kick Logic) ---
def run_expiry_check():
    global sheet
    if sheet is None: sheet, _ = get_sheets()
    if sheet is None: return "❌ เชื่อมต่อ Sheet ไม่ได้"

    try:
        records = sheet.get_all_records()
        now = get_thai_time().replace(tzinfo=None)
        kicked_count = 0
        log_msg = []

        # เริ่มเช็คทีละแถว (เริ่มแถว 2)
        for i, record in enumerate(records, start=2):
            status = record.get('Status', '')
            expiry_str = record.get('Expiry Date', '')
            uid = str(record.get('User ID', ''))
            name = record.get('Name', 'Unknown')

            # ต้องเป็น Active และมีวันหมดอายุ
            if status == 'Active' and expiry_str and expiry_str != '-':
                try:
                    exp_date = datetime.datetime.strptime(expiry_str, "%Y-%m-%d %H:%M:%S")
                    
                    # ถ้าเวลาปัจจุบัน เลยเวลาหมดอายุแล้ว
                    if now > exp_date:
                        print(f"🚫 Kicking: {name}")
                        try:
                            # 1. เตะออกจากกลุ่ม
                            bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                            # 2. ปลดแบนทันที (เพื่อให้เข้าใหม่ได้ในอนาคต)
                            bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                            # 3. แก้สถานะใน Sheet เป็น Expired
                            sheet.update_cell(i, 5, 'Expired') 
                            
                            kicked_count += 1
                            log_msg.append(f"🚫 เตะ: {name}")
                        except Exception as e:
                            log_msg.append(f"⚠️ เตะพลาด {name}: {e}")
                except: continue

        if kicked_count > 0:
            return f"🧹 **ระบบเตะทำงาน**\n" + "\n".join(log_msg)
        return None # ไม่มีใครโดนเตะ ไม่ต้องแจ้ง

    except Exception as e:
        print(f"Check Error: {e}")
        return None

# --- 5. ระบบเตะอัตโนมัติ (วนลูปทุก 60 วิ) ---
def auto_kick_loop():
    print("⏳ Auto-Kick Loop Started...")
    while True:
        try:
            result = run_expiry_check()
            if result: # ถ้ามีการเตะเกิดขึ้น ให้แจ้งแอดมิน
                bot.send_message(GROUP_ID_ADMIN, result)
            time.sleep(60) # พัก 1 นาที
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(10)

# --- 6. เช็ค VVIP ---
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

# --- 7. บันทึกลง Sheet ---
def save_member_to_sheet(user):
    global sheet
    if sheet is None: sheet, _ = get_sheets()
    if sheet is None: return

    try:
        now_thai = get_thai_time()
        is_perm = check_is_vvip(user.id)
        
        if is_perm:
            expiry_str, status_str = "-", "Permanent"
            msg = f"✅ ลูกค้าใหม่ (ถาวร 999+): {user.first_name}\nสถานะ: ถาวรตลอดชีพ"
        else:
            expiry = now_thai + datetime.timedelta(days=30)
            expiry_str, status_str = format_date(expiry), "Active"
            msg = f"✅ ลูกค้าใหม่ (รายเดือน): {user.first_name}\nหมดอายุ: {expiry_str}"

        sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str])
        print(f"💾 Saved {user.first_name}")
        bot.send_message(GROUP_ID_ADMIN, msg)
    except Exception as e:
        print(f"❌ Save Error: {e}")

# --- 8. Event Listener (Dual Mode) ---
@bot.chat_member_handler()
def on_status_change(update):
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        # ถ้าเพิ่งเข้ามาใหม่
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                save_member_to_sheet(update.new_chat_member.user)

@bot.message_handler(content_types=['new_chat_members'])
def on_join_message(message):
    if str(message.chat.id) == GROUP_ID_MONTHLY:
        for user in message.new_chat_members:
            if not user.is_bot:
                save_member_to_sheet(user)

# --- 9. คำสั่ง Test (Admin Only) ---
@bot.message_handler(commands=['test_join', 'test_expired', 'run_check'])
def admin_cmds(message):
    if str(message.chat.id) != GROUP_ID_ADMIN: return

    # เช็คสถานะบอท
    if message.text.startswith('/test_join'):
        bot.reply_to(message, "✅ Bot Ready (Auto-Kick & Dual Mode)")

    # สั่งเช็คเตะเดี๋ยวนี้
    elif message.text.startswith('/run_check'):
        bot.reply_to(message, "⏳ กำลังเช็ครายชื่อคนหมดอายุ...")
        res = run_expiry_check()
        if res: bot.reply_to(message, res)
        else: bot.reply_to(message, "✅ ปกติ: ไม่มีใครหมดอายุ")

    # แกล้งหมดอายุ (เพื่อทดสอบการเตะ)
    elif message.text.startswith('/test_expired'):
        user = message.from_user
        now_thai = get_thai_time()
        yesterday = now_thai - datetime.timedelta(days=1) # ย้อนเวลาไปเมื่อวาน
        yesterday_str = format_date(yesterday)
        
        global sheet
        if sheet is None: sheet, _ = get_sheets()
        if sheet:
            # เพิ่มชื่อคนกดคำสั่งลงชีท แต่ใส่วันหมดอายุเป็น "เมื่อวาน"
            sheet.append_row([str(user.id), user.first_name + " (TEST)", format_date(now_thai), yesterday_str, "Active"])
            bot.reply_to(message, f"🧪 **สร้างข้อมูลทดสอบสำเร็จ!**\n👤 {user.first_name}\n📅 หมดอายุ: {yesterday_str}\n\n👉 พิมพ์ /run_check เพื่อลองเตะตัวเองได้เลย!")

# --- 10. Start Server ---
@app.route('/')
def index(): return "Bot Alive"
def run_flask(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=auto_kick_loop).start() # เปิดระบบเช็คอัตโนมัติ
    
    print("🚀 Bot Started...")
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
