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
        cred_json = os.environ.get('GOOGLE_KEY_JSON')
        if not cred_json: return None, None
        
        try:
            creds_dict = json.loads(cred_json)
        except:
            fixed_json = cred_json.replace('\n', '\\n')
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

# --- 5. ฟังก์ชันหาแถวของผู้ใช้ (เพื่อไม่อัดข้อมูลซ้ำ) ---
def find_user_row_index(user_id):
    """คืนค่าหมายเลขแถว (Row Index) ถ้าเจอ User ID, ถ้าไม่เจอคืนค่า None"""
    global sheet
    if sheet is None: sheet, _ = get_sheets()
    try:
        # ดึงข้อมูลคอลัมน์ A (User ID) ทั้งหมดมาหา
        cell = sheet.find(str(user_id))
        return cell.row
    except:
        return None

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

# --- 7. Event: คนเข้ากลุ่ม (Smart Update Logic) ---
@bot.chat_member_handler()
def on_member_change(update):
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        # เช็คว่าเป็นสมาชิกใหม่ หรือ สมาชิกเก่าที่วนกลับมา
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                
                user = update.new_chat_member.user
                if user.is_bot: return

                now_thai = get_thai_time()
                is_permanent = check_is_vvip(user.id)
                
                # กำหนดสถานะและวันหมดอายุ
                if is_permanent:
                    expiry_str, status_str = "-", "Permanent"
                    msg = f"✅ ลูกค้า VVIP เข้ากลุ่ม: {user.first_name}\nสถานะ: ถาวร (เช็คจากยอด 999+)"
                else:
                    # นับไปอีก 30 วันจากวันนี้
                    expiry = now_thai + datetime.timedelta(days=30)
                    expiry_str, status_str = format_date(expiry), "Active"
                    msg = f"✅ ลูกค้ารายเดือนเข้ากลุ่ม: {user.first_name}\nหมดอายุ: {expiry_str}"

                # --- ส่วนสำคัญ: บันทึกลง Sheet (แก้ปัญหาข้อมูลซ้ำ) ---
                global sheet
                if sheet is None: sheet, _ = get_sheets()
                
                if sheet:
                    try:
                        # 1. ค้นหาว่ามี User นี้อยู่แล้วไหม?
                        existing_row = find_user_row_index(user.id)
                        
                        if existing_row:
                            # [CASE UPDATE] ถ้ามีอยู่แล้ว -> อัปเดตแถวเดิม
                            print(f"🔄 Updating existing user at row {existing_row}")
                            # Column 3=JoinDate, 4=Expiry, 5=Status, 6=Notified
                            sheet.update_cell(existing_row, 3, format_date(now_thai)) # อัปเดตวันที่เข้าล่าสุด
                            sheet.update_cell(existing_row, 4, expiry_str) # อัปเดตวันหมดอายุใหม่
                            sheet.update_cell(existing_row, 5, status_str) # รีเซ็ตสถานะเป็น Active
                            sheet.update_cell(existing_row, 6, "")         # ล้างสถานะแจ้งเตือน (Notified)
                            
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(อัปเดตข้อมูลเดิม แถวที่ {existing_row})")
                        else:
                            # [CASE NEW] ถ้าไม่มี -> เพิ่มแถวใหม่
                            print(f"➕ Adding new user")
                            # เพิ่ม User ID, Name, Join Date, Expiry, Status, Notified(ว่าง)
                            sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str, ""])
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(ลงข้อมูลใหม่)")
                            
                    except Exception as e:
                        print(f"Save Error: {e}")
                        bot.send_message(GROUP_ID_ADMIN, f"❌ Error บันทึกข้อมูล: {e}")

# --- 8. Loop เช็ควันหมดอายุ + แจ้งเตือน + Auto Upgrade ---
def check_expiry_loop():
    print("⏳ Auto-Kick & Notify Loop Started...")
    while True:
        try:
            global sheet
            if sheet is None: sheet, _ = get_sheets()
            if sheet:
                # ดึงข้อมูลทั้งหมด
                records = sheet.get_all_records()
                now = get_thai_time().replace(tzinfo=None)
                
                for i, record in enumerate(records, start=2):
                    # ข้ามคนที่เป็น Permanent หรือไม่มีวันหมดอายุ
                    if record['Status'] != 'Active' or record['Expiry Date'] in ["-", ""]:
                        continue

                    try:
                        exp_date = datetime.datetime.strptime(record['Expiry Date'], "%Y-%m-%d %H:%M:%S")
                        uid = str(record['User ID'])
                        name = record['Name']
                        remaining_time = exp_date - now
                        
                        # --- ส่วนที่ 1: แจ้งเตือนก่อน 2 วัน ---
                        is_notified = str(record.get('Notified', '')).strip()
                        
                        if datetime.timedelta(days=0) < remaining_time <= datetime.timedelta(days=2):
                            if is_notified != 'Yes':
                                try:
                                    msg_warn = (
                                        f"⚠️ <b>แจ้งเตือนใกล้หมดอายุ</b>\n"
                                        f"คุณ {name} เหลือเวลาใช้งานอีก {remaining_time.days} วัน {int(remaining_time.seconds/3600)} ชม.\n"
                                        f"📅 หมดอายุ: {record['Expiry Date']}\n"
                                        f"<i>โปรดต่ออายุก่อนกำหนด เพื่อการใช้งานที่ต่อเนื่อง</i>"
                                    )
                                    bot.send_message(uid, msg_warn, parse_mode='HTML')
                                    print(f"🔔 Notified: {name}")
                                    sheet.update_cell(i, 6, 'Yes') # ติ๊กถูกว่าแจ้งแล้ว (Column F)
                                except Exception as e:
                                    print(f"⚠️ Cannot DM {name}: {e}")

                        # --- ส่วนที่ 2: เช็คหมดอายุและเตะ ---
                        if now > exp_date:
                            # เช็ค VVIP (Auto Upgrade) เผื่อเติมเงินเพิ่มระหว่างเดือน
                            if check_is_vvip(uid):
                                sheet.update_cell(i, 5, 'Permanent')
                                sheet.update_cell(i, 4, '-')
                                bot.send_message(GROUP_ID_ADMIN, f"👑 อัปเกรดอัตโนมัติ: คุณ {name} เป็นถาวร")
                                continue
                            
                            # ถ้าไม่เจอยอด -> เตะ
                            print(f"🚫 Kicking: {name}")
                            try:
                                bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                                bot.unban_chat_member(GROUP_ID_MONTHLY, uid) # ปลดแบนทันทีเพื่อให้เข้าใหม่ได้
                                
                                sheet.update_cell(i, 5, 'Expired') # เปลี่ยนสถานะเป็น Expired
                                bot.send_message(GROUP_ID_ADMIN, f"🧹 เตะแล้ว: {name} (หมดอายุ)")
                            except Exception as e:
                                print(f"❌ Kick Error {name}: {e}")

                    except Exception as inner_e:
                        print(f"Row {i} Error: {inner_e}")
                        continue

            time.sleep(60) # เช็คทุก 1 นาที
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(60)

# --- 9. เริ่มทำงาน ---
if __name__ == "__main__":
    t1 = threading.Thread(target=check_expiry_loop)
    t1.daemon = True
    t1.start()
    
    t2 = threading.Thread(target=run_web_server)
    t2.daemon = True
    t2.start()
    
    print("🚀 Bot Started...")
    bot.infinity_polling(allowed_updates=['chat_member', 'message', 'my_chat_member'])
