"""Checks for linking a repayment to the loan it pays off."""
from decimal import Decimal, InvalidOperation

LEND = "ให้ยืมเงิน"
REPAY = "ได้คืนจากลูกหนี้"


def _money(value):
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        raise ValueError("จำนวนเงินไม่ถูกต้อง")
    if not number.is_finite() or number <= 0:
        raise ValueError("จำนวนเงินต้องมากกว่า 0")
    return number


def check_repay_link(db, user_id, repays_id, person, amount):
    """Return the loan id a repayment may be linked to, or raise ValueError.

    The loan must belong to this user and person, and the repayment may not be
    more than what is still owed on that loan from earlier linked repayments.
    """
    try:
        loan_id = int(repays_id)
    except (TypeError, ValueError):
        raise ValueError("รายการยืมที่เลือกไม่ถูกต้อง")
    loans = (db.table("transactions").select("id,type,category,amount")
             .eq("id", loan_id).eq("user_id", user_id).limit(1).execute().data or [])
    if not loans or loans[0].get("type") != LEND:
        raise ValueError("ไม่พบรายการยืมที่เลือก")
    if str(loans[0].get("category") or "").strip() != str(person or "").strip():
        raise ValueError("รายการยืมที่เลือกเป็นของคนอื่น")
    linked = (db.table("transactions").select("amount")
              .eq("user_id", user_id).eq("type", REPAY).eq("repays_id", loan_id).execute().data or [])
    remaining = abs(_money(loans[0]["amount"])) - sum(abs(_money(row["amount"])) for row in linked)
    if _money(amount) > remaining:
        raise ValueError(f"ยอดคืนเกินยอดที่ค้างของรายการนี้ ({remaining:,.2f} บาท)")
    return loan_id


MAX_LOANS_PER_REPAYMENT = 50


def split_repayment(db, user_id, repays, person, amount):
    """Validate a repayment spread over several loans.

    repays is a list of {"id": loan id, "amount": part}. The parts must add up to
    the whole amount, each loan may appear once, and each part must fit what is
    still owed on its loan. Returns [(loan_id, Decimal part)].
    """
    if not isinstance(repays, list) or not repays or len(repays) > MAX_LOANS_PER_REPAYMENT:
        raise ValueError("กรุณาเลือกรายการยืมที่คืน")
    parts = []
    seen = set()
    for item in repays:
        if not isinstance(item, dict):
            raise ValueError("รายการยืมที่เลือกไม่ถูกต้อง")
        part = _money(item.get("amount"))
        loan_id = check_repay_link(db, user_id, item.get("id"), person, part)
        if loan_id in seen:
            raise ValueError("เลือกรายการยืมซ้ำ")
        seen.add(loan_id)
        parts.append((loan_id, part))
    if sum(part for _, part in parts).quantize(Decimal("0.01")) != _money(amount).quantize(Decimal("0.01")):
        raise ValueError("ยอดที่แบ่งให้แต่ละรายการไม่เท่ากับยอดคืน")
    return parts
