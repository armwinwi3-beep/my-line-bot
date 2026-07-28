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
        sh = gc.open('MoneyBase')
        worksheet = sh.sheet1
        
        now = datetime.now()
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M:%S")

        # 🟢 กรณีที่ 1: ขอสรุปยอด
        if user_text == "สรุป":
            rows = worksheet.get_all_values()
            total_income = 0.0
            total_expense = 0.0
            
            for row in rows[1:]: # ข้ามบรรทัดหัวตาราง
                if len(row) >= 4:
                    try:
                        amt = float(str(row[3]).replace(',', ''))
                        if row[2] == "รายรับ":
                            total_income += amt
                        elif row[2] == "รายจ่าย":
                            total_expense += amt
                    except ValueError:
                        pass
            
            balance = total_income - total_expense
            reply_msg = (
                f"📊 สรุปบัญชีของคุณ:\n"
                f"🟢 รายรับรวม: {total_income:,.2f} บาท\n"
                f"🔴 รายจ่ายรวม: {total_expense:,.2f} บาท\n"
                f"💰 คงเหลือ: {balance:,.2f} บาท"
            )

        # 🔵 กรณีที่ 2: พิมพ์บันทึกเองแบบไม่มีสลิป (เงินสด, รายรับ)
        elif user_text.startswith("รับ ") or user_text.startswith("จ่าย "):
            parts = user_text.split()
            if len(parts) >= 3:
                action = "รายรับ" if parts[0] == "รับ" else "รายจ่าย"
                amount = parts[1]
                category = parts[2]
                account = parts[3] if len(parts) > 3 else "ไม่ได้ระบุ"
                
                # โครงสร้างตาราง: [วันที่, เวลา, ประเภท, ยอดเงิน, บัญชี, หมวดหมู่, ผู้โอน, ผู้รับ]
                worksheet.append_row([date_str, time_str, action, amount, account, category, "-", "-"])
                reply_msg = f"✅ บันทึก{action} {amount} บาท (หมวด{category}) ลงบัญชี {account} สำเร็จ!"
            else:
                reply_msg = "⚠️ รูปแบบผิดครับ พิมพ์ตามนี้ได้เลย:\nรับ [ยอดเงิน] [หมวดหมู่] [บัญชี]\nจ่าย [ยอดเงิน] [หมวดหมู่] [บัญชี]"
        
        # 🟡 กรณีที่ 3: ระบุหมวดหมู่และบัญชีตามหลังสลิป
        else:
            rows = worksheet.get_all_values()
            last_row_index = len(rows)
            
            if last_row_index > 1:
                parts = user_text.split()
                category = parts[0]
                account = parts[1] if len(parts) > 1 else "ไม่ได้ระบุ"
                
                # อัปเดตคอลัมน์ E (บัญชี) และ F (หมวดหมู่)
                worksheet.update_cell(last_row_index, 5, account)
                worksheet.update_cell(last_row_index, 6, category)
                
                reply_msg = f"📝 จัดกลุ่มสลิปนี้เป็น: ค่า'{category}' โดยใช้บัญชี '{account}' เรียบร้อยครับ!"
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
    message_content = line_bot_api.get_message_content(event.message.id)
    file_path = "temp_slip.jpg"
    
    with open(file_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)
            
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
                
                now = datetime.now()
                date_str = now.strftime("%d/%m/%Y")
                time_str = now.strftime("%H:%M:%S")
                
                try:
                    sh = gc.open('MoneyBase')
                    worksheet = sh.sheet1
                    # สลิปถือเป็น "รายจ่าย" เสมอ โดยเว้นว่าง บัญชีและหมวดหมู่ไว้ให้ผู้ใช้พิมพ์ต่อ
                    worksheet.append_row([date_str, time_str, "รายจ่าย", amount, "รอระบุบัญชี", "รอระบุหมวดหมู่", sender_name, receiver_name])
                    
                    reply_msg = (
                        f"✅ บันทึกสลิปลงชีตสำเร็จ!\n"
                        f"💸 ยอดโอนออก: {amount} บาท\n"
                        f"💡 พิมพ์บอกผมต่อได้เลยครับว่า (หมวดหมู่ บัญชี)\n"
                        f"เช่น: ค่าอาหาร กสิกร"
                    )
                except Exception as sheet_error:
                    reply_msg = f"❌ อ่านสลิปได้ แต่บันทึกลงชีตไม่สำเร็จ: {str(sheet_error)}"
            else:
                reply_msg = "❌ อ่านไม่สำเร็จครับ (รูปอาจจะไม่ใช่สลิปโอนเงินที่รองรับ)"
        else:
            reply_msg = f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ SlipOK (Status: {response.status_code})"
    except Exception as e:
        reply_msg = f"❌ เกิดข้อผิดพลาด: {str(e)}"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_msg)
    )

if __name__ == "__main__":
    app.run(port=8080)
