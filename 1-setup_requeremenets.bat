@echo off
chcp 65001 >nul
echo BackupMaster Kurulumu...
echo.
python -m pip install paramiko mysql-connector-python schedule cryptography pymysql psycopg2-binary mysqldump mysql.connector

echo.
echo Kurulum tamamlandi!
echo Programi calistirmak icin: python main.py
pause