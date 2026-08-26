import os
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
from flask_cors import CORS
from supabase import create_client, Client

app = Flask(__name__)
CORS(app)

# 🔑 ตั้งค่าการเชื่อมต่อ Supabase (แทน Google Sheets)
# อย่าลืมเปลี่ยน URL และ Key ให้ตรงกับโปรเจกต์ของคุณใน Supabase
SUPABASE_URL = "https://tmwnszhxikgjelpskqj.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRtd25zemh4YmlrZ2plbHBza3FqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODc3NTM1ODksImV4cCI6MjEwMzMyOTU4OX0.d2w1T00nHf32Ni_wrg_Q7z-zHgwIPlyfdm9gbjlBNZs"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TH_TZ = timezone(timedelta(hours=7))

@app.route("/")
def home():
    return "Bot is awake and connected to Supabase!", 200

# 📊 API ดึงข้อมูลรายรับ-รายจ่าย (แยกตาม user_id และเดือน)
@app.route("/api/data", methods=["GET"])
def api_data():
    month_str = request.args.get('month') # รูปแบบเช่น "08/26"
    user_id = request.args.get('user_id', 'my_account') # รองรับหลาย User (ค่าเริ่มต้นคือ my_account)
    
    try:
        # ดึงข้อมูลจากตาราง transactions ใน Supabase
        response = supabase.table("transactions").select("*").eq("user_id", user_id).execute()
        rows = response.data or []
        
        income_accounts_all = {}
        expense_accounts_all = {}
        monthly_accounts_all = {}
        transfer_in_all = {}
        transfer_out_all = {}
        debtors_all = {}
        
        total_expense = 0.0
        total_income = 0.0
        records = []
        unpaid_records = []

        for row in rows:
            try:
                amt = float(str(row.get('amount', 0)).replace(',', ''))
                record_type = row.get('type')
                date_val = row.get('date') # เช่น "28/08/2026"
                time_val = row.get('time') if row.get('time') else "-"
                account = row.get('account') if row.get('account') else "-"
                cat = row.get('category') if row.get('category') else "-"
                note = row.get('note') if row.get('note') else "-"
                status = row.get('status') if row.get('status') else "จ่ายแล้ว"
                
                # เช็คว่าอยู่ในเดือนที่กำลังเลือกดูอยู่หรือไม่ (จากวันที่ DD/MM/YYYY)
                is_target_month = False
                if date_val:
                    parts = date_val.split('/')
                    if len(parts) >= 3:
                        row_m_y = f"{parts[1]}/{parts[2][-2:]}"
                        if row_m_y == month_str:
                            is_target_month = True

                # คำนวณยอดสะสมแต่ละบัญชี
                if record_type == "รายรับ":
                    income_accounts_all[account] = income_accounts_all.get(account, 0) + amt
                elif record_type == "รายจ่าย":
                    expense_accounts_all[account] = expense_accounts_all.get(account, 0) + amt
                elif record_type == "รายจ่ายต้องชำระต่อเดือน":
                    if status != "ยังไม่จ่าย" and status != "บิลค้างชำระ (เคลียร์แล้ว)":
                        if account != "-":
                            monthly_accounts_all[account] = monthly_accounts_all.get(account, 0) + amt
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
                        "id": row.get('id'),
                        "date": date_val,
                        "time": time_val,
                        "type": record_type,
                        "amount": amt,
                        "category": cat,
                        "account": account,
                        "note": note,
                        "status": status
                    })
                else:
                    # ดึงบิลค้างชำระข้ามเดือน
                    if record_type == "รายจ่ายต้องชำระต่อเดือน" and status == "ยังไม่จ่าย":
                        unpaid_records.append({
                            "id": row.get('id'),
                            "date": date_val,
                            "time": time_val,
                            "type": "รายจ่ายต้องชำระต่อเดือน",
                            "amount": amt,
                            "category": cat,
                            "account": account,
                            "note": f"ยกยอดมา " + (note if note != "-" else ""),
                            "status": "ยังไม่จ่าย"
                        })
            except Exception as ex:
                print("Row Parse Error:", ex)
                pass
        
        records.extend(unpaid_records)
        
        # คำนวณยอดเงินคงเหลือสุทธิแต่ละบัญชี
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
            "debtors": debtors_all, 
            "records": records
        })
    except Exception as e:
        print("API Data Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

# 📝 API เพิ่มข้อมูลลง Supabase
@app.route("/api/add", methods=["POST"])
def api_add():
    data = request.json
    user_id = data.get('user_id', 'my_account')
    
    now = datetime.now(TH_TZ)
    date_str = now.strftime("%d/%m/%Y")
    time_str = now.strftime("%H:%M:%S")
    
    record_type = data.get('type')
    amount = data.get('amount')
    note = data.get('note', '-')
    status = data.get('status', 'จ่ายแล้ว')
    
    try:
        if record_type == 'transfer':
            source_acc = data.get('sourceAccount')
            dest_acc = data.get('destinationAccount')
            
            # บันทึกฝั่งเงินออก
            supabase.table("transactions").insert({
                "user_id": user_id, "date": date_str, "time": time_str,
                "type": "ย้ายเงิน", "amount": amount, "category": dest_acc,
                "account": source_acc, "note": note, "status": "จ่ายแล้ว"
            }).execute()
        elif record_type in ['lend', 'repay']:
            # จัดการเรื่องคนยืมเงิน
            t_type = "ให้ยืมเงิน" if record_type == 'lend' else "ได้คืนจากลูกหนี้"
            supabase.table("transactions").insert({
                "user_id": user_id, "date": date_str, "time": time_str,
                "type": t_type, "amount": amount, "category": data.get('category'),
                "account": data.get('account'), "note": note, "status": "-"
            }).execute()
        else:
            supabase.table("transactions").insert({
                "user_id": user_id, "date": date_str, "time": time_str,
                "type": "รายรับ" if record_type == 'income' else ("รายจ่ายต้องชำระต่อเดือน" if record_type == 'bill' else "รายจ่าย"),
                "amount": amount, "category": data.get('category'),
                "account": data.get('account', '-'), "note": note, "status": status
            }).execute()
            
        return jsonify({"status": "success"})
    except Exception as e:
        print("API Add Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

# 🗑️ API ลบข้อมูล
@app.route("/api/delete", methods=["POST"])
def api_delete():
    data = request.json
    record_id = data.get('id') # ใช้ ID ในการลบ แม่นยำที่สุด
    user_id = data.get('user_id', 'my_account')
    
    try:
        if record_id:
            supabase.table("transactions").delete().eq("id", record_id).eq("user_id", user_id).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        print("API Delete Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)