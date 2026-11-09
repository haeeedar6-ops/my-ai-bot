"""
pairing.py — إقران الجهاز وتخزين التوكن المحلي بأمان (Section 6 & 8).
يستخدم keyring النظام (Windows Credential Locker) عند توفره، أو ملف صلاحيات مقيد للمالك فقط (0600).
لا يُطبع التوكن الخام في السجلات أبداً.
"""
import os
import stat
from typing import Optional

from .local_config import CONFIG_DIR, load_config, save_config

SERVICE_NAME = "RYN_Desktop_Agent"
TOKEN_FALLBACK_PATH = os.path.join(CONFIG_DIR, ".device_credential")


def store_device_token(device_id: str, raw_token: str) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cfg = load_config()
    cfg["device_id"] = device_id
    save_config(cfg)
    try:
        import keyring
        keyring.set_password(SERVICE_NAME, device_id or "default", raw_token)
        return
    except Exception:
        _ = None
    with open(TOKEN_FALLBACK_PATH, "w", encoding="utf-8") as f:
        f.write(raw_token.strip())
    try:
        os.chmod(TOKEN_FALLBACK_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        _ = None


def load_device_token() -> Optional[str]:
    cfg = load_config()
    did = cfg.get("device_id") or "default"
    try:
        import keyring
        tok = keyring.get_password(SERVICE_NAME, did)
        if tok:
            return tok.strip()
    except Exception:
        _ = None
    if os.path.isfile(TOKEN_FALLBACK_PATH):
        try:
            with open(TOKEN_FALLBACK_PATH, "r", encoding="utf-8") as f:
                return f.read().strip() or None
        except Exception:
            return None
    return None


def clear_device_token() -> None:
    cfg = load_config()
    did = cfg.get("device_id") or "default"
    try:
        import keyring
        keyring.delete_password(SERVICE_NAME, did)
    except Exception:
        _ = None
    if os.path.isfile(TOKEN_FALLBACK_PATH):
        try:
            os.remove(TOKEN_FALLBACK_PATH)
        except Exception:
            _ = None
