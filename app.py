import os
import requests
import gspread
from datetime import datetime
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    QuickReply, QuickReplyButton, MessageAction
)

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
            total_income, total_expense = 0.0, 0.0
            for row in rows[1:]:
                if len(row) >= 4:
                    try:
                        amt = float(str(row[3]).replace(',', ''))
                        if row[2] == "รายรับ": total_income += amt
                        elif row[2] == "รายจ่าย": total_expense += amt
                    except ValueError:
                        pass
            
            balance = total_income - total_expense
            reply_msg = f"📊 สรุปบัญชี:\n🟢 รายรับ: {total_income:,.2f}\n🔴 รายจ่าย: {total_expense:,.2f}\n💰 คงเหลือ: {balance:,.2f} บาท"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
            return

        # 🔵 กรณีที่ 2: พิมพ์ตัวเลขเข้ามา (เริ่มต้นจดบัญชีเอง)
        is_number = False
        try:
            float(user_text.replace(',', ''))
            is_number = True
        except ValueError:
            pass

        if is_number:
            amount = user_text.replace(',', '')
            # สร้างแถวใหม่และใส่สถานะ "รอระบุ..." ไว้
            worksheet.append_row([date_str, time_str, "รอระบุประเภท", amount, "รอระบุบัญชี", "รอระบุหมวดหมู่", "-", "-"])
            
            # สร้างปุ่มให้กดเลือก
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="รายรับ", text="รายรับ")),
                QuickReplyButton(action=MessageAction(label="รายจ่าย", text="รายจ่าย"))
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ยอดเงิน {user_text} บาท เป็นรายรับหรือรายจ่ายครับ? 👇", quick_reply=quick_reply)
            )
            return

        # 🟡 กรณีที่ 3: จัดการปุ่มกด (เช็กสถานะจากแถวล่าสุด)
        rows = worksheet.get_all_values()
        last_row_index = len(rows)
        
        if last_row_index > 1:
            last_row = rows[-1]
            
            # ถ้ากำลังรอประเภท (รายรับ/รายจ่าย)
            if len(last_row) > 2 and last_row[2] == "รอระบุประเภท":
                worksheet.update_cell(last_row_index, 3, user_text)
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="กสิกร", text="กสิกร")),
                    QuickReplyButton(action=MessageAction(label="กรุงไทย", text="กรุงไทย")),
                    QuickReplyButton(action=MessageAction(label="TrueMoney", text="TrueMoney")),
                    QuickReplyButton(action=MessageAction(label="เงินสด", text="เงินสด"))
                ])
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เก็บเงิน/จ่ายเงิน ผ่านบัญชีไหนครับ? 👇", quick_reply=quick_reply))
                return
                
            # ถ้ากำลังรอบัญชี
            if len(last_row) > 4 and last_row[4] == "รอระบุบัญชี":
                worksheet.update_cell(last_row_index, 5, user_text)
                record_type = last_row[2]
                
                # เปลี่ยนปุ่มหมวดหมู่ ตามประเภทรายรับ-รายจ่าย
                if record_type == "รายรับ":
                    items = [
                        QuickReplyButton(action=MessageAction(label="เงินเดือน", text="เงินเดือน")),
                        QuickReplyButton(action=MessageAction(label="ค้าขาย", text="ค้าขาย")),
                        QuickReplyButton(action=MessageAction(label="อื่นๆ", text="อื่นๆ"))
                    ]
                else:
                    items = [
                        QuickReplyButton(action=MessageAction(label="🍔 อาหาร", text="อาหาร")),
                        QuickReplyButton(action=MessageAction(label="🚗 เดินทาง", text="เดินทาง")),
                        QuickReplyButton(action=MessageAction(label="ช้อปปิ้ง", text="ช้อปปิ้ง")),
                        QuickReplyButton(action=MessageAction(label="บิลต่างๆ", text="บิลต่างๆ"))
                    ]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="หมวดหมู่คืออะไรครับ? 👇", quick_reply=QuickReply(items=items)))
                return
                
            # ถ้ากำลังรอหมวดหมู่ (ขั้นสุดท้าย)
            if len(last_row) > 5 and last_row[5] == "รอระบุหมวดหมู่":
                worksheet.update_cell(last_row_index, 6, user_text)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ บันทึกรายการลงตารางเรียบร้อยครับ!"))
                return

        # ถ้าพิมพ์ข้อความอื่นๆ ที่บอทไม่เข้าใจ
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="พิมพ์ 'ตัวเลขยอดเงิน' เพื่อเริ่มจดบัญชี\nหรือส่ง 'รูปสลิป' มาได้เลยครับ 💸")
        )
                
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ เกิดข้อผิดพลาด: {str(e)}"))

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
                sender = data.get('sender', {}).get('displayName', '-')
                receiver = data.get('receiver', {}).get('displayName', '-')
                
                now = datetime.now()
                date_str = now.strftime("%d/%m/%Y")
                time_str = now.strftime("%H:%M:%S")
                
                sh = gc.open('MoneyBase')
                worksheet = sh.sheet1
                # สลิปถือเป็น "รายจ่าย" เสมอ
                worksheet.append_row([date_str, time_str, "รายจ่าย", amount, "รอระบุบัญชี", "รอระบุหมวดหมู่", sender, receiver])
                
                # เด้งปุ่มถามบัญชีต่อเลย
                quick_reply = QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="กสิกร", text="กสิกร")),
                    QuickReplyButton(action=MessageAction(label="กรุงไทย", text="กรุงไทย")),
                    QuickReplyButton(action=MessageAction(label="TrueMoney", text="TrueMoney"))
                ])
                reply_msg = f"✅ อ่านสลิปสำเร็จ! (โอนออก: {amount} บาท)\n👇 เลือกบัญชีที่โอนด้านล่างนี้ได้เลยครับ"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg, quick_reply=quick_reply))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ อ่านไม่สำเร็จครับ (รูปอาจจะไม่ใช่สลิป)"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ เชื่อมต่อ SlipOK ไม่ได้"))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ผิดพลาด: {str(e)}"))

if __name__ == "__main__":
    app.run(port=8080)
