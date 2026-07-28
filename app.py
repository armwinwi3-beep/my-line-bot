import os
import requests
import gspread
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage

app = Flask(__name__)

# เชื่อมต่อกับ LINE บอท
line_bot_api = LineBotApi('ETXUTTB9PqZ1QymR0zSM4c+/7ecw+x0BIoB3jc6YB4fm20Hy7OxSV/C4jR7SDAE9hyEx/UBwoc9H7go6147rW9glQMGZO/n3XZ/lf6+Dp7vrTVP01NMzjTqEKYMCY/AfmI/ZSIi5hRDjxjufoO6sdQdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('1716fc54190bf6b7177ba7d80d3b07af')

# เชื่อมต่อกับ Google Sheets
# ⚠️ เปลี่ยนชื่อไฟล์ JSON ให้ตรงกับในเครื่องของคุณถ้าจำเป็น
gc = gspread.service_account(filename='cedar-abacus-503815-i0-be6261b65bfd.json')

@app.route("/webhook", methods=['POST'])
def webhook():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_text = event.message.text.strip()
    
    try:
        # เชื่อมต่อกับ Google Sheets
        sh = gc.open('MoneyBase')
        worksheet = sh.sheet1
        
        # 🟢 กรณีที่ 1: ถ้าพิมพ์คำว่า "สรุป"
        if user_text == "สรุป":
            # ดึงข้อมูลทั้งหมดในคอลัมน์ C (ยอดเงิน) มา
            amounts = worksheet.col_values(3)
            
            total = 0.0
            # ข้ามแถวแรกที่เป็นคำว่า "ยอดเงิน" แล้วเอาตัวเลขมาบวกกัน
            for val in amounts[1:]:
                try:
                    # ลบลูกน้ำออก (ถ้ามี) แล้วแปลงค่าเป็นตัวเลข
                    clean_val = str(val).replace(',', '')
                    total += float(clean_val)
                except ValueError:
                    pass # ถ้าอ่านตัวเลขไม่ได้ให้ข้ามไป
                    
            # จัดรูปแบบตัวเลขให้มีลูกน้ำและทศนิยม 2 ตำแหน่ง
            reply_msg = f"📊 สรุปยอดค่าใช้จ่าย:\nตอนนี้คุณโอนเงินไปแล้วทั้งหมด {total:,.2f} บาท ครับ!"
            
        # 🟡 กรณีที่ 2: ถ้าพิมพ์คำอื่นๆ (ให้ถือว่าเป็นหมวดหมู่)
        else:
            rows = worksheet.get_all_values()
            last_row_index = len(rows)
            
            if last_row_index > 1:
                worksheet.update_cell(last_row_index, 6, user_text)
                reply_msg = f"📝 บันทึกหมวดหมู่ '{user_text}' ลงรายการล่าสุดเรียบร้อยครับ!"
            else:
                reply_msg = "ยังไม่มีรายการสลิป รบกวนส่งสลิปก่อนนะครับ"
                
    except Exception as e:
        reply_msg = f"❌ เกิดข้อผิดพลาด: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_msg)
    )

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # 1. โหลดรูปจาก LINE มาเซฟลงเครื่อง
    message_content = line_bot_api.get_message_content(event.message.id)
    file_path = "temp_slip.jpg"
    
    with open(file_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)
            
    # 2. ส่งรูปให้ SlipOK อ่าน
    SLIPOK_BRANCH_ID = '72439'
    SLIPOK_API_KEY = 'SLIPOK20MVU8T'
    
    url = f'https://api.slipok.com/api/line/apikey/{SLIPOK_BRANCH_ID}'
    headers = {'x-authorization': SLIPOK_API_KEY}
    
    try:
        with open(file_path, 'rb') as f:
            files = {'files': f}
            response = requests.post(url, headers=headers, files=files)
            
        if response.status_code == 200:
            result = response.json()
            
            if result.get('success'):
                data = result['data']
                amount = data.get('amount')
                sender_name = data.get('sender', {}).get('displayName', 'ไม่ทราบชื่อ')
                receiver_name = data.get('receiver', {}).get('displayName', 'ไม่ทราบชื่อ')
                
                # 3. เตรียมข้อมูล วันที่ และ เวลา
                now = datetime.now()
                date_str = now.strftime("%d/%m/%Y")
                time_str = now.strftime("%H:%M:%S")
                
                # 4. บันทึกลง Google Sheets
                try:
                    # เปิดไฟล์ชื่อ 'บัญชีรายรับรายจ่าย' (ต้องตั้งชื่อไฟล์ให้ตรงกับตรงนี้)
                    sh = gc.open('MoneyBase')
                    worksheet = sh.sheet1 # เลือกชีตแรก
                    
                    # นำข้อมูลไปต่อท้ายตาราง (Append Row)
                    worksheet.append_row([date_str, time_str, amount, sender_name, receiver_name])
                    
                    reply_msg = (
                        f"✅ บันทึกลงชีตสำเร็จ!\n"
                        f"💸 ยอดเงิน: {amount} บาท\n"
                        f"ผู้รับ: {receiver_name}"
                    )
                except Exception as sheet_error:
                    reply_msg = f"❌ อ่านสลิปได้ แต่บันทึกลงชีตไม่สำเร็จ: {str(sheet_error)}\n(อย่าลืมแชร์ชีตให้ Email ของบอทนะครับ)"
                
            else:
                reply_msg = "❌ อ่านไม่สำเร็จครับ (รูปอาจจะไม่ใช่สลิปโอนเงินที่รองรับ)"
        else:
            reply_msg = f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ SlipOK (Status: {response.status_code})"
            
    except Exception as e:
        reply_msg = f"❌ เกิดข้อผิดพลาด: {str(e)}"

    # 5. ตอบกลับผู้ใช้
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_msg)
    )

if __name__ == "__main__":
    app.run(port=8080)
