import os
import requests
import gspread
import random
from datetime import datetime, timezone, timedelta
from flask import Flask, request, abort, render_template, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    QuickReply, QuickReplyButton, MessageAction
)

app = Flask(__name__)

last_checked_month = None

@app.route("/")
def home():
    global last_checked_month
    now = datetime.now(TH_TZ)
    current_m = now.strftime("%m/%y")

    if now.day >= 25 and last_checked_month != current_m:
        try:
            sh = gc.open('MoneyBase')
            next_month_date = now.replace(day=28) + timedelta(days=5)
            next_month_str = next_month_date.strftime("%m/%y")
            
            try:
                sh.worksheet(next_month_str)
            except gspread.exceptions.WorksheetNotFound:
                try:
                    curr_ws = sh.worksheet(current_m)
                    new_ws = curr_ws.duplicate(new_sheet_name=next_month_str)
                    new_ws.batch_clear(["A2:J1000"]) 
                except Exception:
                    pass 
            last_checked_month = current_m
        except Exception as e:
            print("Error preparing next month:", e)

    return "Bot is awake and running!", 200

@app.route("/app")
def mini_app():
    return render_template("index.html")
    
@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.json
    record_type = data.get('type')
    amount = data.get('amount')
    category = data.get('category')
    note = data.get('note', '-')
    status_val = data.get('status', '-') 
    account = data.get('account', '-') 
    source_acc = data.get('sourceAccount') 
    dest_acc = data.get('destinationAccount') 
    
    if record_type == 'expense': 
        type_th = "รายจ่าย"
        status_val = "-"
    elif record_type == 'income': 
        type_th = "รายรับ"
        status_val = "-"
    elif record_type == 'transfer': 
        type_th = "ย้ายเงิน"
        status_val = "-"
        account = source_acc  
        category = dest_acc   
    elif record_type == 'bill': 
        type_th = "รายจ่ายต้องชำระต่อเดือน"
        if status_val == "ยังไม่จ่าย":
            account = "-" 
    elif record_type == 'lend': 
        type_th = "ให้ยืมเงิน"
        status_val = "-"
    elif record_type == 'repay': 
        type_th = "ได้คืนจากลูกหนี้"
        status_val = "-"
    else: 
        type_th = "ไม่ระบุ"
        
    if not category: category = "-"

    try:
        worksheet = get_current_worksheet()
        now = datetime.now(TH_TZ)
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M:%S")

        if type_th == "รายจ่ายต้องชำระต่อเดือน" and status_val == "จ่ายแล้ว":
            sh = gc.open('MoneyBase')
            for ws in sh.worksheets():
                rows = ws.get_all_values()
                for i in range(len(rows)-1, 0, -1):
                    row = rows[i]
                    if len(row) >= 9:
                        if row[2] == "รายจ่ายต้องชำระต่อเดือน" and row[5] == category and row[8] == "ยังไม่จ่าย":
                            ws.update_cell(i+1, 3, "บิลค้างชำระ (เคลียร์แล้ว)")
                            ws.update_cell(i+1, 9, "เคลียร์บิลแล้ว")
                            break

        col_values = worksheet.col_values(1)
        next_row = len(col_values) + 1
        
        row_data = [date_str, time_str, type_th, amount, account, category, "-", note, status_val, "-"]
        worksheet.insert_row(row_data, index=next_row)
        
        return jsonify({"status": "success", "message": "บันทึกเรียบร้อย"})
    except Exception as e:
        print("API Add Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json
    month = data.get('month') 
    date_val = data.get('date')
    amount = str(data.get('amount'))
    category = data.get('category')
    
    try:
        sh = gc.open('MoneyBase')
        worksheets_to_check = [month] if month else [ws.title for ws in sh.worksheets()]
        
        deleted = False
        for ws_name in worksheets_to_check:
            try:
                ws = sh.worksheet(ws_name)
                rows = ws.get_all_values()
                for i in range(len(rows)-1, 0, -1):
                    row = rows[i]
                    if len(row) >= 6:
                        if row[0] == date_val and str(row[3]).replace(',', '') == amount and row[5] == category:
                            ws.delete_rows(i + 1)
                            deleted = True
                            break
            except Exception:
                continue
            if deleted: break
            
        if deleted:
            return jsonify({"status": "success", "message": "ลบรายการสำเร็จ"})
        else:
            return jsonify({"status": "error", "message": "ไม่พบรายการที่ต้องการลบ"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/data", methods=["GET"])
def api_data():
    month_str = request.args.get('month') 
    try:
        sh = gc.open('MoneyBase')
        
        income_accounts_all = {}
        expense_accounts_all = {}
        monthly_accounts_all = {}
        transfer_in_all = {}
        transfer_out_all = {}
        debtors_all = {} # เก็บข้อมูลลูกหนี้
        
        total_expense = 0.0
        total_income = 0.0
        records = []
        unpaid_records = []

        for ws in sh.worksheets():
            try:
                rows = ws.get_all_values()
            except Exception:
                continue
                
            is_target_month = (ws.title == month_str)
            
            for row in rows[1:]:
                if len(row) >= 6:
                    try:
                        amt = float(str(row[3]).replace(',', ''))
                        record_type = row[2]
                        date_val = row[0]
                        time_val = row[1] if len(row) > 1 and row[1].strip() != "" else "-"
                        account = row[4] if len(row) > 4 and row[4].strip() != "" else "-"
                        cat = row[5] if len(row) > 5 and row[5].strip() != "" else "-"
                        note = row[7] if len(row) > 7 else "-"
                        status = row[8] if len(row) > 8 and str(row[8]).strip() != "" else "จ่ายแล้ว"
                        paid_account = row[9] if len(row) > 9 and row[9].strip() != "" and row[9] != "-" else account

                        if record_type == "รายรับ":
                            income_accounts_all[account] = income_accounts_all.get(account, 0) + amt
                        elif record_type == "รายจ่าย":
                            expense_accounts_all[account] = expense_accounts_all.get(account, 0) + amt
                        elif record_type == "รายจ่ายต้องชำระต่อเดือน":
                            if status != "ยังไม่จ่าย" and status != "บิลค้างชำระ (เคลียร์แล้ว)":
                                acc_to_deduct = paid_account if paid_account != "-" else account
                                if acc_to_deduct != "-":
                                    monthly_accounts_all[acc_to_deduct] = monthly_accounts_all.get(acc_to_deduct, 0) + amt
                        elif record_type == "ย้ายเงิน":
                            src_acc = account
                            dst_acc = cat
                            if src_acc != "-": transfer_out_all[src_acc] = transfer_out_all.get(src_acc, 0) + amt
                            if dst_acc != "-": transfer_in_all[dst_acc] = transfer_in_all.get(dst_acc, 0) + amt
                        elif record_type == "ให้ยืมเงิน":
                            expense_accounts_all[account] = expense_accounts_all.get(account, 0) + amt
                            debtors_all[cat] = debtors_all.get(cat, 0) + amt
                        elif record_type == "ได้คืนจากลูกหนี้":
                            income_accounts_all[account] = income_accounts_all.get(account, 0) + amt
                            debtors_all[cat] = debtors_all.get(cat, 0) - amt
                            
                        if is_target_month:
                            if record_type in ["รายจ่าย", "ให้ยืมเงิน"] or (record_type == "รายจ่ายต้องชำระต่อเดือน" and status == "จ่ายแล้ว"):
                                total_expense += amt
                            elif record_type in ["รายรับ", "ได้คืนจากลูกหนี้"]:
                                total_income += amt

                            records.append({
                                "date": date_val,
                                "time": time_val,
                                "type": record_type,
                                "amount": amt,
                                "category": cat,
                                "account": account,
                                "note": note,
                                "status": status,
                                "sheet": ws.title
                            })
                        else:
                            if record_type == "รายจ่ายต้องชำระต่อเดือน" and status == "ยังไม่จ่าย":
                                old_note = note if note != "-" else ""
                                unpaid_records.append({
                                    "date": date_val,
                                    "time": time_val,
                                    "type": "รายจ่ายต้องชำระต่อเดือน",
                                    "amount": amt,
                                    "category": cat,
                                    "account": account,
                                    "note": f"ยกยอดจากเดือน {ws.title} " + old_note,
                                    "status": "ยังไม่จ่าย",
                                    "sheet": ws.title
                                })
                    except ValueError:
                        pass
        
        records.extend(unpaid_records)
        
        all_accounts = set(income_accounts_all.keys()) | set(expense_accounts_all.keys()) | set(monthly_accounts_all.keys()) | set(transfer_in_all.keys()) | set(transfer_out_all.keys())
        balance_accounts = {}
        total_balance = 0.0
        
        for acc in all_accounts:
            if acc == "-" or acc == "รอระบุบัญชี": continue
            bal = (income_accounts_all.get(acc, 0.0) 
                   - expense_accounts_all.get(acc, 0.0) 
                   - monthly_accounts_all.get(acc, 0.0) 
                   + transfer_in_all.get(acc, 0.0) 
                   - transfer_out_all.get(acc, 0.0))
            balance_accounts[acc] = bal
            total_balance += bal

        return jsonify({
            "total_expense": total_expense, 
            "total_income": total_income, 
            "total_balance": total_balance,
            "account_balances": balance_accounts,
            "debtors": debtors_all, # ส่งข้อมูลลูกหนี้
            "records": records
        })
    except Exception as e:
        print("API Data Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500


line_bot_api = LineBotApi('ETXUTTB9PqZ1QymR0zSM4c+/7ecw+x0BIoB3jc6YB4fm20Hy7OxSV/C4jR7SDAE9hyEx/UBwoc9H7go6147rW9glQMGZO/n3XZ/lf6+Dp7vrTVP01NMzjTqEKYMCY/AfmI/ZSIi5hRDjxjufoO6sdQdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('1716fc54190bf6b7177ba7d80d3b07af')
gc = gspread.service_account(filename='cedar-abacus-503815-i0-be6261b65bfd.json')
TH_TZ = timezone(timedelta(hours=7))

def get_current_worksheet():
    sh = gc.open('MoneyBase')
    now = datetime.now(TH_TZ)
    sheet_name = now.strftime("%m/%y")
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        first_day_this_month = now.replace(day=1)
        last_month_date = first_day_this_month - timedelta(days=1)
        last_month_str = last_month_date.strftime("%m/%y")
        try:
            prev_ws = sh.worksheet(last_month_str)
            worksheet = prev_ws.duplicate(new_sheet_name=sheet_name)
            worksheet.batch_clear(["A2:J1000"])
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
        # ฟังก์ชันแชทเดิมทั้งหมด...
        pass
    except Exception as e:
        line_bot_api.reply_token(event.reply_token, TextSendMessage(text=f"❌ เกิดข้อผิดพลาด: {str(e)}"))

if __name__ == "__main__":
    app.run(port=8080)
