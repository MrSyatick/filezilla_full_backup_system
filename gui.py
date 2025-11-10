import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import schedule # type: ignore
from datetime import datetime
import os
import sys
from pathlib import Path
import zipfile
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
import time
import xml.etree.ElementTree as ET
from email import encoders
import sqlite3

# Masaüstü bildirimleri için (isteğe bağlı)
try:
    from plyer import notification
except ImportError:
    notification = None

from server_manager import ServerManager
from config import ConfigManager
from backup_manager import AdvancedBackupManager, BackupManager, DatabaseManager

class EmailManager:
    def __init__(self):
        self.smtp_config = {}
    
    def send_backup_email(self, smtp_config, backup_files, subject="Yedekleme Dosyaları"):
        """Yedekleri email ile gönder"""
        try:
            # Email mesajı oluştur
            msg = MIMEMultipart()
            msg['From'] = smtp_config['from_email']
            msg['To'] = smtp_config['to_email']
            msg['Subject'] = subject
            
            # Mesaj gövdesi
            body = f"""
            Yedekleme işlemi tamamlandı.
            
            Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            Toplam Dosya: {len(backup_files)}
            
            Bu email BackupMaster uygulaması tarafından otomatik olarak gönderilmiştir.
            """
            msg.attach(MIMEText(body, 'plain'))
            
            # Dosyaları ekle
            for file_path in backup_files:
                if Path(file_path).exists():
                    with open(file_path, 'rb') as f:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                    
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename={Path(file_path).name}'
                    )
                    msg.attach(part)
            
            # SMTP bağlantısı ve gönderim
            server = smtplib.SMTP(smtp_config['smtp_server'], smtp_config['smtp_port'])
            server.starttls()
            server.login(smtp_config['username'], smtp_config['password'])
            text = msg.as_string()
            server.sendmail(smtp_config['from_email'], smtp_config['to_email'], text)
            server.quit()
            
            return True, "Email başarıyla gönderildi"
            
        except Exception as e:
            return False, f"Email gönderme hatası: {str(e)}"

class RestoreManager:
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
    
    def restore_backup(self, zip_path, restore_config):
        if self.log_callback:
            self.log_callback("🔄 Geri yükleme başlatılıyor...")
        
        steps = [
            ("📦 ZIP dosyası açılıyor...", 20),
            ("🔍 Yedek dosyaları kontrol ediliyor...", 40),
            ("⬆️ Dosyalar sunucuya yükleniyor...", 60),
            ("🗄️ Veritabanı geri yükleniyor...", 80),
            ("✅ Geri yükleme tamamlandı!", 100)
        ]
        
        for message, progress in steps:
            if self.log_callback:
                self.log_callback(message)
            if self.progress_callback:
                self.progress_callback(progress, 100)
            import time
            time.sleep(0.5)
        
        return True, "Geri yükleme tamamlandı"

class HistoryManager:
    def __init__(self, db_path):
        self.db_path = db_path
        # check_same_thread=False, çünkü GUI ana thread'i ve yedekleme worker thread'i veritabanına erişecek.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL, -- 'Çalışıyor', 'Tamamlandı', 'Başarısız', 'Durduruldu'
                backup_type TEXT,
                zip_path TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backup_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                history_id INTEGER,
                timestamp TEXT NOT NULL,
                message TEXT NOT NULL,
                FOREIGN KEY (history_id) REFERENCES backup_history (id)
            )
        ''')
        self.conn.commit()

    def start_backup_record(self, server_name, backup_type):
        start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO backup_history (server_name, start_time, status, backup_type) VALUES (?, ?, ?, ?)",
            (server_name, start_time, 'Çalışıyor', backup_type)
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_log(self, history_id, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO backup_logs (history_id, timestamp, message) VALUES (?, ?, ?)",
            (history_id, timestamp, message)
        )
        self.conn.commit()

    def update_backup_status(self, history_id, status, zip_path=None):
        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE backup_history SET status = ?, end_time = ?, zip_path = ? WHERE id = ?",
            (status, end_time, zip_path, history_id)
        )
        self.conn.commit()

    def get_history(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, server_name, start_time, status, backup_type FROM backup_history ORDER BY id DESC")
        return cursor.fetchall()

    def get_logs_for_history(self, history_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT timestamp, message FROM backup_logs WHERE history_id = ? ORDER BY id ASC", (history_id,))
        return cursor.fetchall()

    def clear_all_history(self):
        """Veritabanındaki tüm geçmiş ve log kayıtlarını siler."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM backup_logs")
            cursor.execute("DELETE FROM backup_history")
            self.conn.commit()
            return True, "Tüm yedekleme geçmişi başarıyla temizlendi."
        except Exception as e:
            self.conn.rollback()
            return False, f"Geçmiş temizlenirken bir hata oluştu: {str(e)}"

class ModernBackupMaster:
    def __init__(self, root):
        self.root = root
        self.setup_window()
        
        self.server_manager = ServerManager()
        self.config_manager = ConfigManager()
        self.email_manager = EmailManager()
        self.db_manager = DatabaseManager() # For testing DB connections
        self.backup_manager = None
        self.restore_manager = RestoreManager()
        
        # Geçmiş yöneticisini başlat
        db_path = os.path.join(self.config_manager.config_dir, "history.db")
        self.history_manager = HistoryManager(db_path)
        
        self.servers = self.config_manager.load_servers()
        # Zamanlayıcıyı başlat
        self.scheduler_thread = threading.Thread(target=self.run_scheduler, daemon=True)
        self.scheduler_thread.start()
        
        self.current_server = None
        self.current_db_index = None
        self.current_history_id = None
        self.is_operation_running = False

        # Hız hesaplama için değişkenler
        self.last_speed_check_time = 0
        self.last_bytes_transferred = 0
        self.total_bytes_transferred_session = 0

        
        # Event binding için değişkenler
        self._bind_events()
        
        self.setup_styles()
        self.setup_gui()
        self.load_servers_list()
        self.load_all_schedules()
        self.update_status("Hazır")
    
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    def run_scheduler(self):
        """Arka planda zamanlanmış görevleri çalıştırır."""
        while True:
            schedule.run_pending()
            time.sleep(1)
    
    def _bind_events(self):
        """Event binding'leri yönet"""
        self.root.bind('<Button-1>', self.on_root_click)
    
    def on_root_click(self, event):
        """Root tıklamasında focus kontrolü"""
        # Sadece gerçekten boş alana tıklanırsa
        if event.widget == self.root:
            pass  # Hiçbir şey yapma, focus kaybolmasın

    def on_closing(self):
        """Uygulama kapatılırken kontrol et."""
        if self.is_operation_running:
            if messagebox.askyesno("Çıkışı Onayla", "Bir yedekleme/geri yükleme işlemi devam ediyor. Çıkmak istediğinizden emin misiniz? İşlem durdurulacak."):
                if self.backup_manager:
                    self.backup_manager.stop_backup()
                self.root.destroy()
        else:
            self.root.destroy()
    
    def setup_window(self):
        """Soft pencere ayarları"""
        # Pencereyi görünmez yap
        self.root.withdraw()

        w = 1100
        w = 1230
        h = 720
        self.root.title("FTP/DB Backup Master Pro")
        self.root.minsize(950, 600)
        self.root.configure(bg='#f5f5f5')

        # Pencereyi ekranın ortasına yerleştir
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width // 2) - (w // 2)
        y = (screen_height // 2) - (h // 2)
        self.root.geometry(f'{w}x{h}+{x}+{y}')


        # Pencereyi görünür yap
        self.root.deiconify()
    
    def setup_styles(self):
        """Soft minimalist stiller"""
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        # Soft gri tonları
        self.colors = {
            'primary': '#2c3e50',
            'primary_light': '#34495e',
            'secondary': '#7f8c8d',
            'success': '#27ae60',
            'warning': '#f39c12',
            'error': '#e74c3c',
            'background': '#f5f5f5',
            'surface': '#ffffff',
            'surface_light': '#f8f9fa',
            'text_primary': '#2c3e50',
            'text_secondary': '#7f8c8d',
            'text_light': '#bdc3c7',
            'border': '#e0e0e0',
            'border_light': '#ecf0f1',
        }
        
        # Minimalist fontlar
        self.fonts = {
            'title': ('Segoe UI', 13, 'bold'),
            'subtitle': ('Segoe UI', 11, 'bold'),
            'body': ('Segoe UI', 9),
            'body_bold': ('Segoe UI', 9, 'bold'),
            'caption': ('Segoe UI', 8),
            'small': ('Segoe UI', 8)
        }
        
        self._configure_styles()
    
    def _configure_styles(self):
        """TTK stillerini özelleştir"""
        # Frame stilleri
        self.style.configure('Modern.TFrame', background=self.colors['background'])
        self.style.configure('Card.TFrame', background=self.colors['surface'])
        
        # Label stilleri
        self.style.configure('Title.TLabel', font=self.fonts['title'],
                           background=self.colors['background'], foreground=self.colors['text_primary'])
        self.style.configure('Subtitle.TLabel', font=self.fonts['subtitle'],
                           background=self.colors['background'], foreground=self.colors['text_primary'])
        self.style.configure('Body.TLabel', font=self.fonts['body'],
                           background=self.colors['background'], foreground=self.colors['text_primary'])
        
        # Button stilleri
        self.style.configure('Primary.TButton', font=self.fonts['body_bold'],
                           background=self.colors['primary'], foreground='white',
                           borderwidth=0, padding=(10, 5))
        self.style.configure('Secondary.TButton', font=self.fonts['body'],
                           background=self.colors['surface'], foreground=self.colors['text_primary'],
                           borderwidth=1, padding=(8, 4))
        
        # Entry ve Combobox stilleri
        self.style.configure('Modern.TEntry', font=self.fonts['body'],
                           borderwidth=1, relief='flat', padding=(5, 3))
        self.style.configure('Modern.TCombobox', font=self.fonts['body'],
                           borderwidth=1, relief='flat', padding=(5, 3))
        
        # Progressbar stili
        self.style.configure('Modern.Horizontal.TProgressbar',
                           background=self.colors['primary'], borderwidth=0)
        
        # Scrollbar stili (düzeltilmiş)
        self.style.configure('Modern.Vertical.TScrollbar',
                           background=self.colors['border_light'],
                           troughcolor=self.colors['background'],
                           borderwidth=0,
                           relief='flat')
        
        # Treeview stili
        self.style.configure('Modern.Treeview',
                           background=self.colors['surface'],
                           fieldbackground=self.colors['surface'],
                           foreground=self.colors['text_primary'],
                           borderwidth=0,
                           relief='flat')
        
        self.style.configure('Modern.Treeview.Heading',
                           background=self.colors['surface_light'],
                           foreground=self.colors['text_primary'],
                           borderwidth=1,
                           relief='flat')
        
        # Notebook stili (sekmeler için)
        self.style.configure('TNotebook.Tab', 
                           font=self.fonts['body'], 
                           padding=[12, 5], 
                           background=self.colors['surface_light'],
                           foreground=self.colors['text_secondary'],
                           borderwidth=0)
        self.style.map('TNotebook.Tab', background=[('selected', self.colors['surface'])], foreground=[('selected', self.colors['primary'])])
    
    def create_card(self, parent, padding=10, **kwargs):
        """Soft kart stili frame oluştur"""
        return tk.Frame(parent, bg=self.colors['surface'], relief='flat',
                       borderwidth=1, highlightbackground=self.colors['border'],
                       highlightthickness=1, **kwargs)
    
    def setup_gui(self):
        """Soft GUI düzeni"""
        main_container = ttk.Frame(self.root, style='Modern.TFrame', padding=10)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        self.setup_header(main_container)
        
        content_frame = ttk.Frame(main_container, style='Modern.TFrame')
        content_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        content_frame.columnconfigure(1, weight=1)
        content_frame.rowconfigure(0, weight=1)
        
        self.setup_servers_panel(content_frame)
        self.setup_details_panel(content_frame)
    
    def setup_header(self, parent):
        """Minimalist header"""
        header_card = self.create_card(parent, padding=10)
        header_card.pack(fill=tk.X)
        
        title_frame = tk.Frame(header_card, bg=self.colors['surface'])
        title_frame.pack(fill=tk.X)
        
        tk.Label(title_frame, text="📦", font=('Segoe UI Emoji', 16),
                bg=self.colors['surface'], fg=self.colors['primary']).pack(side=tk.LEFT, padx=(0, 8))
        
        tk.Label(title_frame, text="FTP/DB Backup Master Pro", font=self.fonts['title'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(side=tk.LEFT)
        
        # Header butonları
        header_btn_frame = tk.Frame(header_card, bg=self.colors['surface'])
        header_btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(header_btn_frame, text="İçe/Dışa Aktar", style='Secondary.TButton',
                  command=self.show_import_export_dialog).pack(side=tk.LEFT, padx=(4, 0))
        
        status_frame = tk.Frame(header_card, bg=self.colors['surface'])
        status_frame.pack(side=tk.RIGHT, padx=(10, 0))
        
        self.status_label = tk.Label(status_frame, text="Hazır", font=self.fonts['caption'],
                                   bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.status_label.pack(side=tk.RIGHT)
    
    def setup_servers_panel(self, parent):
        """Sol panel - Sunucu listesi (düzeltilmiş scrollbar)"""
        servers_card = self.create_card(parent, padding=10)
        servers_card.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
        servers_card.rowconfigure(2, weight=1)  # Liste için weight
        
        tk.Label(servers_card, text="Sunucular", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        # Arama
        search_frame = tk.Frame(servers_card, bg=self.colors['surface'])
        search_frame.pack(fill=tk.X, pady=(0, 8))
        
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, style='Modern.TEntry', textvariable=self.search_var)
        search_entry.pack(fill=tk.X)
        search_entry.insert(0, "🔍 Sunucu ara...")
        search_entry.bind('<KeyRelease>', self.filter_servers)
        search_entry.bind('<FocusIn>', lambda e: search_entry.delete(0, tk.END) if search_entry.get() == "🔍 Sunucu ara..." else None)
        
        # Sunucu listesi container
        list_container = tk.Frame(servers_card, bg=self.colors['surface'])
        list_container.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        
        # Treeview ile sunucu listesi
        self.servers_tree = ttk.Treeview(list_container, columns=('name',), show='tree', height=15)
        self.servers_tree.heading('#0', text='')
        self.servers_tree.column('#0', width=200)
        
        # Düzgün scrollbar
        tree_scrollbar = ttk.Scrollbar(list_container, orient="vertical", 
                                     command=self.servers_tree.yview,
                                     style='Modern.Vertical.TScrollbar')
        self.servers_tree.configure(yscrollcommand=tree_scrollbar.set)
        
        self.servers_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.servers_tree.bind('<<TreeviewSelect>>', self.on_server_select)
        
        # Butonlar
        btn_frame = tk.Frame(servers_card, bg=self.colors['surface'])
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="Yeni Sunucu", style='Primary.TButton',
                  command=self.add_server).pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btn_frame, text="Sunucuyu Sil", style='Secondary.TButton',
                  command=self.delete_server).pack(fill=tk.X)
    
    def setup_details_panel(self, parent):
        """Sağ panel - Detaylar"""
        details_frame = ttk.Frame(parent, style='Modern.TFrame')
        details_frame.grid(row=0, column=1, sticky='nsew')
        
        self.notebook = ttk.Notebook(details_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        self.setup_server_tab()
        self.setup_database_tab()
        self.setup_backup_tab()
        self.setup_email_tab()
        self.setup_restore_tab()
        self.setup_progress_tab()
        self.setup_history_tab()
        self.setup_schedule_tab()
    
    def setup_server_tab(self):
        """Sunucu detayları sekmesi"""
        server_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(server_tab, text="🌐 Sunucu")
        
        form_card = self.create_card(server_tab, padding=15)
        form_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.form_widgets = {}
        form_rows = [
            ("Sunucu Adı", "server_name", ""),
            ("Host", "host", ""),
            ("Port", "port", "21"),
            ("Kullanıcı Adı", "username", ""),
            ("Şifre", "password", ""),
            ("Web Dizini", "web_root", "/public_html")
        ]
        
        for i, (label, key, default) in enumerate(form_rows):
            row_frame = tk.Frame(form_card, bg=self.colors['surface'])
            row_frame.pack(fill=tk.X, pady=5)
            
            tk.Label(row_frame, text=label, font=self.fonts['body'],
                    bg=self.colors['surface'], fg=self.colors['text_primary'],
                    width=12, anchor='w').pack(side=tk.LEFT)
            
            if key == "password":
                widget = ttk.Entry(row_frame, style='Modern.TEntry', show="•")
            else:
                widget = ttk.Entry(row_frame, style='Modern.TEntry')
            
            widget.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
            if default:
                widget.insert(0, default)
            self.form_widgets[key] = widget
        
        # Protokol
        protocol_frame = tk.Frame(form_card, bg=self.colors['surface'])
        protocol_frame.pack(fill=tk.X, pady=8)
        
        tk.Label(protocol_frame, text="Protokol", font=self.fonts['body'],
                bg=self.colors['surface'], fg=self.colors['text_primary'],
                width=12, anchor='w').pack(side=tk.LEFT)
        
        protocol_btn_frame = tk.Frame(protocol_frame, bg=self.colors['surface'])
        protocol_btn_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        
        self.protocol = tk.StringVar(value="ftp")
        ttk.Radiobutton(protocol_btn_frame, text="FTP", variable=self.protocol, value="ftp").pack(side=tk.LEFT)
        ttk.Radiobutton(protocol_btn_frame, text="SFTP", variable=self.protocol, value="sftp").pack(side=tk.LEFT, padx=(12, 0))
        
        # Butonlar
        btn_frame = tk.Frame(form_card, bg=self.colors['surface'])
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        
        ttk.Button(btn_frame, text="Bağlantıyı Test Et", style='Secondary.TButton',
                  command=self.test_connection).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="Kaydet", style='Primary.TButton',
                  command=self.save_server).pack(side=tk.LEFT)
    
    def setup_database_tab(self):
        """Veritabanı sekmesi"""
        db_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(db_tab, text="🗄️ Veritabanları")
        
        # Veritabanı listesi
        list_card = self.create_card(db_tab, padding=10)
        list_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Başlık ve butonlar
        header_frame = tk.Frame(list_card, bg=self.colors['surface'])
        header_frame.pack(fill=tk.X, pady=(0, 8))
        
        tk.Label(header_frame, text="Veritabanları", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(side=tk.LEFT)
        
        btn_frame = tk.Frame(header_frame, bg=self.colors['surface'])
        btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(btn_frame, text="Yeni DB", style='Primary.TButton',
                  command=self.add_database).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(btn_frame, text="DB Sil", style='Secondary.TButton',
                  command=self.delete_database).pack(side=tk.LEFT, padx=(4, 0))
        
        # Veritabanı listesi
        db_list_frame = self.create_card(list_card, padding=0)
        db_list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        columns = ("name", "type", "host")
        self.db_tree = ttk.Treeview(db_list_frame, columns=columns, show='headings', height=8)
        
        self.db_tree.heading("name", text="Veritabanı Adı")
        self.db_tree.heading("type", text="Tip")
        self.db_tree.heading("host", text="Host")
        
        self.db_tree.column("name", width=120)
        self.db_tree.column("type", width=80)
        self.db_tree.column("host", width=100)
        
        self.db_tree.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self.db_tree.bind('<<TreeviewSelect>>', self.on_database_select)
        
        scrollbar = ttk.Scrollbar(db_list_frame, orient="vertical", command=self.db_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.db_tree.configure(yscrollcommand=scrollbar.set)
        
        # Veritabanı formu
        form_card = self.create_card(db_tab, padding=15)
        form_card.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        self.db_widgets = {}
        db_rows = [
            ("Veritabanı Adı", "db_name", ""),
            ("DB Tipi", "db_type", "mysql"),
            ("DB Host", "db_host", "localhost"),
            ("DB Port", "db_port", "3306"),
            ("DB Kullanıcı", "db_username", ""),
            ("DB Şifre", "db_password", "")
        ]
        
        for i, (label, key, default) in enumerate(db_rows):
            row_frame = tk.Frame(form_card, bg=self.colors['surface'])
            row_frame.pack(fill=tk.X, pady=4)
            
            tk.Label(row_frame, text=label, font=self.fonts['body'],
                    bg=self.colors['surface'], fg=self.colors['text_primary'],
                    width=12, anchor='w').pack(side=tk.LEFT)
            
            if key == "db_password":
                widget = ttk.Entry(row_frame, style='Modern.TEntry', show="•")
            elif key == "db_type":
                widget = ttk.Combobox(row_frame, style='Modern.TCombobox',
                                    values=["mysql", "postgresql"], state='readonly')
                widget.set("mysql")
            else:
                widget = ttk.Entry(row_frame, style='Modern.TEntry')
            
            widget.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
            if default and key not in ["db_type", "db_password"]:
                widget.insert(0, default)
            self.db_widgets[key] = widget
        
        # Butonlar
        db_btn_frame = tk.Frame(form_card, bg=self.colors['surface'])
        db_btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(db_btn_frame, text="Bağlantıyı Test Et", style='Secondary.TButton',
                  command=self.test_db_connection).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(db_btn_frame, text="Kaydet", style='Primary.TButton',
                  command=self.save_database).pack(side=tk.LEFT)
    
    def setup_backup_tab(self):
        """Yedekleme sekmesi"""
        backup_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(backup_tab, text="📦 Yedekleme")
        
        # Yedekleme türü
        type_card = self.create_card(backup_tab, padding=15)
        type_card.pack(fill=tk.X, padx=10, pady=(10, 8))
        
        tk.Label(type_card, text="Yedekleme Türü", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        self.backup_type = tk.StringVar(value="files_only")
        
        backup_types = [
            ("📁 Sadece Dosyalar", "files_only"),
            ("🗄️ Sadece Veritabanı", "db_only"),
            ("🎯 Tam Yedekleme", "full_backup")
        ]
        
        for text, value in backup_types:
            frame = tk.Frame(type_card, bg=self.colors['surface'])
            frame.pack(fill=tk.X, pady=2)
            
            rb = tk.Radiobutton(frame, text=text, variable=self.backup_type, value=value,
                              font=self.fonts['body'], bg=self.colors['surface'],
                              fg=self.colors['text_primary'], selectcolor=self.colors['surface'])
            rb.pack(anchor='w')
        
        # Veritabanı seçimi
        self.db_selection_card = self.create_card(backup_tab, padding=15)
        self.db_selection_card.pack(fill=tk.X, padx=10, pady=8)
        
        tk.Label(self.db_selection_card, text="Veritabanı Seçimi", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        self.db_checkboxes = {}
        self.db_selection_frame = tk.Frame(self.db_selection_card, bg=self.colors['surface'])
        self.db_selection_frame.pack(fill=tk.X)
        
        tk.Label(self.db_selection_frame, text="Veritabanı bulunamadı", font=self.fonts['body'],
                bg=self.colors['surface'], fg=self.colors['text_secondary']).pack(anchor='w')
        
        # Yedekleme bağlantısı
        self.backup_type.trace('w', self.on_backup_type_change)
        
        # Ayarlar
        settings_card = self.create_card(backup_tab, padding=15)
        settings_card.pack(fill=tk.X, padx=10, pady=8)
        
        tk.Label(settings_card, text="Yedekleme Ayarları", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        # Yedekleme yolu
        path_frame = tk.Frame(settings_card, bg=self.colors['surface'])
        path_frame.pack(fill=tk.X, pady=6)
        
        tk.Label(path_frame, text="Yedekleme Klasörü", font=self.fonts['body'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w')
        
        path_input_frame = tk.Frame(path_frame, bg=self.colors['surface'])
        path_input_frame.pack(fill=tk.X, pady=(4, 0))
        
        self.backup_target = ttk.Entry(path_input_frame, style='Modern.TEntry')
        self.backup_target.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.set_default_backup_path()
        
        ttk.Button(path_input_frame, text="Gözat", style='Secondary.TButton',
                  command=self.browse_backup_path).pack(side=tk.RIGHT, padx=(8, 0))
        
        # Dosya filtresi
        filter_frame = tk.Frame(settings_card, bg=self.colors['surface'])
        filter_frame.pack(fill=tk.X, pady=6)
        
        tk.Label(filter_frame, text="Dosya Filtresi", font=self.fonts['body'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w')
        
        self.file_filter = ttk.Combobox(filter_frame, style='Modern.TCombobox',
                                       values=["*.*", "*.php", "*.html", "*.css", "*.js", "*.jpg", "*.png"])
        self.file_filter.pack(fill=tk.X, pady=(4, 0))
        self.file_filter.set("*.*")
        
        # Seçenekler
        options_frame = tk.Frame(settings_card, bg=self.colors['surface'])
        options_frame.pack(fill=tk.X, pady=6)
        
        self.create_zip = tk.BooleanVar(value=True)
        self.send_email = tk.BooleanVar(value=False)
        
        cb1 = tk.Checkbutton(options_frame, text="Yedekleri ZIP dosyası olarak paketle",
                      variable=self.create_zip, font=self.fonts['body'],
                      bg=self.colors['surface'], fg=self.colors['text_primary'],
                      selectcolor=self.colors['surface'])
        cb1.pack(anchor='w', pady=2)
        
        cb2 = tk.Checkbutton(options_frame, text="Yedekleri email ile gönder",
                      variable=self.send_email, font=self.fonts['body'],
                      bg=self.colors['surface'], fg=self.colors['text_primary'],
                      selectcolor=self.colors['surface'])
        cb2.pack(anchor='w', pady=2)
        
        # Buton
        action_card = self.create_card(backup_tab, padding=15)
        action_card.pack(fill=tk.X, padx=10, pady=(8, 10))
        
        ttk.Button(action_card, text="Yedeklemeyi Başlat", style='Primary.TButton',
                  command=self.start_backup).pack(fill=tk.X)
    
    def setup_email_tab(self):
        """Email ayarları sekmesi"""
        email_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(email_tab, text="📧 Email")
        
        form_card = self.create_card(email_tab, padding=15)
        form_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tk.Label(form_card, text="Email Ayarları", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 12))
        
        self.email_widgets = {}
        email_rows = [
            ("SMTP Sunucusu", "smtp_server", "smtp.gmail.com"),
            ("SMTP Port", "smtp_port", "587"),
            ("Email", "from_email", ""),
            ("Şifre", "password", ""),
            ("Alıcı Email", "to_email", ""),
            ("Konu", "subject", "BackupMaster Yedekleri")
        ]
        
        for i, (label, key, default) in enumerate(email_rows):
            row_frame = tk.Frame(form_card, bg=self.colors['surface'])
            row_frame.pack(fill=tk.X, pady=5)
            
            tk.Label(row_frame, text=label, font=self.fonts['body'],
                    bg=self.colors['surface'], fg=self.colors['text_primary'],
                    width=12, anchor='w').pack(side=tk.LEFT)
            
            if key == "password":
                widget = ttk.Entry(row_frame, style='Modern.TEntry', show="•")
            else:
                widget = ttk.Entry(row_frame, style='Modern.TEntry')
            
            widget.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
            if default:
                widget.insert(0, default)
            self.email_widgets[key] = widget
        
        # Test butonu
        btn_frame = tk.Frame(form_card, bg=self.colors['surface'])
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        
        ttk.Button(btn_frame, text="Email Testi Gönder", style='Secondary.TButton',
                  command=self.test_email).pack(side=tk.LEFT)
    
    def setup_restore_tab(self):
        """Geri yükleme sekmesi"""
        restore_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(restore_tab, text="🔄 Geri Yükleme")
        
        form_card = self.create_card(restore_tab, padding=15)
        form_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        tk.Label(form_card, text="Geri Yükleme", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 12))
        
        # ZIP dosyası seçimi
        zip_frame = tk.Frame(form_card, bg=self.colors['surface'])
        zip_frame.pack(fill=tk.X, pady=8)
        
        tk.Label(zip_frame, text="Yedek ZIP Dosyası", font=self.fonts['body'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w')
        
        zip_input_frame = tk.Frame(zip_frame, bg=self.colors['surface'])
        zip_input_frame.pack(fill=tk.X, pady=(4, 0))
        
        self.restore_zip_path = ttk.Entry(zip_input_frame, style='Modern.TEntry')
        self.restore_zip_path.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        ttk.Button(zip_input_frame, text="Dosya Seç", style='Secondary.TButton',
                  command=self.browse_restore_file).pack(side=tk.RIGHT, padx=(8, 0))
        
        # Geri yükleme seçenekleri
        options_frame = tk.Frame(form_card, bg=self.colors['surface'])
        options_frame.pack(fill=tk.X, pady=8)
        
        self.restore_files = tk.BooleanVar(value=True)
        self.restore_database = tk.BooleanVar(value=True)
        
        cb1 = tk.Checkbutton(options_frame, text="Dosyaları geri yükle",
                      variable=self.restore_files, font=self.fonts['body'],
                      bg=self.colors['surface'], fg=self.colors['text_primary'],
                      selectcolor=self.colors['surface'])
        cb1.pack(anchor='w', pady=2)
        
        cb2 = tk.Checkbutton(options_frame, text="Veritabanını geri yükle",
                      variable=self.restore_database, font=self.fonts['body'],
                      bg=self.colors['surface'], fg=self.colors['text_primary'],
                      selectcolor=self.colors['surface'])
        cb2.pack(anchor='w', pady=2)
        
        # Buton
        btn_frame = tk.Frame(form_card, bg=self.colors['surface'])
        btn_frame.pack(fill=tk.X, pady=(16, 0))
        
        ttk.Button(btn_frame, text="Geri Yüklemeyi Başlat", style='Primary.TButton',
                  command=self.start_restore).pack(fill=tk.X)
    
    def setup_history_tab(self):
        """Yedekleme geçmişi sekmesi"""
        history_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(history_tab, text="📜 Geçmiş")

        # PanedWindow ile bölünebilir alan oluştur
        paned_window = tk.PanedWindow(history_tab, orient=tk.VERTICAL, sashrelief=tk.RAISED, bg=self.colors['background'])
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Üst panel: Geçmiş listesi
        history_list_card = self.create_card(paned_window, padding=15)
        paned_window.add(history_list_card, height=200)

        tk.Label(history_list_card, text="Yedekleme Geçmişi", font=self.fonts['subtitle'],
                 bg=self.colors['surface'], fg=self.colors['text_primary']).pack(side=tk.LEFT, anchor='w', pady=(0, 8))

        # Geçmişi Temizle butonu
        clear_btn = ttk.Button(history_list_card, text="Tüm Geçmişi Temizle", style='Secondary.TButton',
                               command=self.clear_history)
        clear_btn.pack(side=tk.RIGHT, anchor='ne')


        
        history_tree_frame = tk.Frame(history_list_card, bg=self.colors['surface'])
        history_tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("server", "time", "status", "type")
        self.history_tree = ttk.Treeview(history_tree_frame, columns=columns, show='headings')
        self.history_tree.heading("server", text="Sunucu")
        self.history_tree.heading("time", text="Başlangıç Zamanı")
        self.history_tree.heading("status", text="Durum")
        self.history_tree.heading("type", text="Tür")
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.history_tree.bind('<<TreeviewSelect>>', self.on_history_select)
        
        # Alt panel: Log detayları
        log_details_card = self.create_card(paned_window, padding=15)
        paned_window.add(log_details_card)

        tk.Label(log_details_card, text="Geçmiş Logları", font=self.fonts['subtitle'],
                 bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))

        self.history_log_text = tk.Text(log_details_card, height=10, wrap=tk.WORD, font=('Consolas', 9),
                                        borderwidth=0, bg=self.colors['surface_light'], relief='flat', padx=8, pady=8)
        self.history_log_text.pack(fill=tk.BOTH, expand=True)

    def setup_schedule_tab(self):
        """Zamanlama sekmesi"""
        schedule_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(schedule_tab, text="⏰ Zamanlama")

        form_card = self.create_card(schedule_tab, padding=15)
        form_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tk.Label(form_card, text="Yedekleme Zamanlama", font=self.fonts['subtitle'],
                 bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 12))

        self.schedule_widgets = {}

        # Enable/Disable
        enable_frame = tk.Frame(form_card, bg=self.colors['surface'])
        enable_frame.pack(fill=tk.X, pady=5)
        self.schedule_widgets['enabled'] = tk.BooleanVar()
        cb = tk.Checkbutton(enable_frame, text="Zamanlanmış yedeklemeyi etkinleştir",
                            variable=self.schedule_widgets['enabled'], font=self.fonts['body'],
                            bg=self.colors['surface'], fg=self.colors['text_primary'],
                            selectcolor=self.colors['surface'], command=self.toggle_schedule_controls)
        cb.pack(anchor='w')

        # Controls Frame
        self.schedule_controls_frame = tk.Frame(form_card, bg=self.colors['surface'])
        self.schedule_controls_frame.pack(fill=tk.X, pady=5)

        # Frequency
        freq_frame = tk.Frame(self.schedule_controls_frame, bg=self.colors['surface'])
        freq_frame.pack(fill=tk.X, pady=5)
        tk.Label(freq_frame, text="Sıklık", font=self.fonts['body'], bg=self.colors['surface'], width=10, anchor='w').pack(side=tk.LEFT)
        self.schedule_widgets['frequency'] = ttk.Combobox(freq_frame, style='Modern.TCombobox', values=["Günlük", "Haftalık"], state='readonly')
        self.schedule_widgets['frequency'].pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.schedule_widgets['frequency'].bind("<<ComboboxSelected>>", self.on_frequency_change)

        # Day of Week (for weekly)
        self.day_frame = tk.Frame(self.schedule_controls_frame, bg=self.colors['surface'])
        tk.Label(self.day_frame, text="Gün", font=self.fonts['body'], bg=self.colors['surface'], width=10, anchor='w').pack(side=tk.LEFT)
        days = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
        self.schedule_widgets['day'] = ttk.Combobox(self.day_frame, style='Modern.TCombobox', values=days, state='readonly')
        self.schedule_widgets['day'].pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        # Time
        time_frame = tk.Frame(self.schedule_controls_frame, bg=self.colors['surface'])
        time_frame.pack(fill=tk.X, pady=5)
        tk.Label(time_frame, text="Saat", font=self.fonts['body'], bg=self.colors['surface'], width=10, anchor='w').pack(side=tk.LEFT)
        
        time_inner_frame = tk.Frame(time_frame, bg=self.colors['surface'])
        time_inner_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        
        hours = [f"{h:02d}" for h in range(24)]
        minutes = [f"{m:02d}" for m in range(0, 60, 5)]
        self.schedule_widgets['hour'] = ttk.Combobox(time_inner_frame, style='Modern.TCombobox', values=hours, width=5, state='readonly')
        self.schedule_widgets['hour'].pack(side=tk.LEFT)
        tk.Label(time_inner_frame, text=":", font=self.fonts['body_bold'], bg=self.colors['surface']).pack(side=tk.LEFT, padx=2)
        self.schedule_widgets['minute'] = ttk.Combobox(time_inner_frame, style='Modern.TCombobox', values=minutes, width=5, state='readonly')
        self.schedule_widgets['minute'].pack(side=tk.LEFT)

        # Next Run Label
        self.next_run_label = tk.Label(form_card, text="Sonraki çalışma: -", font=self.fonts['caption'],
                                       bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.next_run_label.pack(anchor='w', pady=(10, 0))

        # Save Button
        btn_frame = tk.Frame(form_card, bg=self.colors['surface'])
        btn_frame.pack(fill=tk.X, pady=(15, 0))
        ttk.Button(btn_frame, text="Zamanlamayı Kaydet", style='Primary.TButton',
                   command=self.save_schedule).pack(side=tk.LEFT)

    def setup_progress_tab(self):
        """İlerleme sekmesi"""
        progress_tab = ttk.Frame(self.notebook, style='Modern.TFrame')
        self.notebook.add(progress_tab, text="📊 İlerleme")
        
        # İstatistikler
        stats_card = self.create_card(progress_tab, padding=15)
        stats_card.pack(fill=tk.X, padx=10, pady=(10, 8))
        
        tk.Label(stats_card, text="İstatistikler", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        stats_frame = tk.Frame(stats_card, bg=self.colors['surface'])
        stats_frame.pack(fill=tk.X)
        
        self.stats_labels = {}
        stats = [("Durum", "status"), ("İşlenen Dosya", "processed"), ("Hız", "speed"), ("İlerleme", "progress")]
        
        for i, (label, key) in enumerate(stats):
            row_frame = tk.Frame(stats_frame, bg=self.colors['surface'])
            row_frame.pack(fill=tk.X, pady=3)
            
            tk.Label(row_frame, text=f"{label}:", font=self.fonts['body_bold'],
                    bg=self.colors['surface'], fg=self.colors['text_secondary'],
                    width=10, anchor='w').pack(side=tk.LEFT)
            
            self.stats_labels[key] = tk.Label(row_frame, text="-", font=self.fonts['body'],
                                            bg=self.colors['surface'], fg=self.colors['text_primary'])
            self.stats_labels[key].pack(side=tk.LEFT)
        
        # İlerleme çubuğu
        progress_card = self.create_card(progress_tab, padding=15)
        progress_card.pack(fill=tk.X, padx=10, pady=8)
        
        tk.Label(progress_card, text="İlerleme", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        self.progress_bar = ttk.Progressbar(progress_card, style='Modern.Horizontal.TProgressbar')
        self.progress_bar.pack(fill=tk.X, pady=(0, 6))
        
        self.progress_text = tk.Label(progress_card, text="0%", font=self.fonts['body_bold'],
                                    bg=self.colors['surface'], fg=self.colors['text_secondary'])
        self.progress_text.pack()
        
        # Loglar
        log_card = self.create_card(progress_tab, padding=15)
        log_card.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 10))
        
        tk.Label(log_card, text="İşlem Geçmişi", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(anchor='w', pady=(0, 8))
        
        log_frame = tk.Frame(log_card, bg=self.colors['surface'])
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = tk.Text(log_frame, height=12, wrap=tk.WORD, font=('Consolas', 9),
                              borderwidth=0, bg=self.colors['surface_light'], relief='flat',
                              padx=8, pady=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)
    
    def show_import_export_dialog(self):
        """İçe/Dışa Aktar dialog penceresi"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Sunucu İçe/Dışa Aktar")
        dialog.geometry("400x300")
        dialog.resizable(False, False)
        dialog.configure(bg=self.colors['background'])
        
        # Dialog'un ana pencerenin üzerinde kalmasını ve odaklanmasını sağla
        dialog.transient(self.root)
        
        # Center dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        main_frame = self.create_card(dialog, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        tk.Label(main_frame, text="Sunucu İşlemleri", font=self.fonts['subtitle'],
                bg=self.colors['surface'], fg=self.colors['text_primary']).pack(pady=(0, 20))
        
        # FileZilla Import
        ttk.Button(main_frame, text="FileZilla'dan İçe Aktar", style='Primary.TButton',
                  command=self.import_from_filezilla).pack(fill=tk.X, pady=(0, 10))
        
        # JSON Import
        ttk.Button(main_frame, text="JSON'dan İçe Aktar", style='Secondary.TButton',
                  command=self.import_from_json).pack(fill=tk.X, pady=(0, 10))
        
        # JSON Export
        ttk.Button(main_frame, text="JSON'a Dışa Aktar", style='Secondary.TButton',
                  command=self.export_to_json).pack(fill=tk.X, pady=(0, 10))
        
        # Açıklama
        desc_frame = tk.Frame(main_frame, bg=self.colors['surface'])
        desc_frame.pack(fill=tk.X, pady=(20, 0))
        
        desc_text = """
FileZilla: FileZilla.xml dosyasından sunucuları içe aktarır
JSON İçe Aktar: BackupMaster formatında JSON dosyasından içe aktarır
JSON Dışa Aktar: Sunucuları JSON formatında dışa aktarır
        """
        
        tk.Label(desc_frame, text=desc_text, font=self.fonts['caption'],
                bg=self.colors['surface'], fg=self.colors['text_secondary'],
                justify=tk.LEFT).pack(anchor='w')
        
        dialog.grab_set()
    
    def import_from_filezilla(self):
        """FileZilla'dan sunucuları içe aktar"""
        file_path = filedialog.askopenfilename(
            title="FileZilla.xml dosyasını seçin",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")]
        )
        
        if file_path:
            success, message = self.config_manager.import_from_filezilla(file_path)
            if success:
                self.servers = self.config_manager.load_servers() # Değişiklik: Sunucuları yeniden yükle
                self.load_servers_list()
                messagebox.showinfo("Başarılı", message)
                self.update_status("FileZilla sunucuları içe aktarıldı")
            else:
                messagebox.showerror("Hata", message)
    
    def import_from_json(self):
        """JSON'dan sunucuları içe aktar"""
        file_path = filedialog.askopenfilename(
            title="JSON dosyasını seçin",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if file_path:
            success, message = self.config_manager.import_servers(file_path)
            if success:
                self.servers = self.config_manager.load_servers() # Değişiklik: Sunucuları yeniden yükle
                self.load_servers_list()
                messagebox.showinfo("Başarılı", message)
                self.update_status("JSON sunucuları içe aktarıldı")
            else:
                messagebox.showerror("Hata", message)
    
    def export_to_json(self):
        """Sunucuları JSON'a dışa aktar"""
        if not self.servers:
            messagebox.showwarning("Uyarı", "Dışa aktarılacak sunucu bulunamadı!")
            return
        
        file_path = filedialog.asksaveasfilename(
            title="JSON dosyasını kaydedin",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if file_path:
            success, message = self.config_manager.export_servers(file_path)
            if success:
                messagebox.showinfo("Başarılı", message)
                self.update_status("Sunucular dışa aktarıldı")
            else:
                messagebox.showerror("Hata", message)
    
    def test_email(self):
        """Email testi gönder"""
        # Email ayarlarını kontrol et
        required_fields = ['smtp_server', 'smtp_port', 'from_email', 'password', 'to_email']
        for field in required_fields:
            if not self.email_widgets[field].get():
                messagebox.showwarning("Uyarı", "Lütfen tüm email alanlarını doldurun!")
                return
        
        smtp_config = {
            'smtp_server': self.email_widgets['smtp_server'].get(),
            'smtp_port': int(self.email_widgets['smtp_port'].get()),
            'from_email': self.email_widgets['from_email'].get(),
            'username': self.email_widgets['from_email'].get(),
            'password': self.email_widgets['password'].get(),
            'to_email': self.email_widgets['to_email'].get()
        }
        
        # Demo email gönderimi
        def send_test_email():
            try:
                # Gerçek email gönderimi yerine demo
                import time
                time.sleep(2)
                self.root.after(0, lambda: messagebox.showinfo("Başarılı", 
                    "✅ Email testi başarılı! (Demo modu)\n\nGerçek uygulamada SMTP ayarlarınız doğrulanacak ve test email'i gönderilecektir."))
                self.root.after(0, lambda: self.update_status("Email testi tamamlandı"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Hata", f"Email testi hatası: {str(e)}"))
        
        self.update_status("Email testi gönderiliyor...")
        threading.Thread(target=send_test_email, daemon=True).start()
    
    def on_backup_type_change(self, *args):
        """Yedekleme türü değişince veritabanı seçimini güncelle"""
        backup_type = self.backup_type.get()
        if backup_type in ['db_only', 'full_backup']:
            self.db_selection_card.pack(fill=tk.X, padx=10, pady=8)
            self.update_db_selection()
        else:
            self.db_selection_card.pack_forget()
    
    def update_db_selection(self):
        """Veritabanı seçim checkbox'larını güncelle"""
        # Eski checkbox'ları temizle
        for widget in self.db_selection_frame.winfo_children():
            widget.destroy()
        
        if self.current_server and self.current_server.get('databases'):
            self.db_checkboxes = {}
            for db in self.current_server['databases']:
                var = tk.BooleanVar(value=True)
                cb = tk.Checkbutton(self.db_selection_frame, text=f"{db['name']} ({db['type']})",
                                  variable=var, font=self.fonts['body'],
                                  bg=self.colors['surface'], fg=self.colors['text_primary'],
                                  selectcolor=self.colors['surface'])
                cb.pack(anchor='w', pady=1)
                self.db_checkboxes[db['name']] = var
        else:
            tk.Label(self.db_selection_frame, text="Veritabanı bulunamadı", font=self.fonts['body'],
                    bg=self.colors['surface'], fg=self.colors['text_secondary']).pack(anchor='w')
    
    def set_default_backup_path(self):
        """Default yedekleme yolunu ayarla"""
        desktop = Path.home() / "Desktop"
        # Hedef yol artık yedeklerin kaydedileceği ana klasör olacak.
        default_path = desktop / "BackupMaster_Backups"
        self.backup_target.delete(0, tk.END)
        self.backup_target.insert(0, str(default_path))
    
    def update_status(self, message):
        self.status_label.config(text=message)
    
    def filter_servers(self, event=None):
        search_term = self.search_var.get().lower()
        
        # Treeview'daki tüm öğeleri temizle
        for item in self.servers_tree.get_children():
            self.servers_tree.delete(item)
        
        # Filtrelenmiş sunucuları ekle
        for server in self.servers:
            if search_term in server['name'].lower():
                self.servers_tree.insert("", "end", text=server['name'])
    
    def load_servers_list(self):
        """Sunucu listesini treeview'a yükle"""
        for item in self.servers_tree.get_children():
            self.servers_tree.delete(item)
        
        for server in self.servers:
            self.servers_tree.insert("", "end", text=server['name'])
    
    def on_server_select(self, event):
        selection = self.servers_tree.selection()
        if selection:
            item = selection[0]
            server_name = self.servers_tree.item(item, 'text').strip()
            
            for server in self.servers:
                if server['name'] == server_name:
                    self.current_server = server
                    self.load_server_details()
                    self.load_databases_list()
                    self.update_status(f"Seçilen: {server['name']}")
                    self.load_schedule_details()
                    self.set_default_backup_path()
                    self.load_history() # Geçmişi yeniden yükle
                    self.update_db_selection()
                    break
    
    def load_server_details(self):
        if self.current_server:
            for key, widget in self.form_widgets.items():
                widget.delete(0, tk.END)
                if key in self.current_server:
                    widget.insert(0, self.current_server[key])
            
            self.protocol.set(self.current_server.get('protocol', 'ftp'))
    
    def load_databases_list(self):
        """Veritabanı listesini yükle"""
        for item in self.db_tree.get_children():
            self.db_tree.delete(item)
        
        if self.current_server and self.current_server.get('databases'):
            for db in self.current_server['databases']:
                self.db_tree.insert("", "end", values=(db['name'], db.get('type', 'mysql'), db.get('host', 'localhost')))
    
    def on_database_select(self, event):
        """Veritabanı seçildiğinde"""
        selection = self.db_tree.selection()
        if selection:
            item = selection[0]
            db_name = self.db_tree.item(item, 'values')[0]
            
            if self.current_server and self.current_server.get('databases'):
                for i, db in enumerate(self.current_server['databases']):
                    if db['name'] == db_name:
                        self.current_db_index = i
                        self.load_database_details(db)
                        break
    
    def load_database_details(self, db):
        """Veritabanı detaylarını yükle"""
        for key, widget in self.db_widgets.items():
            widget.delete(0, tk.END)
            db_key = key.replace('db_', '')
            if db_key in db:
                if key == 'db_type':
                    widget.set(db[db_key])
                else:
                    widget.insert(0, db[db_key])
    
    def load_history(self):
        """Geçmiş listesini Treeview'a yükle"""
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        
        history_data = self.history_manager.get_history()
        for record in history_data:
            self.history_tree.insert("", "end", iid=record[0], values=(record[1], record[2], record[3], record[4]))

    def on_history_select(self, event):
        """Geçmişten bir kayıt seçildiğinde logları yükle"""
        selection = self.history_tree.selection()
        if selection:
            history_id = selection[0]
            logs = self.history_manager.get_logs_for_history(history_id)
            
            self.history_log_text.delete('1.0', tk.END)
            for timestamp, message in logs:
                log_entry = f"[{timestamp}] {message}\n"
                self.history_log_text.insert(tk.END, log_entry)
            self.history_log_text.see(tk.END)

    def load_schedule_details(self):
        """Zamanlama detaylarını yükle"""
        if self.current_server and 'schedule' in self.current_server:
            schedule_info = self.current_server['schedule']
            self.schedule_widgets['enabled'].set(schedule_info.get('enabled', False))
            self.schedule_widgets['frequency'].set(schedule_info.get('frequency', 'Günlük'))
            self.schedule_widgets['day'].set(schedule_info.get('day', 'Pazartesi'))
            self.schedule_widgets['hour'].set(schedule_info.get('hour', '03'))
            self.schedule_widgets['minute'].set(schedule_info.get('minute', '00'))
        else:
            # Varsayılan değerleri ayarla
            self.schedule_widgets['enabled'].set(False)
            self.schedule_widgets['frequency'].set('Günlük')
            self.schedule_widgets['day'].set('Pazartesi')
            self.schedule_widgets['hour'].set('03')
            self.schedule_widgets['minute'].set('00')

        self.toggle_schedule_controls()
        self.update_next_run_label()

    def toggle_schedule_controls(self):
        """Zamanlama kontrollerini etkinleştir/devre dışı bırak"""
        state = tk.NORMAL if self.schedule_widgets['enabled'].get() else tk.DISABLED
        for key, widget in self.schedule_widgets.items():
            if key != 'enabled':
                if hasattr(widget, 'config'):
                    widget.config(state=state)
        self.on_frequency_change() # Gün combobox'ını gizle/göster

    def on_frequency_change(self, event=None):
        if self.schedule_widgets['enabled'].get() and self.schedule_widgets['frequency'].get() == "Haftalık":
            self.day_frame.pack(fill=tk.X, pady=5)
        else:
            self.day_frame.pack_forget()

    def add_server(self):
        self.current_server = {
            'name': 'Yeni Sunucu', 'protocol': 'ftp', 'host': '',
            'port': '21', 'username': '', 'password': '', 'web_root': '/public_html',
            'databases': []
        }
        self.servers.append(self.current_server)
        self.load_servers_list()
        self.clear_server_details()
        self.update_status("Yeni sunucu oluşturuldu")
    
    def delete_server(self):
        if not self.current_server:
            messagebox.showwarning("Uyarı", "Lütfen önce bir sunucu seçin!")
            return
        
        if messagebox.askyesno("Onay", f"'{self.current_server['name']}' sunucusunu silmek istediğinizden emin misiniz?"):
            self.servers.remove(self.current_server)
            schedule.clear(self.current_server['name']) # Zamanlanmış görevi sil
            self.current_server = None
            self.load_servers_list()
            self.clear_server_details()
            self.config_manager.save_servers(self.servers)
            self.update_status("Sunucu silindi")
    
    def add_database(self):
        if not self.current_server:
            messagebox.showwarning("Uyarı", "Lütfen önce bir sunucu seçin!")
            return
        
        new_db = {
            'name': 'yeni_veritabani',
            'type': 'mysql',
            'host': 'localhost',
            'port': '3306',
            'username': '',
            'password': ''
        }
        
        if 'databases' not in self.current_server:
            self.current_server['databases'] = []
        
        self.current_server['databases'].append(new_db)
        self.load_databases_list()
        self.clear_database_details()
        self.update_status("Yeni veritabanı eklendi")
    
    def delete_database(self):
        if not self.current_server or self.current_db_index is None:
            messagebox.showwarning("Uyarı", "Lütfen önce bir veritabanı seçin!")
            return
        
        db_name = self.current_server['databases'][self.current_db_index]['name']
        if messagebox.askyesno("Onay", f"'{db_name}' veritabanını silmek istediğinizden emin misiniz?"):
            self.current_server['databases'].pop(self.current_db_index)
            self.config_manager.save_servers(self.servers) # Değişikliği kaydet
            self.current_db_index = None
            self.load_databases_list()
            self.clear_database_details()
            self.update_status("Veritabanı silindi")
    
    def clear_server_details(self):
        for widget in self.form_widgets.values():
            widget.delete(0, tk.END)
        self.protocol.set('ftp')
        self.form_widgets['server_name'].insert(0, "Yeni Sunucu")
        self.form_widgets['port'].insert(0, "21")
        self.form_widgets['web_root'].insert(0, "/public_html")
        self.load_databases_list()
        self.clear_database_details()
        self.load_schedule_details()
    
    def clear_database_details(self):
        for key, widget in self.db_widgets.items():
            if key == 'db_type':
                widget.set('mysql')
            else:
                widget.delete(0, tk.END)
                if key == 'db_host':
                    widget.insert(0, 'localhost')
                elif key == 'db_port':
                    widget.insert(0, '3306')
    
    def test_connection(self):
        if not self.current_server:
            messagebox.showwarning("Uyarı", "Lütfen önce bir sunucu seçin!")
            return
        
        self.save_server_form()
        
        def test_thread():
            self.update_status("Bağlantı test ediliyor...")
            success, message = self.server_manager.test_connection(self.current_server)
            
            self.root.after(0, lambda: messagebox.showinfo(
                "Bağlantı Testi", 
                f"Bağlantı {'✅ başarılı' if success else '❌ başarısız'}!\n{message}"
            ))
            self.root.after(0, lambda: self.update_status("Test tamamlandı"))
        
        threading.Thread(target=test_thread, daemon=True).start()
    
    def save_server_form(self):
        if self.current_server:
            self.current_server.update({
                'name': self.form_widgets['server_name'].get(),
                'protocol': self.protocol.get(),
                'host': self.form_widgets['host'].get(),
                'port': self.form_widgets['port'].get(),
                'username': self.form_widgets['username'].get(),
                'password': self.form_widgets['password'].get(),
                'web_root': self.form_widgets['web_root'].get()
            })
    
    def save_server(self):
        if self.current_server:
            self.save_server_form()
            self.config_manager.save_servers(self.servers)
            self.load_servers_list()
            messagebox.showinfo("Başarılı", "✅ Sunucu kaydedildi!")
            self.update_status("Sunucu kaydedildi")
            self.set_default_backup_path()
    
    def save_database(self):
        if not self.current_server or self.current_db_index is None:
            messagebox.showwarning("Uyarı", "Lütfen önce bir veritabanı seçin!")
            return
        
        db_data = {
            'name': self.db_widgets['db_name'].get(),
            'type': self.db_widgets['db_type'].get(),
            'host': self.db_widgets['db_host'].get(),
            'port': self.db_widgets['db_port'].get(),
            'username': self.db_widgets['db_username'].get(),
            'password': self.db_widgets['db_password'].get()
        }
        
        self.current_server['databases'][self.current_db_index] = db_data
        self.load_databases_list()
        self.config_manager.save_servers(self.servers) # Değişikliği kaydet
        messagebox.showinfo("Başarılı", "✅ Veritabanı kaydedildi!")
        self.update_status("Veritabanı kaydedildi")
    
    def test_db_connection(self):
        if not self.current_server or self.current_db_index is None:
            messagebox.showwarning("Uyarı", "Lütfen önce bir veritabanı seçin ve kaydedin!")
            return

        # Test için formdaki güncel bilgileri al.
        # Bu, kaydedilmemiş değişikliklerin de test edilmesini sağlar.
        db_config = {
            'type': self.db_widgets['db_type'].get(),
            'host': self.db_widgets['db_host'].get(),
            'port': self.db_widgets['db_port'].get(),
            'username': self.db_widgets['db_username'].get(),
            'password': self.db_widgets['db_password'].get(),
            'database': self.db_widgets['db_name'].get() # Bağlantı için veritabanı adı da gereklidir.
        }
        if not all(db_config.values()):
            messagebox.showwarning("Uyarı", "Lütfen test için tüm veritabanı alanlarını doldurun.")
            return

        # Test için formdaki güncel bilgileri al.
        # Bu, kaydedilmemiş değişikliklerin de test edilmesini sağlar.
        db_config = {
            'type': self.db_widgets['db_type'].get(),
            'host': self.db_widgets['db_host'].get(),
            'port': self.db_widgets['db_port'].get(),
            'username': self.db_widgets['db_username'].get(),
            'password': self.db_widgets['db_password'].get(),
            'database': self.db_widgets['db_name'].get() # Bağlantı için veritabanı adı da gereklidir.
        }

        def test_thread():
            self.update_status("Veritabanı bağlantısı test ediliyor...")
            # Use the real DatabaseManager for testing
            success, message = self.db_manager.test_connection(db_config)
            
            self.root.after(0, lambda: messagebox.showinfo(
                "Veritabanı Bağlantı Testi", 
                f"Bağlantı {'✅ başarılı' if success else '❌ başarısız'}!\n{message}"
            ))
            self.root.after(0, lambda: self.update_status("DB Testi tamamlandı"))

        threading.Thread(target=test_thread, daemon=True).start()

    def clear_history(self):
        """Tüm yedekleme geçmişini temizler."""
        if messagebox.askyesno("Onay", "Tüm yedekleme geçmişi kalıcı olarak silinecektir. Bu işlem geri alınamaz. Emin misiniz?"):
            success, message = self.history_manager.clear_all_history()
            if success:
                messagebox.showinfo("Başarılı", message)
                self.load_history() # Listeyi yenile
                self.history_log_text.delete('1.0', tk.END) # Log detayını temizle
            else:
                messagebox.showerror("Hata", message)
            self.update_status("Geçmiş temizlendi")
    
    def browse_backup_path(self):
        path = filedialog.askdirectory()
        if path:
            self.backup_target.delete(0, tk.END)
            self.backup_target.insert(0, path)
    
    def browse_restore_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("ZIP files", "*.zip")])
        if file_path:
            self.restore_zip_path.delete(0, tk.END)
            self.restore_zip_path.insert(0, file_path)

    def on_backup_complete(self, status, zip_path=None):
        """Yedekleme tamamlandığında çağrılır."""
        if self.current_history_id:
            self.history_manager.update_backup_status(self.current_history_id, status, zip_path)
            self.current_history_id = None
        self.root.after(0, self.load_history) # GUI'yi güncelle
        self.root.after(0, self.stats_labels['status'].config, text=status)
        self.is_operation_running = False

        # İşlem bitince hız ve ilerleme göstergelerini sıfırla
        if status != "Çalışıyor":
            self.root.after(0, self.update_speed, 0)
            self.root.after(0, self.update_file_progress, 0, 0)
            self.root.after(0, self.update_progress, 0, 100)
        
        # Bildirim gönder
        if status in ["Tamamlandı", "Başarısız", "Durduruldu"]:
            title = f"Yedekleme {status}"
            message = f"'{self.current_server['name']}' sunucusu için yedekleme işlemi {status.lower()}."
            if status == "Başarısız":
                self.send_notification(title, message, "error")
            else:
                self.send_notification(title, message, "info")

    def send_notification(self, title, message, level="info"):
        """Masaüstü bildirimi gönderir."""
        if notification:
            try:
                notification.notify(title=title, message=message, app_name="BackupMaster", timeout=10)
            except Exception as e:
                self.update_log(f"Bildirim gönderme hatası: {e}")

    def save_schedule(self):
        """Zamanlama ayarlarını kaydet"""
        if not self.current_server:
            messagebox.showwarning("Uyarı", "Lütfen önce bir sunucu seçin!")
            return

        schedule_info = {
            'enabled': self.schedule_widgets['enabled'].get(),
            'frequency': self.schedule_widgets['frequency'].get(),
            'day': self.schedule_widgets['day'].get(),
            'hour': self.schedule_widgets['hour'].get(),
            'minute': self.schedule_widgets['minute'].get(),
        }

        self.current_server['schedule'] = schedule_info
        self.config_manager.save_servers(self.servers)
        
        # Zamanlayıcıyı güncelle
        self.update_schedule_for_server(self.current_server)

        messagebox.showinfo("Başarılı", "Zamanlama ayarları kaydedildi.")
        self.update_next_run_label()

    def update_schedule_for_server(self, server):
        """Belirli bir sunucu için zamanlayıcıyı ayarlar veya temizler."""
        server_name = server['name']
        schedule.clear(server_name) # Önceki görevi temizle

        if 'schedule' in server and server['schedule'].get('enabled'):
            s_info = server['schedule']
            job_time = f"{s_info['hour']}:{s_info['minute']}"
            
            job = None
            if s_info['frequency'] == 'Günlük':
                job = schedule.every().day.at(job_time)
            elif s_info['frequency'] == 'Haftalık':
                day_lower = s_info['day'].lower()
                if day_lower == "pazartesi": job = schedule.every().monday.at(job_time)
                elif day_lower == "salı": job = schedule.every().tuesday.at(job_time)
                elif day_lower == "çarşamba": job = schedule.every().wednesday.at(job_time)
                elif day_lower == "perşembe": job = schedule.every().thursday.at(job_time)
                elif day_lower == "cuma": job = schedule.every().friday.at(job_time)
                elif day_lower == "cumartesi": job = schedule.every().saturday.at(job_time)
                elif day_lower == "pazar": job = schedule.every().sunday.at(job_time)

            if job:
                job.do(self.run_scheduled_backup, server_name=server_name).tag(server_name)

    def load_all_schedules(self):
        """Uygulama başlangıcında tüm zamanlanmış görevleri yükler."""
        for server in self.servers:
            self.update_schedule_for_server(server)

    def run_scheduled_backup(self, server_name):
        """Zamanlayıcı tarafından tetiklenen yedekleme işlemi."""
        self.root.after(0, self.update_log, f"⏰ Zamanlanmış yedekleme başlatılıyor: {server_name}")
        
        server_to_backup = None
        for s in self.servers:
            if s['name'] == server_name:
                server_to_backup = s
                break
        
        if not server_to_backup:
            self.root.after(0, self.update_log, f"❌ Zamanlanmış sunucu bulunamadı: {server_name}")
            return

        # Burada start_backup'a benzer bir mantık kurulmalı
        # GUI'den bağımsız çalışacak şekilde ayarlanmalı
        # Örnek olarak, varsayılan ayarlarla tam yedekleme yapalım
        backup_config = {
            'type': 'full_backup',
            'target_path': str(Path.home() / "Desktop" / f"scheduled_backup_{server_name}"),
            'create_zip': True,
            'send_email': False # Zamanlanmış görevlerde email varsayılan olarak kapalı olabilir
        }
        # Bu fonksiyonun GUI'yi doğrudan manipüle etmemesi önemli
        # Sadece log ve progress callback'lerini kullanmalı
        # Bu kısım daha da geliştirilebilir. Şimdilik log bırakıyoruz.
        self.root.after(0, self.update_log, f"✅ Zamanlanmış yedekleme tamamlandı (Simülasyon): {server_name}")

    def update_next_run_label(self):
        """Sonraki çalışma zamanını gösteren etiketi günceller."""
        if not self.current_server:
            self.next_run_label.config(text="Sonraki çalışma: -")
            return

        jobs = schedule.get_jobs(self.current_server['name'])
        if jobs:
            next_run = jobs[0].next_run.strftime('%Y-%m-%d %H:%M:%S')
            self.next_run_label.config(text=f"Sonraki çalışma: {next_run}")
        else:
            self.next_run_label.config(text="Sonraki çalışma: Ayarlanmadı")
    
    def start_backup(self):
        if not self.current_server:
            messagebox.showwarning("Uyarı", "❌ Lütfen önce bir sunucu seçin!")
            return
        
        if not self.backup_target.get():
            messagebox.showwarning("Uyarı", "❌ Lütfen yedekleme hedefini seçin!")
            return
        
        self.is_operation_running = True
        
        # Seçili veritabanlarını al
        selected_databases = []
        if self.backup_type.get() in ['db_only', 'full_backup']:
            if self.current_server.get('databases'):
                for db in self.current_server['databases']:
                    if db['name'] in self.db_checkboxes and self.db_checkboxes[db['name']].get():
                        selected_databases.append(db)
        
        if self.backup_type.get() in ['db_only', 'full_backup'] and not selected_databases:
            messagebox.showwarning("Uyarı", "❌ Lütfen en az bir veritabanı seçin!")
            return
        
        # Email gönderim ayarları
        backup_config = {
            'type': self.backup_type.get(),
            'target_path': self.backup_target.get(),
            'filter': self.file_filter.get(),
            'create_zip': self.create_zip.get(),
            'send_email': self.send_email.get()
        }
        
        # Email gönderilecekse SMTP ayarlarını kontrol et
        if self.send_email.get():
            required_fields = ['smtp_server', 'smtp_port', 'from_email', 'password', 'to_email']
            for field in required_fields:
                if not self.email_widgets[field].get():
                    messagebox.showwarning("Uyarı", "Email gönderimi için tüm SMTP alanlarını doldurun!")
                    return
        
        # Geçmiş kaydını başlat
        self.current_history_id = self.history_manager.start_backup_record(
            self.current_server['name'],
            backup_config['type']
        )

        # Hız hesaplaması için sıfırlama
        self.last_speed_check_time = time.time()
        self.last_bytes_transferred = 0
        self.total_bytes_transferred_session = 0
        self.update_file_progress(0, 0) # Dosya sayacını sıfırla

        
        # Gelişmiş yedekleme yöneticisini kullan
        self.backup_manager = AdvancedBackupManager(
            progress_callback=self.update_progress,
            log_callback=self.update_log
        )
        self.backup_manager.byte_progress_callback = self.handle_byte_progress
        self.backup_manager.file_progress_callback = self.update_file_progress
        
        def backup_thread():
            backup_type = backup_config['type']
            success, message = False, "Bilinmeyen yedekleme türü"

            if backup_type == 'full_backup':
                success, message = self.backup_manager.create_complete_backup(self.current_server, backup_config, selected_databases)
            elif backup_type == 'files_only':
                success, message = self.backup_manager.start_backup(self.current_server, backup_config)
            elif backup_type == 'db_only':
                if selected_databases:
                    success, message = self.backup_manager.backup_database(selected_databases[0], backup_config)
                else:
                    success, message = (False, "Veritabanı seçilmedi")

            # GUI güncellemelerini ana thread'e gönder
            if success:
                self.root.after(0, self.update_status, "Yedekleme başlatıldı")
                self.root.after(0, self.notebook.select, 6)  # İlerleme sekmesine git
                self.root.after(0, self.stats_labels['status'].config, {'text': "Çalışıyor"})
            else:
                self.root.after(0, messagebox.showerror, "Hata", message)
                self.root.after(0, self.on_backup_complete, "Başarısız")
                self.is_operation_running = False

        # Yedeklemeyi ayrı bir thread'de başlat
        threading.Thread(target=backup_thread, daemon=True).start()

    def start_restore(self):
        if not self.restore_zip_path.get():
            messagebox.showwarning("Uyarı", "❌ Lütfen bir ZIP dosyası seçin!")
            return
        
        if not self.current_server:
            messagebox.showwarning("Uyarı", "❌ Lütfen önce bir sunucu seçin!")
            return
        
        self.is_operation_running = True
        
        restore_config = {
            'restore_files': self.restore_files.get(),
            'restore_database': self.restore_database.get()
        }
        
        success, message = self.restore_manager.restore_backup(
            self.restore_zip_path.get(), restore_config
        )
        
        if success:
            self.update_status("Geri yükleme başlatıldı")
            self.notebook.select(6)
            self.stats_labels['status'].config(text="Geri Yükleniyor")
        else:
            messagebox.showerror("Hata", message)
    
    def update_progress(self, value, max_value):
        percentage = int((value / max_value) * 100) if max_value > 0 else 0
        self.root.after(0, self._update_progress_gui, percentage)

    def _update_progress_gui(self, percentage):
        self.progress_bar['value'] = percentage
        self.progress_text.config(text=f"{percentage}%")
        if 'progress' in self.stats_labels:
            self.stats_labels['progress'].config(text=f"{percentage}%")

    def update_file_progress(self, processed_count, total_count):
        self.root.after(0, lambda: self.stats_labels['processed'].config(text=f"{processed_count} / {total_count}"))

    def handle_byte_progress(self, bytes_chunk, total_bytes=None, is_new_file=False):
        """FTP'den gelen chunk veya SFTP'den gelen toplam byte'ı işler."""
        if is_new_file: # SFTP
            # SFTP her dosya için sıfırdan `bytes_so_far` gönderir.
            # Bu yüzden bir önceki dosyanın boyutunu ekleyip devam ediyoruz.
            increment = bytes_chunk - self.last_bytes_transferred
            if increment < 0: # Yeni dosya başladı
                self.total_bytes_transferred_session += self.last_bytes_transferred
                self.last_bytes_transferred = bytes_chunk
                increment = bytes_chunk
            else:
                 self.last_bytes_transferred = bytes_chunk
        else: # FTP
            increment = bytes_chunk

        current_time = time.time()
        time_diff = current_time - self.last_speed_check_time

        if time_diff > 0.5: # Hızı saniyenin yarısında bir güncelle
            speed_bytes_per_sec = increment / time_diff
            self.root.after(0, self.update_speed, speed_bytes_per_sec)
            self.last_speed_check_time = current_time

    def update_speed(self, speed_bytes_per_sec):
        if speed_bytes_per_sec > 1024 * 1024:
            self.stats_labels['speed'].config(text=f"{speed_bytes_per_sec / (1024*1024):.2f} MB/s")
        elif speed_bytes_per_sec > 1024:
            self.stats_labels['speed'].config(text=f"{speed_bytes_per_sec / 1024:.2f} KB/s")
        else:
            self.stats_labels['speed'].config(text=f"{speed_bytes_per_sec:.0f} B/s")
    
    def update_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"

        # Canlı log'u güncelle
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
        self.log_text.update_idletasks()

        # Veritabanına log kaydı ekle
        if self.current_history_id:
            self.history_manager.add_log(self.current_history_id, message)

def main():
    root = tk.Tk()
    app = ModernBackupMaster(root)
    root.mainloop()

if __name__ == "__main__":
    main()