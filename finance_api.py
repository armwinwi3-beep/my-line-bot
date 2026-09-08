"""Finance routes use the same per-user ownership filter as transactions.

The new private tables require SUPABASE_SERVICE_ROLE_KEY on the server.
LINE profiles are verified before accepting the user identity for these routes.
"""
import hmac
import os
from uuid import UUID
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
import requests
from finance_domain import budget, budget_usage, recurring, transaction, record_date


def register_finance(app, existing_client, client_factory, supabase_url):
    api = Blueprint('finance', __name__, url_prefix='/api/finance')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    private_client = client_factory(supabase_url, key) if key else None

    def identity():
        user = request.args.get('user_id') if request.method == 'GET' else (request.get_json(silent=True) or {}).get('user_id')
        if not isinstance(user, str) or not user:
            raise PermissionError('กรุณาเข้าสู่ระบบก่อน')
        if user == 'admin':
            pin = os.environ.get('ADMIN_PIN', '')
            if not pin or not hmac.compare_digest(request.headers.get('X-Admin-Pin', ''), pin):
                raise PermissionError('กรุณาตั้งค่า ADMIN_PIN ฝั่งเซิร์ฟเวอร์และเข้าสู่ระบบอีกครั้ง')
        else:
            token = request.headers.get('Authorization', '')
            if not token.startswith('Bearer '):
                raise PermissionError('กรุณาเปิดเว็บผ่าน LINE อีกครั้ง')
            response = requests.get('https://api.line.me/v2/profile', headers={'Authorization': token}, timeout=10)
            if response.status_code != 200 or response.json().get('userId') != user:
                raise PermissionError('บัญชี LINE ไม่ตรงกับผู้ใช้')
        return user

    def db():
        if private_client is None:
            raise RuntimeError('FINANCE_SETUP_REQUIRED')
        return private_client

    def rows(user):
        output, offset = [], 0
        while True:
            batch = existing_client.table('transactions').select('*').eq('user_id', user).order('id').range(offset, offset + 499).execute().data or []
            output.extend(batch)
            if len(batch) < 500:
                return output
            offset += 500

    @api.errorhandler(PermissionError)
    def unauthorized(error):
        return jsonify(message=str(error)), 401

    @api.errorhandler(ValueError)
    def invalid(error):
        return jsonify(message=str(error)), 400

    @api.errorhandler(Exception)
    def unavailable(error):
        app.logger.warning('Finance request failed: %s', type(error).__name__)
        return jsonify(message='ระบบส่วนนี้ยังไม่พร้อม กรุณาลองใหม่หรือติดต่อผู้ดูแล', code='FINANCE_UNAVAILABLE'), 503

    @api.get('/records')
    def history():
        user = identity()
        records = sorted(rows(user), key=lambda row: (record_date(row), row.get('time') or '', str(row['id'])), reverse=True)
        return jsonify(records=records)

    @api.post('/record')
    def edit_record():
        user = identity()
        data = request.get_json()
        updates = transaction(data)
        record_id = data.get('id')
        if not record_id:
            raise ValueError('ไม่พบรหัสรายการ')
        owned = existing_client.table('transactions').select('id').eq('id', record_id).eq('user_id', user).execute().data
        if not owned:
            return jsonify(message='ไม่พบรายการของบัญชีนี้'), 404
        existing_client.table('transactions').update(updates).eq('id', record_id).eq('user_id', user).execute()
        return jsonify(status='success')

    @api.get('/plan')
    def plan():
        user = identity()
        month = request.args.get('month', '')
        from finance_domain import iso_date
        month_date = iso_date(month + '-01').isoformat()
        # Idempotent catch-up is also performed by the database scheduler.
        generated = db().rpc('finance_generate_due', {'p_user_id': user}).execute().data or 0
        budgets = db().table('monthly_budgets').select('*').eq('user_id', user).eq('month', month_date).execute().data or []
        rules = db().table('recurring_rules').select('*').eq('user_id', user).order('created_at').execute().data or []
        all_rows = rows(user)
        today = datetime.now(timezone(timedelta(hours=7))).date()
        due = sorted([row for row in all_rows if row.get('type') == 'รายจ่ายต้องชำระต่อเดือน' and row.get('status') == 'ยังไม่จ่าย' and record_date(row) <= today], key=record_date, reverse=True)
        return jsonify(budgets=budget_usage(budgets, all_rows), recurring=rules, due=due, generated=generated)

    @api.post('/budget')
    def save_budget():
        user = identity()
        values = budget(request.get_json())
        db().table('monthly_budgets').upsert({**values, 'user_id': user}, on_conflict='user_id,month,category').execute()
        return jsonify(status='success')

    @api.post('/budget/delete')
    def remove_budget():
        user = identity()
        record_id = request.get_json().get('id')
        if not record_id:
            raise ValueError('ไม่พบรหัสงบ')
        db().table('monthly_budgets').delete().eq('id', record_id).eq('user_id', user).execute()
        return jsonify(status='success')

    @api.post('/recurring')
    def save_rule():
        user = identity()
        data = request.get_json()
        values = recurring(data)
        # New schedules never backfill transactions before creation.
        today = datetime.now(timezone(timedelta(hours=7))).date()
        if values['start_date'] < today.isoformat():
            raise ValueError('วันเริ่มรายการประจำต้องเป็นวันนี้หรือวันถัดไป')
        try:
            rule_id = str(UUID(str(data.get('id', ''))))
        except ValueError:
            raise ValueError('รหัสรายการประจำไม่ถูกต้อง กรุณาเปิดฟอร์มใหม่')
        db().table('recurring_rules').upsert({**values, 'id': rule_id, 'user_id': user}, on_conflict='id', ignore_duplicates=True).execute()
        db().rpc('finance_generate_due', {'p_user_id': user}).execute()
        return jsonify(status='success')

    @api.post('/recurring/toggle')
    def toggle_rule():
        user = identity()
        data = request.get_json()
        if not data.get('id') or not isinstance(data.get('active'), bool):
            raise ValueError('สถานะรายการประจำไม่ถูกต้อง')
        # Resume from today, without generating the paused months.
        values = {'active': data['active']}
        if data['active']:
            values['start_date'] = datetime.now(timezone(timedelta(hours=7))).date().isoformat()
        db().table('recurring_rules').update(values).eq('id', data['id']).eq('user_id', user).execute()
        return jsonify(status='success')

    app.register_blueprint(api)
