"""
main.py — نقطة الدخول لتطبيق سطح المكتب RYN Desktop (Windows).
يشتغل بالطريقتين: python -m app.main  أو  python run_desktop.py (الموصى به + PyInstaller).
"""
import sys

try:
    from .ui import launch_desktop_ui
except ImportError:  # تشغيل مباشر: python app/main.py
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.ui import launch_desktop_ui

if __name__ == "__main__":
    if "--version" in sys.argv:
        print("RYN Desktop Agent v1.1.0")
        raise SystemExit(0)
    launch_desktop_ui()
