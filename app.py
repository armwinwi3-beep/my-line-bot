# 🌟 ปรับปรุง API เพื่อส่งข้อมูลเวลา (time) ไปแสดงผลที่หน้าเว็บด้วย
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
                        time_val = row[1] if len(row) > 1 and row[1].strip() != "" else "-" # 🌟 ดึงค่าเวลา
                        account = row[4] if len(row) > 4 and row[4].strip() != "" else "-"
                        cat = row[5] if len(row) > 5 and row[5].strip() != "" else "-"
                        note = row[7] if len(row) > 7 else "-"
                        status = row[8] if len(row) > 8 and str(row[8]).strip() != "" else "จ่ายแล้ว"
                        paid_account = row[9] if len(row) > 9 and row[9].strip() != "" and row[9] != "-" else account

                        # คำนวณสะสมรวมทุกเดือน
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
                            
                        # ดึงเฉพาะรายการของเดือนปัจจุบัน
                        if is_target_month:
                            if record_type == "รายจ่าย" or (record_type == "รายจ่ายต้องชำระต่อเดือน" and status == "จ่ายแล้ว"):
                                total_expense += amt
                            elif record_type == "รายรับ":
                                total_income += amt

                            records.append({
                                "date": date_val,
                                "time": time_val, # 🌟 ส่งเวลาแนบไปให้หน้าเว็บ
                                "type": record_type,
                                "amount": amt,
                                "category": cat,
                                "account": account,
                                "note": note,
                                "status": status,
                                "sheet": ws.title
                            })
                        else:
                            # ดึงบิลค้างชำระเดือนเก่ามาแสดงด้วย
                            if record_type == "รายจ่ายต้องชำระต่อเดือน" and status == "ยังไม่จ่าย":
                                old_note = note if note != "-" else ""
                                unpaid_records.append({
                                    "date": date_val,
                                    "time": time_val, # 🌟 ส่งเวลาแนบไปให้หน้าเว็บ
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
            "records": records
        })
    except Exception as e:
        print("API Data Error:", str(e))
        return jsonify({"status": "error", "message": str(e)}), 500
