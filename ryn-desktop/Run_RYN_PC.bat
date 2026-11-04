@echo off
chcp 65001 >nul
title RYN Desktop Agent - Windows PC
echo ========================================================
echo   🤖 جاري تجهيز وتشغيل RYN Desktop Agent على الكمبيوتر...
echo ========================================================
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] يرجى تثبيت Python من موقع python.org مع تفعيل خيار Add Python to PATH
    pause
    exit /b 1
)

echo [*] جاري التأكد من المكتبات المطلوبة...
python -m pip install --quiet -r requirements.txt

echo [*] جاري فتح نافذة RYN Desktop Agent...
python -m app.main
pause
