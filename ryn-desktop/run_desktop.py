"""نقطة تشغيل ثابتة (تعمل مع PyInstaller): python run_desktop.py"""
import sys

from app.ui import launch_desktop_ui

if __name__ == "__main__":
    if "--version" in sys.argv:
        print("RYN Desktop Agent v1.1.0")
        raise SystemExit(0)
    launch_desktop_ui()
