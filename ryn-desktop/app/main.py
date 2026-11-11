"""
main.py — نقطة الدخول لتطبيق سطح المكتب RYN Desktop (Windows).

للتشغيل استعمل run_desktop.py (أو: python -m app.main من مجلد ryn-desktop).
"""
import sys
from typing import List, Optional

from .ui import launch_desktop_ui

VERSION = "1.0.0"


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv:
        print(f"RYN Desktop Agent v{VERSION}")
        return 0
    launch_desktop_ui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
