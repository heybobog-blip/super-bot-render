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

# --- 1. Server กันหลับ ---
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

# ชื่อไฟล์/Sheet เก่า
SHEET_NAME = os.environ.get('SHEET_NAME', 'Members') 
PAYMENT_SHEET_NAME = "VVIP_Data"

# 🔴 ชื่อไฟล์/Sheet ใหม่
SHEET_JARERN_NAME = os.environ.get('SHEET_JARERN') 
TRANSACTION_SHEET_NAME = "Transactions" 

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
        
        # 1. ไฟล์หลัก
        s_main = client.open(SHEET_NAME).worksheet('Members')
        try: s_pay = client.open(PAYMENT_SHEET_NAME).sheet1
        except: s_pay = None
        
        # 2. ไฟล์ใหม่
        s_trans = None
        if SHEET_JARERN_NAME:
            try:
                s_trans = client.open(SHEET_JARERN_NAME).worksheet(TRANSACTION_SHEET_NAME)
                print("✅ Connected to Transactions Sheet")
            except Exception as e:
                print(f"⚠️ Cannot connect to Transactions sheet: {e}")

        print("✅ Google Sheet Main Connected!")
        return s_main, s_pay, s_trans
    except Exception as e:
        print(f"❌ Connect Error: {e}")
        return None, None, None

sheet, sheet_payment, sheet_transactions = get_sheets()

# --- 5. หาแถว ---
def find_user_row_index(user_id):
    global sheet
    if sheet is None: sheet, _, _ = get_sheets()
    try:
        cell = sheet.find(str(user_id))
        return cell.row
    except:
        return None

# --- 6. ดึงยอดเงินล่าสุด (แก้ User__ID ตรงนี้) ---
def get_user_payment_amount(user_id):
    global sheet_payment, sheet_transactions
    if sheet_payment is None or sheet_transactions is None: 
        _, sheet_payment, sheet_transactions = get_sheets()
    
    max_amount = 0
    
    # 1. เช็คจากชีทเก่า (VVIP_Data)
    if sheet_payment:
        try:
            records = sheet_payment.get_all_records()
            for record in records:
                r_uid = str(record.get('User ID', '')).strip()
                r_amount = record.get('Amount', 0)
                status = record.get('Status', '')
                if r_uid == str(user_id) and status == 'สำเร็จ':
                    try:
                        val = float(str(r_amount).replace(',', ''))
                        if val > max_amount: max_amount = val
                    except: continue
        except Exception as e: print(f"Check VVIP_Data Error: {e}")

    # 2. 🔴 เช็คจากชีทใหม่ (Transactions) - แก้โค้ดให้รับ User__ID
    if sheet_transactions:
        try:
            records = sheet_transactions.get_all_records()
            for record in records:
                # 🛠️ แก้ตรงนี้: เปลี่ยน User_ID เป็น User__ID (ขีดล่าง 2 อัน)
                # หรือถ้ามันหาไม่เจอจริงๆ ให้ลองหาทั้ง 2 แบบเพื่อความชัวร์
                raw_uid = record.get('User__ID') or record.get('User_ID') or ''
                r_uid = str(raw_uid).strip()

                r_amount = record.get('Amount', 0)
                status = str(record.get('Status', '')).strip()
                
                if r_uid == str(user_id) and status == 'Approved':
                    try:
                        val = float(str(r_amount).replace(',', ''))
                        if val > max_amount: max_amount = val
                    except: continue
        except Exception as e: print(f"Check Transactions Error: {e}")

    return max_amount

# --- 7. Event คนเข้า ---
@bot.chat_member_handler()
def on_member_change(update):
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                
                user = update.new_chat_member.user
                if user.is_bot: return

                now_thai = get_thai_time()
                amount = get_user_payment_amount(user.id)
                
                if amount >= 2499:
                    expiry_str, status_str = "-", "Permanent"
                    msg = f"✅ ลูกค้า 2499 เข้ากลุ่ม: {user.first_name}\nสถานะ: ถาวร (VIP)"
                elif amount >= 1299:
                    expiry = now_thai + datetime.timedelta(days=90)
                    expiry_str, status_str = format_date(expiry), "Active"
                    msg = f"✅ ลูกค้า 1299 เข้ากลุ่ม: {user.first_name}\nสถานะ: 90 วัน"
                else:
                    expiry = now_thai + datetime.timedelta(days=30)
                    expiry_str, status_str = format_date(expiry), "Active"
                    msg = f"✅ ลูกค้า 300 เข้ากลุ่ม: {user.first_name}\nสถานะ: 30 วัน"

                global sheet
                if sheet is None: sheet, _, _ = get_sheets()
                
                if sheet:
                    try:
                        existing_row = find_user_row_index(user.id)
                        if existing_row:
                            try:
                                old_status = sheet.cell(existing_row, 5).value 
                                if old_status == 'Permanent':
                                    expiry_str, status_str = "-", "Permanent"
                                    msg = f"👑 ลูกค้าเก่า (ถาวร) กลับมา: {user.first_name}\nสถานะ: ถาวร (คงสภาพเดิม)"
                            except: pass

                            try:
                                val_msg_id = sheet.cell(existing_row, 7).value 
                                if val_msg_id: bot.delete_message(GROUP_ID_MONTHLY, int(val_msg_id))
                            except: pass

                            sheet.update(f'C{existing_row}:G{existing_row}', [[format_date(now_thai), expiry_str, status_str, "", ""]])
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(อัปเดตสมาชิกเดิม)")
                        else:
                            sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str, "", ""])
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(สมาชิกใหม่)")
                    except Exception as e:
                        print(f"Save Error: {e}")

# --- 8. Loop เช็ค + แจ้งเตือน ---
def check_expiry_loop():
    print("⏳ Auto-Kick Loop Started...")
    while True:
        try:
            global sheet
            if sheet is None: sheet, _, _ = get_sheets()
            if sheet:
                records = sheet.get_all_records()
                now = get_thai_time().replace(tzinfo=None)
                
                for i, record in enumerate(records, start=2):
                    if record['Status'] == 'Permanent' or record['Expiry Date'] in ["-", ""]: continue
                    if record['Status'] != 'Active': continue

                    try:
                        exp_date = datetime.datetime.strptime(record['Expiry Date'], "%Y-%m-%d %H:%M:%S")
                        uid = str(record['User ID'])
                        name = record['Name']
                        remaining_time = exp_date - now
                        
                        msg_id_str = str(record.get('Message ID', '')).strip()
                        is_notified = str(record.get('Notified', '')).strip()
                        
                        # 1. แจ้งเตือน 2 วัน
                        if datetime.timedelta(days=0) < remaining_time <= datetime.timedelta(days=2):
                            if is_notified != 'Yes':
                                try:
                                    msg_group = (f"📢 <b>แจ้งเตือนใกล้หมดอายุ</b>\nถึง <a href='tg://user?id={uid}'>คุณ {name}</a>\n"
                                                 f"เหลือเวลา {remaining_time.days} วัน {int(remaining_time.seconds/3600)} ชม.\n"
                                                 f"หมดอายุ: {record['Expiry Date']}\n<i>ติดต่อแอดมินเพื่อต่ออายุก่อนโดนลบครับ</i>")
                                    sent_msg = bot.send_message(GROUP_ID_MONTHLY, msg_group, parse_mode='HTML')
                                    sheet.update(f'F{i}:G{i}', [['Yes', str(sent_msg.message_id)]])
                                    time.sleep(1.5)
                                except Exception as e: print(f"Notify Error: {e}")

                        # 2. หมดเวลา
                        if now > exp_date:
                            amount = get_user_payment_amount(uid)
                            if amount >= 2499:
                                sheet.update_cell(i, 5, 'Permanent')
                                sheet.update_cell(i, 4, '-')
                                bot.send_message(GROUP_ID_ADMIN, f"👑 อัปเกรดเป็นถาวร: คุณ {name}")
                                continue
                            
                            try:
                                if msg_id_str:
                                    try: bot.delete_message(GROUP_ID_MONTHLY, int(msg_id_str))
                                    except: pass

                                bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                                bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                                sheet.update_cell(i, 5, 'Expired')
                                sheet.update_cell(i, 7, "")
                                bot.send_message(GROUP_ID_ADMIN, f"🧹 เตะแล้ว: {name} (หมดอายุ)")
                                time.sleep(1.5)
                            except Exception as e: print(f"Kick Error: {e}")

                    except: continue
            time.sleep(60)
        except: time.sleep(60)

if __name__ == "__main__":
    t1 = threading.Thread(target=check_expiry_loop)
    t1.daemon = True
    t1.start()
    t2 = threading.Thread(target=run_web_server)
    t2.daemon = True
    t2.start()
    print("🚀 Bot Started...")
    bot.infinity_polling(allowed_updates=['chat_member', 'message', 'my_chat_member'])
