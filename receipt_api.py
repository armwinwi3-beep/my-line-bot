"""Private image attachments for expense records."""
import hmac
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from urllib.parse import urljoin

import requests
from flask import Blueprint, jsonify, request


BUCKET = "expense-receipts"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_SIGNATURES = {
    "image/jpeg": (".jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": (".png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (".webp", lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
}


def register_receipts(app, client_factory, supabase_url):
    api = Blueprint("receipts", __name__, url_prefix="/api/receipts")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    private_client = client_factory(supabase_url, key) if key else None

    def db():
        if private_client is None:
            raise RuntimeError("RECEIPT_SETUP_REQUIRED")
        return private_client

    def identity():
        user = request.form.get("user_id") if request.method == "POST" else request.args.get("user_id")
        if not isinstance(user, str) or not user:
            raise PermissionError("กรุณาเข้าสู่ระบบก่อน")
        if user == "admin":
            pin = os.environ.get("ADMIN_PIN", "")
            if not pin or not hmac.compare_digest(request.headers.get("X-Admin-Pin", ""), pin):
                raise PermissionError("กรุณาเข้าสู่ระบบแอดมินอีกครั้ง")
        else:
            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                raise PermissionError("กรุณาเปิดเว็บผ่าน LINE อีกครั้ง")
            response = requests.get(
                "https://api.line.me/v2/profile",
                headers={"Authorization": token},
                timeout=10,
            )
            if response.status_code != 200 or response.json().get("userId") != user:
                raise PermissionError("บัญชี LINE ไม่ตรงกับผู้ใช้")
        return user

    @api.errorhandler(PermissionError)
    def unauthorized(error):
        return jsonify(status="error", message=str(error)), 401

    @api.errorhandler(ValueError)
    def invalid(error):
        return jsonify(status="error", message=str(error)), 400

    @api.errorhandler(Exception)
    def unavailable(error):
        app.logger.warning("Receipt request failed: %s", type(error).__name__)
        return jsonify(status="error", message="ระบบรูปหลักฐานยังไม่พร้อม กรุณาลองใหม่"), 503

    @api.post("/expense")
    def save_expense():
        user = identity()
        try:
            amount = Decimal(str(request.form.get("amount", "")))
        except InvalidOperation:
            raise ValueError("จำนวนเงินไม่ถูกต้อง")
        category = (request.form.get("category") or "").strip()
        account = (request.form.get("account") or "").strip()
        note = (request.form.get("note") or "-").strip() or "-"
        if not amount.is_finite() or amount <= 0 or not category or not account:
            raise ValueError("กรุณาระบุจำนวนเงิน หมวดหมู่ และบัญชี")
        if len(category) > 120 or len(account) > 120 or len(note) > 1000:
            raise ValueError("ข้อมูลรายการยาวเกินกำหนด")

        image = request.files.get("receipt")
        if image is None or image.mimetype not in IMAGE_SIGNATURES:
            raise ValueError("กรุณาเลือกรูป JPG, PNG หรือ WebP")
        image_bytes = image.read(MAX_IMAGE_BYTES + 1)
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ValueError("รูปหลักฐานต้องมีขนาดไม่เกิน 5 MB")
        extension, valid_signature = IMAGE_SIGNATURES[image.mimetype]
        if not valid_signature(image_bytes):
            raise ValueError("ไฟล์รูปไม่ถูกต้อง")

        path = f"{uuid4().hex}{extension}"
        storage = db().storage.from_(BUCKET)
        storage.upload(path=path, file=image_bytes, file_options={"content-type": image.mimetype, "upsert": "false"})
        record_id = None
        try:
            now = datetime.now(timezone(timedelta(hours=7)))
            inserted = db().table("transactions").insert({
                "user_id": user, "date": now.strftime("%d/%m/%Y"),
                "time": now.strftime("%H:%M:%S"), "type": "รายจ่าย",
                "amount": str(amount), "category": category, "account": account,
                "note": note, "status": "จ่ายแล้ว",
            }).execute().data or []
            if not inserted:
                raise RuntimeError("Expense insert returned no row")
            record_id = inserted[0]["id"]
            db().table("expense_receipts").insert({
                "record_id": record_id, "user_id": user, "object_path": path
            }).execute()
        except Exception:
            if record_id is not None:
                db().table("transactions").delete().eq("id", record_id).eq("user_id", user).execute()
            storage.remove([path])
            raise
        return jsonify(status="success", id=record_id)

    @api.get("")
    def list_receipts():
        user = identity()
        rows = db().table("expense_receipts").select("record_id").eq("user_id", user).execute().data or []
        return jsonify(record_ids=[row["record_id"] for row in rows])

    @api.get("/<int:record_id>")
    def receipt_url(record_id):
        user = identity()
        rows = db().table("expense_receipts").select("object_path").eq("record_id", record_id).eq("user_id", user).execute().data or []
        if not rows:
            return jsonify(status="error", message="ไม่พบรูปหลักฐาน"), 404
        owned = db().table("transactions").select("id").eq("id", record_id).eq("user_id", user).execute().data or []
        if not owned:
            return jsonify(status="error", message="ไม่พบรายการนี้"), 404
        signed = db().storage.from_(BUCKET).create_signed_url(rows[0]["object_path"], 60)
        url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url")
        if not url:
            raise RuntimeError("Signed URL missing")
        if url.startswith("/"):
            url = urljoin(supabase_url, url)
        return jsonify(url=url)

    app.register_blueprint(api)

    def cleanup(user, record_id):
        if private_client is None:
            return
        rows = db().table("expense_receipts").select("object_path").eq("record_id", record_id).eq("user_id", user).execute().data or []
        if rows:
            db().storage.from_(BUCKET).remove([rows[0]["object_path"]])
            db().table("expense_receipts").delete().eq("record_id", record_id).eq("user_id", user).execute()

    return cleanup

