"""
local_config.py — إعدادات عميل سطح المكتب لويندوز (بدون أي مفاتيح سرية مدمجة).
"""
import json
import os
from typing import Any, Dict

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".ryn_desktop")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "server_url": os.environ.get("RYN_SERVER_URL", "https://ryn-ai-1.onrender.com").rstrip("/"),
    "device_id": "",
    "device_name": os.environ.get("COMPUTERNAME", "Windows-PC"),
    "auto_approve_low_risk": True,
    "require_approval_medium": True,
    "emergency_stop_hotkey": "F12",
    "max_image_width": 1280,
    "jpeg_quality": 78,
}


def load_config() -> Dict[str, Any]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            _ = None
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    safe_cfg = {k: v for k, v in cfg.items() if "token" not in k.lower() and "secret" not in k.lower()}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe_cfg, f, ensure_ascii=False, indent=2)
