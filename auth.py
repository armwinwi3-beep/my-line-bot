"""Request identity checks shared by every /api route.

Rules:
- A valid X-Admin-Pin header (matching the ADMIN_PIN env var) may act as any user_id.
  This is the owner key used by the web admin mode and the Scriptable scripts.
- Otherwise the caller must send "Authorization: Bearer <LIFF access token>" and the
  LINE profile behind that token must be the same user_id the request names.
"""
import hashlib
import hmac
import os
import threading
import time

import requests

MAX_PIN_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

_failures = {}
_lock = threading.Lock()

# Verified LINE tokens, so each page load does not ask LINE again for every request.
TOKEN_CACHE_SECONDS = 5 * 60
TOKEN_CACHE_MAX = 1000
_token_cache = {}


def _line_profile_id(token):
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
    try:
        response = requests.get(
            "https://api.line.me/v2/profile",
            headers={"Authorization": token},
            timeout=10,
        )
        profile_id = response.json().get("userId") if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        raise PermissionError("ตรวจสอบบัญชี LINE ไม่สำเร็จ กรุณาลองใหม่")
    if profile_id:
        with _lock:
            if len(_token_cache) >= TOKEN_CACHE_MAX:
                for old in [k for k, v in _token_cache.items() if v[1] <= now] or list(_token_cache)[:TOKEN_CACHE_MAX // 2]:
                    _token_cache.pop(old, None)
            _token_cache[key] = (profile_id, now + TOKEN_CACHE_SECONDS)
    return profile_id


def _client_key(request):
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "unknown"


# Wrong-PIN counts must survive between requests. On Vercel each request may run in a
# fresh instance, so the counts live in Supabase (table auth_pin_failures, service-role
# key only). Without that key (tests, local runs) they stay in memory.
_pin_db = None


def _pin_store():
    global _pin_db
    if _pin_db is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            return None
        from supabase import create_client
        _pin_db = create_client(url, key)
    return _pin_db


def _get_failures(key):
    db = _pin_store()
    if db is not None:
        try:
            rows = db.table("auth_pin_failures").select("count,since").eq("client_key", key).limit(1).execute().data or []
            return (rows[0]["count"], float(rows[0]["since"])) if rows else None
        except Exception:
            pass
    with _lock:
        return _failures.get(key)


def _set_failures(key, count, since):
    db = _pin_store()
    if db is not None:
        try:
            db.table("auth_pin_failures").upsert(
                {"client_key": key, "count": count, "since": since}, on_conflict="client_key"
            ).execute()
            return
        except Exception:
            pass
    with _lock:
        _failures[key] = (count, since)


def _clear_failures(key):
    db = _pin_store()
    if db is not None:
        try:
            db.table("auth_pin_failures").delete().eq("client_key", key).execute()
        except Exception:
            pass
    with _lock:
        _failures.pop(key, None)


def _check_pin(request):
    """Return True for a correct PIN, False when no PIN was sent, raise on a wrong one."""
    sent = request.headers.get("X-Admin-Pin", "")
    if not sent:
        return False
    key = hashlib.sha256(_client_key(request).encode()).hexdigest()
    now = time.time()
    count, since = _get_failures(key) or (0, now)
    locked = count >= MAX_PIN_FAILURES and now - since < LOCKOUT_SECONDS
    if locked:
        raise PermissionError("ใส่ PIN ผิดหลายครั้ง กรุณารอ 15 นาทีแล้วลองใหม่")
    pin = os.environ.get("ADMIN_PIN", "")
    if pin and hmac.compare_digest(sent.encode(), pin.encode()):
        if count:
            _clear_failures(key)
        return True
    if now - since >= LOCKOUT_SECONDS:
        count, since = 0, now
    _set_failures(key, count + 1, since)
    raise PermissionError("PIN แอดมินไม่ถูกต้อง")


def verify_user(request, user_id):
    """Return user_id if the caller may act as it, otherwise raise PermissionError."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise PermissionError("กรุณาเข้าสู่ระบบก่อน")
    user_id = user_id.strip()
    if _check_pin(request):
        return user_id
    if user_id == "admin":
        raise PermissionError("กรุณาเข้าสู่ระบบแอดมินอีกครั้ง")
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer ") or len(token) <= len("Bearer "):
        raise PermissionError("กรุณาเปิดเว็บผ่าน LINE อีกครั้ง")
    if _line_profile_id(token) != user_id:
        raise PermissionError("บัญชี LINE ไม่ตรงกับผู้ใช้")
    return user_id
