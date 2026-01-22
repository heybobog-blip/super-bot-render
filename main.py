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

# --- 7. Event: คนเข้ากลุ่ม (Smart Update + Auto Delete Old Warning) ---
@bot.chat_member_handler()
def on_member_change(update):
    if str(update.chat.id) == GROUP_ID_MONTHLY:
        if update.new_chat_member.status in ['member', 'administrator', 'creator']:
            if update.old_chat_member.status not in ['member', 'administrator', 'creator']:
                
                user = update.new_chat_member.user
                if user.is_bot: return

                now_thai = get_thai_time()
                is_permanent = check_is_vvip(user.id)
                
                if is_permanent:
                    expiry_str, status_str = "-", "Permanent"
                    msg = f"✅ ลูกค้า VVIP เข้ากลุ่ม: {user.first_name}\nสถานะ: ถาวร (เช็คจากยอด 999+)"
                else:
                    expiry = now_thai + datetime.timedelta(days=30)
                    expiry_str, status_str = format_date(expiry), "Active"
                    msg = f"✅ ลูกค้ารายเดือนเข้ากลุ่ม: {user.first_name}\nหมดอายุ: {expiry_str}"

                global sheet
                if sheet is None: sheet, _ = get_sheets()
                
                if sheet:
                    try:
                        existing_row = find_user_row_index(user.id)
                        
                        if existing_row:
                            # [CASE UPDATE] อัปเดตสมาชิกเดิม
                            print(f"🔄 Updating existing user at row {existing_row}")
                            
                            # 1. พยายามลบข้อความแจ้งเตือนเก่า (ถ้ามี)
                            try:
                                # อ่านค่า Msg ID จาก Col 7 (ระวัง: ถ้าแถวใหม่ยังไม่มี Col 7 อาจ error ได้จึงต้อง try)
                                val_msg_id = sheet.cell(existing_row, 7).value 
                                if val_msg_id:
                                    bot.delete_message(GROUP_ID_MONTHLY, int(val_msg_id))
                                    print(f"🗑️ Deleted old warning for {user.first_name}")
                            except Exception as del_e:
                                print(f"⚠️ Could not delete old msg: {del_e}")

                            # 2. อัปเดตข้อมูลใหม่ (รวบยอด 1 API Call)
                            # Update C(3) to G(7) -> [JoinDate, Expiry, Status, Notified, MsgID]
                            # ตั้งค่า Notified และ MsgID เป็นค่าว่าง ""
                            sheet.update(f'C{existing_row}:G{existing_row}', [[format_date(now_thai), expiry_str, status_str, "", ""]])
                            
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(อัปเดตข้อมูลเดิม แถวที่ {existing_row})")
                        else:
                            # [CASE NEW] สมาชิกใหม่
                            print(f"➕ Adding new user")
                            sheet.append_row([str(user.id), user.first_name, format_date(now_thai), expiry_str, status_str, "", ""])
                            bot.send_message(GROUP_ID_ADMIN, f"{msg}\n(ลงข้อมูลใหม่)")
                            
                    except Exception as e:
                        print(f"Save Error: {e}")
                        bot.send_message(GROUP_ID_ADMIN, f"❌ Error บันทึกข้อมูล: {e}")

# --- 8. Loop เช็ควันหมดอายุ + แจ้งเตือน + ลบข้อความ (Optimized Rate Limit) ---
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
                    if record['Status'] != 'Active' or record['Expiry Date'] in ["-", ""]:
                        continue

                    try:
                        exp_date = datetime.datetime.strptime(record['Expiry Date'], "%Y-%m-%d %H:%M:%S")
                        uid = str(record['User ID'])
                        name = record['Name']
                        remaining_time = exp_date - now
                        
                        # อ่าน Message ID เก่า (ใช้ .get เพื่อป้องกัน Error กรณีลืมใส่หัวตาราง)
                        msg_id_str = str(record.get('Message ID', '')).strip()
                        
                        # --- ส่วนที่ 1: แจ้งเตือนก่อน 2 วัน ---
                        is_notified = str(record.get('Notified', '')).strip()
                        
                        if datetime.timedelta(days=0) < remaining_time <= datetime.timedelta(days=2):
                            if is_notified != 'Yes':
                                try:
                                    mention_link = f"<a href='tg://user?id={uid}'>คุณ {name}</a>"
                                    
                                    msg_group = (
                                        f"📢 <b>แจ้งเตือนใกล้หมดอายุ</b>\n"
                                        f"ถึง {mention_link}\n"
                                        f"สถานะสมาชิกของคุณเหลือเวลาอีก {remaining_time.days} วัน {int(remaining_time.seconds/3600)} ชม.\n"
                                        f"📅 หมดอายุวันที่: {record['Expiry Date']}\n"
                                        f"<i>กรุณาติดต่อแอดมินเพื่อต่ออายุก่อนระบบจะดำเนินการอัตโนมัติครับ</i>"
                                    )
                                    
                                    # ส่งข้อความ
                                    sent_msg = bot.send_message(GROUP_ID_MONTHLY, msg_group, parse_mode='HTML')
                                    print(f"📢 Group Notify Sent: {name}")
                                    
                                    # [OPTIMIZATION] อัปเดต Col F และ G พร้อมกัน (1 API Call)
                                    # F=Notified, G=Message ID
                                    sheet.update(f'F{i}:G{i}', [['Yes', str(sent_msg.message_id)]])
                                    
                                    # [IMPORTANT] พัก 1.5 วินาที กัน Google Block
                                    time.sleep(1.5)
                                    
                                except Exception as e:
                                    print(f"⚠️ Notify Error {name}: {e}")

                        # --- ส่วนที่ 2: เช็คหมดอายุและเตะ ---
                        if now > exp_date:
                            if check_is_vvip(uid):
                                sheet.update_cell(i, 5, 'Permanent')
                                sheet.update_cell(i, 4, '-')
                                bot.send_message(GROUP_ID_ADMIN, f"👑 อัปเกรดอัตโนมัติ: คุณ {name} เป็นถาวร")
                                time.sleep(1.5)
                                continue
                            
                            print(f"🚫 Kicking: {name}")
                            try:
                                # 1. ลบข้อความแจ้งเตือนเก่าทิ้ง
                                if msg_id_str:
                                    try:
                                        bot.delete_message(GROUP_ID_MONTHLY, int(msg_id_str))
                                        print(f"🗑️ Auto-delete warning msg for {name}")
                                    except Exception as del_e:
                                        print(f"⚠️ Delete msg failed: {del_e}")

                                # 2. เตะออกจากกลุ่ม
                                bot.ban_chat_member(GROUP_ID_MONTHLY, uid)
                                bot.unban_chat_member(GROUP_ID_MONTHLY, uid)
                                
                                # 3. อัปเดตชีท (เปลี่ยน Status และล้าง Msg ID)
                                # ใช้ update_cell แยกกันแต่มี sleep คั่น ปลอดภัยกว่า
                                sheet.update_cell(i, 5, 'Expired')
                                sheet.update_cell(i, 7, "") 
                                
                                bot.send_message(GROUP_ID_ADMIN, f"🧹 เตะแล้ว: {name} (หมดอายุ) และลบแจ้งเตือนแล้ว")
                                
                                # [IMPORTANT] พัก 1.5 วินาที กัน Google Block
                                time.sleep(1.5)
                                
                            except Exception as e:
                                print(f"❌ Kick Error {name}: {e}")

                    except Exception as inner_e:
                        print(f"Row {i} Error: {inner_e}")
                        continue

            time.sleep(60)
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
