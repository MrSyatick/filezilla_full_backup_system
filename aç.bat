@echo off
chcp 65001 >nul
title BackupMaster
echo 🚀 BackupMaster başlatılıyor...
echo 📅 %date% %time%
echo.
cd /d "%~dp0"
python main.py
pause