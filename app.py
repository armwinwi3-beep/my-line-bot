import os
import requests
import gspread
import random
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    QuickReply, QuickReplyButton, MessageAction
)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is awake and running!", 200

# เชื่อมต่อกับ LINE บอท
line_bot_api = LineBotApi('ETXUTTB9PqZ1QymR0zSM4c+/7ecw+x0BIoB3jc6YB4fm20Hy7OxSV/C4jR7SDAE9hyEx/UBwoc9H7go6147rW9glQMGZO/n3XZ/lf6+Dp7vrTVP01NMzjTqEKYMCY/AfmI/ZSIi5hRDjxjufoO6sdQdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('1716fc54190bf6b7177ba7d80d3b07af')

# เชื่อมต่อกับ Google Sheets
gc = gspread.service_account(filename='cedar-abacus-503815-i0-be6261b65bfd.json')

# 🕒 ตั้งค่าเวลาประเทศไทย (UTC+7)
TH_TZ = timezone(timedelta(hours=7))

def get_current_worksheet():
    sh = gc.open('MoneyBase')
    now = datetime.now(TH_TZ)
    sheet_name = now.strftime("%m/%y")
    
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows=1000, cols=10)
        headers = ["วันที่", "เวลา", "ประเภท (รายรับ/รายจ่าย)", "ยอดเงิน", "บัญชี (เงินสด / กสิกร / กรุงไทย / TrueMoney)", "หมวดหมู่", "ผู้โอน", "ผู้รับ", "สถานะบิล", "บัญชีที่จ่าย"]
        worksheet.append_row(headers)
        
    return worksheet

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
        worksheet = get_current_worksheet()
        
        now = datetime.now(TH_TZ)
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M:%S")

        current_month_str = now.strftime("%m/%Y")
        first_day_this_month = now.replace(day=1)
        last_month_date = first_day_this_month - timedelta(days=1)
        last_month_str = last_month_date.strftime("%m/%Y")

        # 🟢 1. เช็คบิลที่ยังไม่จ่าย (สแกนหาบิลค้างชำระจาก "ทุกแท็บ")
        if user_text.lower() == "bill":
            unpaid_bills = []
            for ws in sh.worksheets():
                rows = ws.get_all_values()
                for i, row in enumerate(rows):
                    if len(row) >= 9:
                        if row[2] == "รายจ่ายต้องชำระต่อเดือน" and row[8] == "ยังไม่จ่าย":
                            cat = row[5] if len(row) > 5 else "ไม่ระบุ"
                            unpaid_bills.append((cat, row[3]))
            
            if not unpaid_bills:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🎉 เดือนนี้ไม่มีบิลค้างชำระครับ!"))
                return
                
            reply_msg = "🧾 บิลที่ยังไม่จ่าย:\n"
            items = []
            for cat, amt in unpaid_bills:
                reply_msg += f"• {cat} : {amt} บาท\n"
                if len(items) < 13:
                    items.append(QuickReplyButton(action=MessageAction(label=f"จ่าย {cat}", text=f"อัปเดตบิล {cat}")))
                    
            reply_msg += "\n👇 กดปุ่มด้านล่างเพื่อเลือกจ่ายบิลได้เลยครับ"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg, quick_reply=QuickReply(items=items)))
            return

        # 🟢 2. ขั้นตอนเลือกบิลเพื่อไปเลือกบัญชีต่อ
        if user_text.startswith("อัปเดตบิล "):
            target_cat = user_text.replace("อัปเดตบิล ", "").strip()
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="กสิกร", text=f"จ่ายผ่าน|{target_cat}|กสิกร")),
                QuickReplyButton(action=MessageAction(label="กรุงไทย", text=f"จ่ายผ่าน|{target_cat}|กรุงไทย")),
                QuickReplyButton(action=MessageAction(label="TrueMoney", text=f"จ่ายผ่าน|{target_cat}|TrueMoney")),
                QuickReplyButton(action=MessageAction(label="เงินสด", text=f"จ่ายผ่าน|{target_cat}|เงินสด"))
            ])
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(text=f"เลือกบัญชีที่ใช้จ่ายบิล '{target_cat}' ครับ 👇", quick_reply=quick_reply)
            )
            return

        # 🟢 3. บันทึกสถานะ "จ่ายแล้ว" พร้อมบัญชีที่จ่ายจริง (ค้นหาและอัปเดตจากทุกแท็บ)
        if user_text.startswith("จ่ายผ่าน|"):
            parts = user_text.split("|")
            target_cat = parts[1]
            chosen_account = parts[2]
            
            found = False
            for ws in reversed(sh.worksheets()):  # ค้นหาจากแท็บเดือนล่าสุดย้อนกลับไป
                rows = ws.get_all_values()
                for i in range(len(rows)-1, 0, -1):
                    row = rows[i]
                    if len(row) >= 9:
                        if row[2] == "รายจ่ายต้องชำระต่อเดือน" and row[5] == target_cat and row[8] == "ยังไม่จ่าย":
                            ws.update_cell(i+1, 9, "จ่ายแล้ว")
                            while len(ws.row_values(i+1)) < 10:
                                ws.update_cell(i+1, 10, "-")
                            ws.update_cell(i+1, 10, chosen_account)
                            found = True
                            break
                if found:
                    break
                        
            if found:
                line_bot_api.reply_message(
                    event.reply_token, 
                    TextSendMessage(text=f"✅ อัปเดตบิล '{target_cat}' เป็น 'จ่ายแล้ว' (จ่ายผ่านบัญชี {chosen_account}) เรียบร้อย หักยอดเงินสำเร็จ!")
                )
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ ไม่พบบิล '{target_cat}' ที่ยังไม่จ่ายครับ"))
            return

        # 🟢 4. สรุปยอด
        if user_text == "สรุป":
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="📊 สรุปเดือนนี้", text="สรุปเดือนนี้")),
                QuickReplyButton(action=MessageAction(label="📅 สรุปเดือนที่แล้ว", text="สรุปเดือนที่แล้ว")),
                QuickReplyButton(action=MessageAction(label="📈 สรุปทั้งหมด", text="สรุปทั้งหมด"))
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ต้องการดูสรุปบัญชีของช่วงเวลาไหนครับ? 👇", quick_reply=quick_reply)
            )
            return

        if user_text in ["สรุปเดือนนี้", "สรุปเดือนที่แล้ว", "สรุปทั้งหมด"]:
            target_sheets_names = []
            if user_text == "สรุปเดือนนี้":
                target_sheets_names = [now.strftime("%m/%y")]
                month_label = f"เดือน {current_month_str}"
            elif user_text == "สรุปเดือนที่แล้ว":
                target_sheets_names = [last_month_date.strftime("%m/%y")]
                month_label = f"เดือน {last_month_str}"
            else:
                target_sheets_names = [ws.title for ws in sh.worksheets()]
                month_label = "ทั้งหมด"

            # ตัวแปรสำหรับคำนวณรายรับ/รายจ่าย เฉพาะเดือนที่เลือก
            total_income, total_expense = 0.0, 0.0
            total_monthly_paid, total_monthly_unpaid = 0.0, 0.0
            income_accounts_monthly, expense_accounts_monthly = {}, {}
            monthly_paid_cats, monthly_unpaid_cats = {}, {}
            
            # ตัวแปรสำหรับยอดเงินคงเหลือสะสม จาก 'ทุกแท็บ'
            income_accounts_all, expense_accounts_all, monthly_accounts_all = {}, {}, {}
            transfer_in_all, transfer_out_all = {}, {}
            
            for ws in sh.worksheets():
                rows = ws.get_all_values()
                is_target_month = (ws.title in target_sheets_names)
                
                for row in rows[1:]:
                    if len(row) >= 4:
                        try:
                            amt = float(str(row[3]).replace(',', ''))
                            record_type = row[2]
                            account = row[4] if len(row) > 4 and row[4].strip() != "" else "-"
                            cat = row[5] if len(row) > 5 and row[5].strip() != "" else "ไม่ระบุหมวดหมู่"
                            
                            # คำนวณยอดเงินสะสม (เพื่อหายอดคงเหลือรวม)
                            if record_type == "รายรับ": 
                                income_accounts_all[account] = income_accounts_all.get(account, 0) + amt
                            elif record_type == "รายจ่าย": 
                                expense_accounts_all[account] = expense_accounts_all.get(account, 0) + amt
                            elif record_type == "รายจ่ายต้องชำระต่อเดือน":
                                status = row[8] if len(row) > 8 and row[8].strip() != "" else "จ่ายแล้ว"
                                if status != "ยังไม่จ่าย":
                                    paid_account = row[9] if len(row) > 9 and row[9].strip() != "" and row[9] != "-" else account
                                    if paid_account != "-":
                                        monthly_accounts_all[paid_account] = monthly_accounts_all.get(paid_account, 0) + amt
                            elif record_type == "ย้ายเงิน":
                                src_acc = account
                                dst_acc = cat
                                if src_acc != "-": transfer_out_all[src_acc] = transfer_out_all.get(src_acc, 0) + amt
                                if dst_acc != "-": transfer_in_all[dst_acc] = transfer_in_all.get(dst_acc, 0) + amt

                            # คำนวณรายรับรายจ่ายเฉพาะเดือนนั้นๆ (เพื่อแสดงผล)
                            if is_target_month:
                                if record_type == "รายรับ": 
                                    total_income += amt
                                    income_accounts_monthly[account] = income_accounts_monthly.get(account, 0) + amt
                                elif record_type == "รายจ่าย": 
                                    total_expense += amt
                                    expense_accounts_monthly[account] = expense_accounts_monthly.get(account, 0) + amt
                                elif record_type == "รายจ่ายต้องชำระต่อเดือน":
                                    status = row[8] if len(row) > 8 and row[8].strip() != "" else "จ่ายแล้ว"
                                    if status == "ยังไม่จ่าย":
                                        total_monthly_unpaid += amt
                                        monthly_unpaid_cats[cat] = monthly_unpaid_cats.get(cat, 0) + amt
                                    else:
                                        total_monthly_paid += amt
                                        monthly_paid_cats[cat] = monthly_paid_cats.get(cat, 0) + amt
                        except ValueError:
                            pass
            
            # สรุปยอดคงเหลือสะสม
            all_accounts = set(income_accounts_all.keys()) | set(expense_accounts_all.keys()) | set(monthly_accounts_all.keys()) | set(transfer_in_all.keys()) | set(transfer_out_all.keys())
            balance_accounts = {}
            total_balance = 0.0
            
            for acc in all_accounts:
                if acc == "-": continue
                bal = (income_accounts_all.get(acc, 0.0) 
                       - expense_accounts_all.get(acc, 0.0) 
                       - monthly_accounts_all.get(acc, 0.0) 
                       + transfer_in_all.get(acc, 0.0) 
                       - transfer_out_all.get(acc, 0.0))
                balance_accounts[acc] = bal
                total_balance += bal
            
            reply_msg = f"📊 สรุปบัญชี ({month_label}):\n\n"
            reply_msg += f"🟢 รายรับรวม: {total_income:,.2f} บาท\n"
            for acc, amt in income_accounts_monthly.items():
                if acc != "-": reply_msg += f"   • {acc}: {amt:,.2f}\n"
                
            reply_msg += f"\n🔴 รายจ่ายทั่วไป: {total_expense:,.2f} บาท\n"
            for acc, amt in expense_accounts_monthly.items():
                if acc != "-": reply_msg += f"   • {acc}: {amt:,.2f}\n"
                
            reply_msg += f"\n🟡 บิลต้องชำระ (จ่ายแล้ว): {total_monthly_paid:,.2f} บาท\n"
            for c, amt in monthly_paid_cats.items(): reply_msg += f"   • {c}: {amt:,.2f}\n"
                
            if total_monthly_unpaid > 0:
                reply_msg += f"\n⭕ บิลค้างชำระ (ยังไม่จ่าย): {total_monthly_unpaid:,.2f} บาท\n"
                for c, amt in monthly_unpaid_cats.items(): reply_msg += f"   • {c}: {amt:,.2f}\n"
                
            reply_msg += f"\n💰 คงเหลือแต่ละบัญชี (สะสม):\n"
            for acc, amt in balance_accounts.items(): reply_msg += f"   • {acc}: {amt:,.2f}\n"
                
            reply_msg += f"\n💵 ยอดคงเหลือรวมสุทธิ: {total_balance:,.2f} บาท"
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
            return

        # 🔵 5. พิมพ์ตัวเลขเข้ามา (เริ่มต้นจดบัญชีเอง)
        is_number = False
        try:
            float(user_text.replace(',', ''))
            is_number = True
        except ValueError:
            pass

        if is_number:
            amount = user_text.replace(',', '')
            worksheet.append_row([date_str, time_str, "รอระบุประเภท", amount, "รอระบุบัญชี", "รอระบุหมวดหมู่", "-", "-", "รอระบุสถานะบิล", "-"])
            
            quick_reply = QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="รายรับ", text="รายรับ")),
                QuickReplyButton(action=MessageAction(label="รายจ่ายทั่วไป", text="รายจ่าย")),
                QuickReplyButton(action=MessageAction(label="บิลรายเดือน", text="รายจ่ายต้องชำระต่อเดือน")),
                QuickReplyButton(action=MessageAction(label="🔄 ย้ายเงิน", text="ย้ายเงิน"))
            ])
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"ยอดเงิน {user_text} บาท เป็นประเภทไหนครับ? 👇", quick_reply=quick_reply)
            )
            return

        # 🟡 6. จัดการปุ่มกดตาม State
        rows = worksheet.get_all_values()
        last_row_index = len(rows)
        
        if last_row_index > 1:
            last_row = rows[-1]
            
            if len(last_row) > 2 and last_row[2] == "รอระบุประเภท":
                worksheet.update_cell(last_row_index, 3, user_text)
                
                if user_text == "รายจ่ายต้องชำระต่อเดือน":
                    worksheet.update_cell(last_row_index, 5, "-") 
                    items = [
                        QuickReplyButton(action=MessageAction(label="🧡 ShopeePay", text="ShopeePay")),
                        QuickReplyButton(action=MessageAction(label="💸 SEasyCash", text="SEasyCash")),
                        QuickReplyButton(action=MessageAction(label="💳 SPayExtra", text="SPayExtra")),
                        QuickReplyButton(action=MessageAction(label="🌐 Internet", text="Internet")),
                        QuickReplyButton(action=MessageAction(label="🦷 ค่าทำฟัน", text="ค่าทำฟัน")),
                        QuickReplyButton(action=MessageAction(label="🏥 ประกันสังคม", text="ประกันสังคม")),
                        QuickReplyButton(action=MessageAction(label="บิลอื่นๆ", text="บิลอื่นๆ"))
                    ]
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="เลือกหมวดหมู่บิลรายเดือนครับ? 👇", quick_reply=QuickReply(items=items)))
                    return
                elif user_text == "ย้ายเงิน":
                    quick_reply = QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="กสิกร", text="กสิกร")),
                        QuickReplyButton(action=MessageAction(label="กรุงไทย", text="กรุงไทย")),
                        QuickReplyButton(action=MessageAction(label="TrueMoney", text="TrueMoney")),
                        QuickReplyButton(action=MessageAction(label="เงินสด", text="เงินสด"))
                    ])
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="โอนออกจากบัญชีไหนครับ (ต้นทาง)? 👇", quick_reply=quick_reply))
                    return
                else:
                    quick_reply = QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="กสิกร", text="กสิกร")),
                        QuickReplyButton(action=MessageAction(label="กรุงไทย", text="กรุงไทย")),
                        QuickReplyButton(action=MessageAction(label="TrueMoney", text="TrueMoney")),
                        QuickReplyButton(action=MessageAction(label="เงินสด", text="เงินสด"))
                    ])
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ทำรายการผ่านบัญชีไหนครับ? 👇", quick_reply=quick_reply))
                    return
                
            if len(last_row) > 4 and last_row[4] == "รอระบุบัญชี":
                worksheet.update_cell(last_row_index, 5, user_text)
                record_type = last_row[2]
                
                if record_type == "ย้ายเงิน":
                    worksheet.update_cell(last_row_index, 6, "รอระบุบัญชีปลายทาง")
                    quick_reply = QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="กสิกร", text="กสิกร")),
                        QuickReplyButton(action=MessageAction(label="กรุงไทย", text="กรุงไทย")),
                        QuickReplyButton(action=MessageAction(label="TrueMoney", text="TrueMoney")),
                        QuickReplyButton(action=MessageAction(label="เงินสด", text="เงินสด"))
                    ])
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ย้ายเข้าบัญชีไหนครับ (ปลายทาง)? 👇", quick_reply=quick_reply))
                    return
                elif record_type == "รายรับ":
                    items = [
                        QuickReplyButton(action=MessageAction(label="จากพ่อ", text="จากพ่อ")),
                        QuickReplyButton(action=MessageAction(label="จากแม่", text="จากแม่")),
                        QuickReplyButton(action=MessageAction(label="เงินเดือน", text="เงินเดือน")),
                        QuickReplyButton(action=MessageAction(label="อื่นๆ", text="อื่นๆ"))
                    ]
                else:
                    items = [
                        QuickReplyButton(action=MessageAction(label="🍔 อาหาร", text="อาหาร")),
                        QuickReplyButton(action=MessageAction(label="🚗 เดินทาง", text="เดินทาง")),
                        QuickReplyButton(action=MessageAction(label="🚆 BTS", text="BTS")),
                        QuickReplyButton(action=MessageAction(label="🛍️ ช้อปปิ้ง", text="ช้อปปิ้ง")),
                        QuickReplyButton(action=MessageAction(label="ทั่วไป", text="ทั่วไป"))
                    ]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="หมวดหมู่คืออะไรครับ? 👇", quick_reply=QuickReply(items=items)))
                return
                
            if len(last_row) > 5 and last_row[5] == "รอระบุบัญชีปลายทาง":
                worksheet.update_cell(last_row_index, 6, user_text)
                worksheet.update_cell(last_row_index, 9, "-")
                worksheet.update_cell(last_row_index, 10, "-")
                
                try:
                    transfer_amount = float(str(last_row[3]).replace(',', ''))
                except ValueError:
                    transfer_amount = 0.0
                    
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกย้ายเงินจาก {last_row[4]} ➡️ {user_text} จำนวน {transfer_amount:,.2f} บาท เรียบร้อย!"))
                return

            if len(last_row) > 5 and last_row[5] == "รอระบุหมวดหมู่":
                worksheet.update_cell(last_row_index, 6, user_text)
                
                monthly_bill_cats = ["ShopeePay", "SEasyCash", "SPayExtra", "Internet", "ค่าทำฟัน", "ประกันสังคม", "บิลอื่นๆ"]
                is_monthly = False
                if user_text in monthly_bill_cats and last_row[2] == "รายจ่าย":
                    worksheet.update_cell(last_row_index, 3, "รายจ่ายต้องชำระต่อเดือน")
                    worksheet.update_cell(last_row_index, 5, "-")
                    is_monthly = True
                elif last_row[2] == "รายจ่ายต้องชำระต่อเดือน":
                    is_monthly = True
                
                if is_monthly:
                    if len(last_row) > 6 and last_row[6] != "-":
                        worksheet.update_cell(last_row_index, 9, "จ่ายแล้ว")
                        worksheet.update_cell(last_row_index, 10, "-")
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกบิล '{user_text}' (จ่ายแล้ว) เรียบร้อย!"))
                    else:
                        worksheet.update_cell(last_row_index, 9, "รอระบุสถานะบิล")
                        quick_reply = QuickReply(items=[
                            QuickReplyButton(action=MessageAction(label="✅ จ่ายแล้ว", text="จ่ายแล้ว")),
                            QuickReplyButton(action=MessageAction(label="⏳ ยังไม่จ่าย", text="ยังไม่จ่าย"))
                        ])
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="บิลนี้จ่ายหรือยังครับ? 👇", quick_reply=quick_reply))
                else:
                    worksheet.update_cell(last_row_index, 9, "-")
                    worksheet.update_cell(last_row_index, 10, "-")
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ บันทึกรายการลงตารางเรียบร้อยครับ!"))
                return

            if len(last_row) > 8 and last_row[8] == "รอระบุสถานะบิล":
                if user_text == "จ่ายแล้ว":
                    worksheet.update_cell(last_row_index, 9, "จ่ายแล้ว")
                    quick_reply = QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="กสิกร", text="จ่ายบิลผ่าน|กสิกร")),
                        QuickReplyButton(action=MessageAction(label="กรุงไทย", text="จ่ายบิลผ่าน|กรุงไทย")),
                        QuickReplyButton(action=MessageAction(label="TrueMoney", text="จ่ายบิลผ่าน|TrueMoney")),
                        QuickReplyButton(action=MessageAction(label="เงินสด", text="จ่ายบิลผ่าน|เงินสด"))
                    ])
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="จ่ายผ่านบัญชีไหนครับ? 👇", quick_reply=quick_reply))
                else:
                    worksheet.update_cell(last_row_index, 9, "ยังไม่จ่าย")
                    worksheet.update_cell(last_row_index, 10, "-")
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ บันทึกสถานะเป็น 'ยังไม่จ่าย' (บันทึกเป็นบิลค้างชำระเรียบร้อย)"))
                return

            if user_text.startswith("จ่ายบิลผ่าน|"):
                account_name = user_text.split("|")[1]
                worksheet.update_cell(last_row_index, 10, account_name)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ บันทึกบิลเรียบร้อย! (หักเงินจากบัญชี {account_name} แล้ว)"))
                return

        cheeky_replies = [
            "แหมมม ทักมาซะตกใจ นึกว่าจะโอนเงินให้! 💸 ถ้าจะจดบัญชี พิมพ์ตัวเลขมาได้เลยจ้า",
            "จ้าาา รับทราบจ้า! แต่ถ้าจะให้จดบัญชี รบกวนพิมพ์เป็นตัวเลขนะจ๊ะตัวเอง 😆",
            "ทักมาทำไม เหงาอ่อ? 😝 บอทคุยไม่เก่งนะ บอทเก่งแต่เรื่องทวงบิล! (พิมพ์ bill เพื่อดูบิลค้างได้นะ)",
            "จร้าจ่าจ้ะ! 🤣 ถ้าไม่ได้มาจดบัญชี บอทขออนุญาตไปนอนพักสายตาก่อนนะ...",
            "พิมพ์มาแบบนี้ บอทงงเด้อ! 🤪 ถ้าจะจดรายจ่าย พิมพ์ตัวเลขมาโลดดด",
            "ฮั่นแน่! แอบอู้ไม่ยอมทำงานมาแชทเล่นกับบอทใช่มั้ย! 🤨 รีบไปหาเงินมาให้บอทจดเดี๋ยวนี้!"
        ]
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=random.choice(cheeky_replies))
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
                
                now = datetime.now(TH_TZ)
                date_str = now.strftime("%d/%m/%Y")
                time_str = now.strftime("%H:%M:%S")
                
                worksheet = get_current_worksheet()
                worksheet.append_row([date_str, time_str, "รายจ่าย", amount, "รอระบุบัญชี", "รอระบุหมวดหมู่", sender, receiver, "-", "-"])
                
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
