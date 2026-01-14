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
        print("✅ Google Sheet Connected!")
        return s_main, s_pay
    except Exception as e:
        print(f"❌ Connect Error: {e}")
        return None, None

sheet, sheet_payment = get_sheets()

# --- 4. ฟังก์ชันบันทึกลง Sheet (แยกออกมาให้เรียกใช้ง่ายๆ) ---
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

        # บันทึก
        sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str])
        print(f"💾 Saved {user.first_name} to Sheet!")
        bot.send_message(GROUP_ID_ADMIN, msg)
    except Exception as e:
        print(f"❌ Save Error: {e}")

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

# --- 6. Event Listener 1: ดักจับแบบ Status Change ---
@bot.chat_member_handler()
def on_member_status_change(update):
    print(f"⚡ Status Event: {update.chat.id}")
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        user = update.new_chat_member.user
        if user.is_bot: return
        
        # ถ้าสถานะใหม่เป็น member
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            # ถ้าสถานะเก่าไม่ใช่ member (คือเพิ่งเข้า)
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                print(f"📝 Detect via Status: {user.first_name}")
                save_member_to_sheet(user)

# --- 7. Event Listener 2: ดักจับแบบ Service Message (สำคัญ!) ---
# บอทบางตัวตาบอดเพราะขาดอันนี้
@bot.message_handler(content_types=['new_chat_members'])
def on_user_join_message(message):
    print(f"⚡ Message Event: {message.chat.id}")
    if str(message.chat.id) == GROUP_ID_MONTHLY:
        for user in message.new_chat_members:
            if not user.is_bot:
                print(f"📝 Detect via Message: {user.first_name}")
                save_member_to_sheet(user)

# --- 8. ระบบเตะคน ---
def auto_kick_loop():
    print("⏳ Auto-Kick Started...")
    while True:
        try:
            # (ใส่ Logic เตะคนตรงนี้เหมือนเดิม)
            time.sleep(60) 
        except: time.sleep(10)

# --- 9. Server กันหลับ & คำสั่ง Test ---
@app.route('/')
def index(): return "Bot Alive"
def run_flask(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

@bot.message_handler(commands=['test_join'])
def test(m):
    if str(m.chat.id) == GROUP_ID_ADMIN:
        bot.reply_to(m, "✅ Bot Ready (Dual Mode)")

# --- 10. Start ---
if __name__ == "__main__":
    t1 = threading.Thread(target=run_flask).start()
    t2 = threading.Thread(target=auto_kick_loop).start()
    
    print("🚀 Bot started with ALL updates...")
    # สำคัญ! สั่งให้รับทุก Update รวมถึง chat_member
    bot.infinity_polling(allowed_updates=['message', 'chat_member', 'my_chat_member'])
