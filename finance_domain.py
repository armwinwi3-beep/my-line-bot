"""Validation shared by the finance routes; no database or network side effects."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

TYPES = {'รายรับ', 'รายจ่าย', 'รายจ่ายต้องชำระต่อเดือน', 'ย้ายเงิน', 'ให้ยืมเงิน', 'ได้คืนจากลูกหนี้'}


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal('999999999999.99'):
            raise ValueError()
        if amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
        return float(amount)
    except (InvalidOperation, ValueError):
        raise ValueError('จำนวนเงินต้องมากกว่า 0 และมีทศนิยมไม่เกิน 2 ตำแหน่ง')


def text(value, label, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f'กรุณาระบุ{label} (ไม่เกิน {maximum} ตัวอักษร)')
    return value.strip()


def iso_date(value):
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError('วันที่ไม่ถูกต้อง')


def transaction(data):
    kind = data.get('type')
    if kind not in TYPES:
        raise ValueError('ประเภทรายการไม่ถูกต้อง')
    status = data.get('status', 'จ่ายแล้ว') if kind == 'รายจ่ายต้องชำระต่อเดือน' else 'จ่ายแล้ว'
    if status not in {'จ่ายแล้ว', 'ยังไม่จ่าย'}:
        raise ValueError('สถานะรายการไม่ถูกต้อง')
    account = text(data.get('account'), 'บัญชี')
    category = text(data.get('category'), 'หมวดหมู่หรือปลายทาง')
    if kind == 'ย้ายเงิน' and account == category:
        raise ValueError('บัญชีต้นทางและปลายทางต้องต่างกัน')
    if account == '-' and not (kind == 'รายจ่ายต้องชำระต่อเดือน' and status == 'ยังไม่จ่าย'):
        raise ValueError('กรุณาเลือกบัญชี')
    note = data.get('note') or '-'
    if not isinstance(note, str) or len(note) > 1000:
        raise ValueError('โน้ตต้องไม่เกิน 1,000 ตัวอักษร')
    return dict(type=kind, amount=money(data.get('amount')), category=category, account=account,
                date=iso_date(data.get('date')).strftime('%d/%m/%Y'), note=note, status=status)


def budget(data):
    month = iso_date(str(data.get('month', '')) + '-01')
    return dict(month=month.isoformat(), category=text(data.get('category'), 'หมวดหมู่'), amount=money(data.get('amount')))


def recurring(data):
    kind = data.get('type')
    if kind not in {'รายรับ', 'รายจ่ายต้องชำระต่อเดือน'}:
        raise ValueError('รายการประจำต้องเป็นรายรับหรือบิล')
    day = data.get('day_of_month')
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise ValueError('วันที่ประจำต้องอยู่ระหว่าง 1 ถึง 31')
    start = iso_date(data.get('start_date'))
    return dict(type=kind, category=text(data.get('category'), 'ชื่อรายการ'),
                account=text(data.get('account'), 'บัญชี'), amount=money(data.get('amount')),
                day_of_month=day, start_date=start.isoformat(), note=text(data.get('note') or '-', 'โน้ต', 1000))


def record_date(row):
    try:
        return datetime.strptime(row.get('date', ''), '%d/%m/%Y').date()
    except (ValueError, TypeError):
        return date.min


def budget_usage(budgets, rows):
    result = []
    for item in budgets:
        spent = sum(Decimal(str(row.get('amount', 0))) for row in rows
                    if record_date(row).strftime('%Y-%m') == item['month'][:7]
                    and row.get('category') == item['category']
                    and (row.get('type') == 'รายจ่าย' or (row.get('type') == 'รายจ่ายต้องชำระต่อเดือน' and row.get('status') == 'จ่ายแล้ว')))
        limit = Decimal(str(item['amount']))
        result.append({**item, 'spent': float(spent), 'remaining': float(limit - spent),
                       'percent': round(float(spent / limit * 100), 1) if limit > 0 else 0})
    return result
