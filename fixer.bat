@echo off
chcp 65001 >nul
title BackupMaster - Sorun Giderme
echo 🔧 BackupMaster Sorun Giderme Aracı
echo.

echo 1. Python ve PIP kontrolü...
python --version >nul 2>&1 && echo ✅ Python mevcut || echo ❌ Python bulunamadı
python -m pip --version >nul 2>&1 && echo ✅ PIP mevcut || echo ❌ PIP bulunamadı

echo.
echo 2. Gereksinimleri kontrol et...
python -c "
try:
    import tkinter
    print('✅ tkinter - OK')
except: print('❌ tkinter - FAILED')

try:
    import paramiko
    print('✅ paramiko - OK')
except: print('❌ paramiko - FAILED')

try:
    import cryptography
    print('✅ cryptography - OK')
except: print('❌ cryptography - FAILED')

try:
    from PIL import Image
    print('✅ Pillow - OK')
except: print('❌ Pillow - FAILED')

try:
    import schedule
    print('✅ schedule - OK')
except: print('❌ schedule - FAILED')
"

echo.
echo 3. Olası çözümler:
echo.
echo 📥 Eksik paketleri kur: python -m pip install paramiko cryptography Pillow schedule
echo 🔄 PIP'i güncelle: python -m pip install --upgrade pip
echo 🏗️  Windows için: https://visualstudio.microsoft.com/visual-cpp-build-tools/
echo 🌍 İnternet bağlantınızı kontrol edin
echo.

pause