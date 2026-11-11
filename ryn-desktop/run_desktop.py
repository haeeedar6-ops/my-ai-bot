"""
run_desktop.py — نقطة تشغيل عميل ويندوز (RYN Desktop Agent).

    python run_desktop.py            # تشغيل مباشر
    python run_desktop.py --version  # رقم الإصدار

هاد الملف هو اللي بيبني منو PyInstaller ملف الـ EXE (مو app/main.py)،
لأنو app/main.py بيستعمل imports نسبية ما بتشتغل إذا انشغّل كسكربت لحاله.
"""
import sys

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
