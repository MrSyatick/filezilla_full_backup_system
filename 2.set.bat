@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title BackupMaster - Gelişmiş Kurulum
color 0A

echo.
echo ================================================
echo        🚀 BackupMaster Kurulum Programı
echo ================================================
echo.

:: Python kontrolü
echo [-] Python kontrol ediliyor...
python --version >nul 2>&1
if errorlevel 1 (
    echo [❌] Python bulunamadi!
    echo.
    echo 📥 Python indirme sayfasi: https://www.python.org/downloads/
    echo 💡 Kurulum sirasinda "Add Python to PATH" secenegini isaretleyin!
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%i"
echo [✅] !PYTHON_VERSION! bulundu

:: Tkinter kontrolü
echo [-] Tkinter kontrol ediliyor...
python -c "import tkinter; print('success')" >nul 2>&1
if errorlevel 1 (
    echo [❌] Tkinter bulunamadi!
    echo.
    echo 🔧 Cozum onerileri:
    echo 1. Python'u yeniden yukleyin
    echo 2. Kurulumda 'tcl/tk and IDLE' secenegini secin
    echo 3. Windows kullanicilari icin: https://www.tcl.tk/software/tcltk/
    echo.
    pause
    exit /b 1
)
echo [✅] Tkinter mevcut

:: PIP güncelleme
echo [-] PIP guncelleniyor...
python -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [⚠️] PIP guncellenemedi, mevcut versiyonla devam ediliyor...
) else (
    echo [✅] PIP basariyla guncellendi
)

echo.
echo 📦 Gereksinimler kuruluyor...
echo.

:: Ana gereksinimler
set "PACKAGES=paramiko cryptography Pillow schedule pathlib2"

for %%p in (%PACKAGES%) do (
    echo [-] %%p kuruluyor...
    python -m pip install %%p --quiet
    if errorlevel 1 (
        echo [❌] %%p kurulumu basarisiz!
    ) else (
        echo [✅] %%p kuruldu
    )
)

echo.
echo 🔍 Kurulum kontrol ediliyor...
echo.

python -c "
import sys
print('=== KURULUM KONTROLÜ ===')
modules = [
    ('tkinter', 'tkinter'),
    ('paramiko', 'paramiko'),
    ('cryptography', 'cryptography'),
    ('PIL', 'Pillow'),
    ('schedule', 'schedule'),
    ('pathlib', 'pathlib')
]

all_ok = True
for module, name in modules:
    try:
        __import__(module)
        print(f'✅ {name:15} - OK')
    except ImportError as e:
        print(f'❌ {name:15} - FAILED')
        all_ok = False

print('=======================')
if all_ok:
    print('🎉 TÜM GEREKSİNİMLER KURULDU!')
    print('🚀 Programı çalıştırmak için: python main.py')
else:
    print('❌ Bazı paketler kurulamadı!')
    print('   Lütfen hataları çözün ve tekrar deneyin.')
"

echo.
echo ================================================
echo              KURULUM TAMAMLANDI!
echo ================================================
echo.

:: Kullanım talimatları
echo 📝 KULLANIM TALİMATLARI:
echo.
echo 1. 🚀 Programı çalıştır: python main.py
echo 2. 📁 Yeni sunucu eklemek için sol paneli kullan
echo 3. 🔗 Bağlantıyı test et butonu ile kontrol et
echo 4. 📦 Yedekleme sekmesinden yedekleme yap
echo 5. 🎭 Demo mod ile önce test yapabilirsin
echo.

:: Hızlı başlangıç dosyası oluştur
echo @echo off > baslat.bat
echo chcp 65001 >> baslat.bat
echo echo BackupMaster baslatiliyor... >> baslat.bat
echo python main.py >> baslat.bat
echo pause >> baslat.bat

echo [📄] 'baslat.bat' dosyasi olusturuldu - programi hizli baslatmak icin kullanin!
echo.

pause