"""
main.py — نقطة الدخول لتطبيق سطح المكتب RYN Desktop (Windows).
"""
import sys
from .ui import launch_desktop_ui

if __name__ == "__main__":
    if "--version" in sys.argv:
        print("RYN Desktop Agent v1.0.0")
        raise SystemExit(0)
    launch_desktop_ui()
