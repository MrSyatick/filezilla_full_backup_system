import os
import shutil
import threading
from datetime import datetime
import ftplib
import paramiko
import time
import fnmatch
from pathlib import Path
import zipfile
import mysql.connector
import subprocess
import tempfile

class BackupManager:
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.is_running = False
        self.current_operation = ""
    
    def _log(self, message):
        """Log mesajını callback fonksiyonu ile ilet"""
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception as e:
                print(f"Log hatası: {e}")
    
    def _progress(self, value, max_value):
        """İlerleme durumunu callback fonksiyonu ile ilet"""
        if self.progress_callback:
            try:
                self.progress_callback(value, max_value)
            except Exception as e:
                print(f"Progress hatası: {e}")
    
    def stop_backup(self):
        """Yedeklemeyi durdur"""
        self.is_running = False
        self._log("⏹️ Yedekleme kullanıcı tarafından durduruldu!")
        if hasattr(self, 'on_complete_callback'):
            self.on_complete_callback("Durduruldu") # on_complete_callback'i çağır
    
    def _backup_thread(self, server_info, backup_config):
        """Yedekleme işlemini yöneten ana thread"""
        try:
            self._log("🚀 Yedekleme işlemi başlatılıyor...")
            self._progress(5, 100)
            
            # Bağlantıyı test et
            self._log("🔗 Sunucuya bağlanılıyor...")
            self._progress(10, 100)
            
            if server_info['protocol'] == 'ftp':
                success, conn = self._connect_ftp(server_info)
            else:
                success, conn = self._connect_sftp(server_info)
            
            if not success:
                self._log(f"❌ Bağlantı hatası: {conn}")
                self._progress(0, 100)
                return
            
            self._log("✅ Sunucu bağlantısı başarılı!")
            self._progress(20, 100)
            
            # Yedekleme işlemini gerçekleştir
            if server_info['protocol'] == 'ftp':
                self._perform_ftp_backup(conn, server_info, backup_config)
            else:
                self._perform_sftp_backup(conn, server_info, backup_config)
            
            # Bağlantıyı kapat
            if server_info['protocol'] == 'ftp':
                conn.quit()
            else:
                conn.close()
            
            if self.is_running:
                self._log("✅ Yedekleme başarıyla tamamlandı!")
                self._progress(100, 100)
                if hasattr(self, 'on_complete_callback'):
                    self.on_complete_callback("Tamamlandı")
            
        except Exception as e:
            self._log(f"❌ Beklenmeyen hata: {str(e)}")
            self._progress(0, 100)
            if hasattr(self, 'on_complete_callback'):
                self.on_complete_callback("Başarısız")
        finally:
            self.is_running = False
    
    def _connect_ftp(self, server_info):
        """FTP bağlantısı kur"""
        try:
            ftp = ftplib.FTP()
            ftp.connect(server_info['host'], int(server_info['port']))
            ftp.login(server_info['username'], server_info['password'])
            return True, ftp
        except Exception as e:
            return False, str(e)
    
    def _connect_sftp(self, server_info):
        """SFTP bağlantısı kur"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                server_info['host'],
                port=int(server_info['port']),
                username=server_info['username'],
                password=server_info['password'],
                timeout=30
            )
            sftp = ssh.open_sftp()
            return True, sftp
        except Exception as e:
            return False, str(e)
    
    def _perform_ftp_backup(self, ftp, server_info, backup_config):
        """FTP yedekleme işlemini gerçekleştir"""
        try:
            # Yedekleme dizinini oluştur
            backup_path = self._create_backup_path(backup_config['target_path'])

            # Dosya ve klasörleri recursive olarak yedekle
            self._log("📁 Dosya ve klasörler taranıyor...")
            self._progress(25, 100)
            
            # Tüm dosya ve klasörleri listele
            all_items = self._ftp_list_recursive(ftp, "")
            
            if not all_items:
                self._log("ℹ️ Sunucuda dosya/klasör bulunamadı")
                return

            self._log(f"📊 {len(all_items)} öğe bulundu. İndirme işlemi başlıyor...")
            self._download_items_ftp(ftp, all_items, backup_path, backup_config.get('filter', '*.*'))

            self._progress(90, 100)

        except Exception as e:
            raise Exception(f"FTP yedekleme hatası: {str(e)}")
    
    def _perform_sftp_backup(self, sftp, server_info, backup_config):
        """SFTP yedekleme işlemini gerçekleştir"""
        try:
            # Yedekleme dizinini oluştur
            backup_path = self._create_backup_path(backup_config['target_path'])

            # Dosya ve klasörleri recursive olarak yedekle
            self._log("📁 Dosya ve klasörler taranıyor ve indiriliyor...")
            self._progress(25, 100)
            
            # Tüm dosya ve klasörleri listele
            all_items = self._sftp_list_recursive(sftp, ".")
            
            if not all_items:
                self._log("ℹ️ Sunucuda dosya/klasör bulunamadı")
                return

            self._log(f"📊 {len(all_items)} öğe bulundu. İndirme işlemi başlıyor...")
            self._download_items_sftp(sftp, all_items, backup_path, backup_config.get('filter', '*.*'))

            self._progress(90, 100)
            
        except Exception as e:
            raise Exception(f"SFTP yedekleme hatası: {str(e)}")
    
    def _ftp_list_recursive(self, ftp, path):
        """FTP'de recursive dosya listesi al"""
        items = []
        self._log(f"🔎 Taranıyor: {path if path else '/'}")
        try:
            # Geçerli dizindeki öğeleri listele
            lines = []
            ftp.retrlines(f'LIST {path}', lines.append)
            
            for line in lines:
                parts = line.split()
                if len(parts) < 9:
                    continue
                
                # İzinleri ve ismi al
                permissions = parts[0]
                name = ' '.join(parts[8:])
                
                if name in ['.', '..']:
                    continue
                
                full_path = os.path.join(path, name).replace('\\', '/')
                
                # Klasör mü dosya mı kontrol et
                if permissions.startswith('d'):
                    # Klasör
                    items.append((full_path, True))
                    # Recursive olarak alt klasörleri listele
                    items.extend(self._ftp_list_recursive(ftp, full_path))
                else:
                    # Dosya
                    items.append((full_path, False))
                    
        except Exception as e:
            self._log(f"⚠️ Liste alınırken hata: {str(e)}")
        
        return items
    
    def _sftp_list_recursive(self, sftp, path):
        """SFTP'de recursive dosya listesi al"""
        items = []
        self._log(f"🔎 Taranıyor: {path if path else '/'}")
        try:
            for item in sftp.listdir(path):
                full_path = os.path.join(path, item).replace('\\', '/')
                
                try:
                    # Öğenin türünü kontrol et
                    stat = sftp.stat(full_path)
                    
                    if stat.st_mode & 0o40000:  # Klasör
                        items.append((full_path, True))
                        # Recursive olarak alt klasörleri listele
                        items.extend(self._sftp_list_recursive(sftp, full_path))
                    else:  # Dosya
                        items.append((full_path, False))
                        
                except Exception:
                    # Erişim hatası olabilir, devam et
                    continue
                    
        except Exception as e:
            self._log(f"⚠️ Liste alınırken hata: {str(e)}")
        
        return items
    
    def _download_items_ftp(self, ftp, items, backup_path, file_filter):
        """FTP'den öğeleri indirir."""
        filtered_items = self._filter_items(items, file_filter)
        total_items = len(filtered_items)
        downloaded_count = 0

        self._log(f"⬇️ {total_items} öğe indirilecek...")

        for i, (item_path, is_dir) in enumerate(filtered_items):
            if not self.is_running:
                break
            
            try:
                local_path = os.path.join(backup_path, item_path)
                
                if is_dir:
                    os.makedirs(local_path, exist_ok=True)
                    self._log(f"📁 Klasör oluşturuldu: {item_path}")
                else:
                    local_dir = os.path.dirname(local_path)
                    os.makedirs(local_dir, exist_ok=True)
                    
                    def ftp_callback(data):
                        local_file.write(data)
                        if hasattr(self, 'byte_progress_callback') and self.byte_progress_callback:
                            self.byte_progress_callback(len(data))

                    self._log(f"📥 İndiriliyor: {item_path}")
                    with open(local_path, 'wb') as local_file:
                        ftp.retrbinary(f'RETR {item_path}', ftp_callback)
                
                downloaded_count += 1

                if hasattr(self, 'file_progress_callback') and self.file_progress_callback:
                    self.file_progress_callback(downloaded_count, total_items)
                
                progress = 25 + (i / total_items) * 65
                self._progress(int(progress), 100)
                
            except Exception as e:
                self._log(f"⚠️ {item_path} işlenemedi: {str(e)}")
        
        self._log(f"✅ {downloaded_count}/{total_items} öğe başarıyla işlendi.")

    def _download_items_sftp(self, sftp, items, backup_path, file_filter):
        """SFTP'den öğeleri indirir."""
        filtered_items = self._filter_items(items, file_filter)
        total_items = len(filtered_items)
        downloaded_count = 0

        self._log(f"⬇️ {total_items} öğe indirilecek...")

        for i, (item_path, is_dir) in enumerate(filtered_items):
            if not self.is_running:
                break
            
            try:
                safe_item_path = item_path.lstrip('/\\')
                local_path = os.path.join(backup_path, safe_item_path)
                
                if is_dir:
                    os.makedirs(local_path, exist_ok=True)
                    self._log(f"📁 Klasör oluşturuldu: {item_path}")
                else:
                    local_dir = os.path.dirname(local_path)
                    os.makedirs(local_dir, exist_ok=True)
                    
                    def sftp_callback(bytes_so_far, total_bytes):
                        if hasattr(self, 'byte_progress_callback') and self.byte_progress_callback:
                            self.byte_progress_callback(bytes_so_far, total_bytes, is_new_file=True)

                    self._log(f"📥 İndiriliyor: {item_path}")
                    sftp.get(item_path, local_path, callback=sftp_callback)
                
                downloaded_count += 1
                
                if hasattr(self, 'file_progress_callback') and self.file_progress_callback:
                    self.file_progress_callback(downloaded_count, total_items)

                progress = 25 + (i / total_items) * 65
                self._progress(int(progress), 100)
                
            except Exception as e:
                self._log(f"⚠️ {item_path} işlenemedi: {str(e)}")
        
        self._log(f"✅ {downloaded_count}/{total_items} öğe başarıyla işlendi.")

    def _filter_items(self, items, file_filter):
        """Öğeleri filtrele"""
        if file_filter == '*.*' or not file_filter:
            return items
        
        filtered = []
        for item_path, is_dir in items:
            if is_dir:
                # Klasörleri her zaman dahil et
                filtered.append((item_path, is_dir))
            else:
                # Dosyaları filtrele
                filename = os.path.basename(item_path)
                if fnmatch.fnmatch(filename, file_filter):
                    filtered.append((item_path, is_dir))
        
        return filtered
    
    def _create_backup_path(self, base_path):
        """Yedekleme dizinini oluştur"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(base_path, f"backup_{timestamp}")
        
        os.makedirs(backup_path, exist_ok=True)
        self._log(f"📂 Yedekleme dizini: {backup_path}")
        
        return backup_path


class DatabaseManager:
    """Veritabanı yedekleme sınıfı"""
    
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
    
    def _log(self, message):
        if self.log_callback:
            try:
                self.log_callback(message)
            except:
                pass
    
    def backup_mysql(self, db_config, backup_path):
        """MySQL veritabanı yedekle"""
        # mysqldump komutunun sistemde var olup olmadığını kontrol et
        if not shutil.which("mysqldump"):
            error_msg = "❌ 'mysqldump' komutu bulunamadı. Lütfen MySQL/MariaDB client araçlarının yüklü ve sistem PATH'inde olduğundan emin olun."
            self._log(error_msg)
            return False, error_msg

        try:
            self._log("🗄️ MySQL veritabanı yedekleniyor...")
            
            # Bağlantı kur
            conn = mysql.connector.connect(
                host=db_config['host'],
                port=int(db_config.get('port', 3306)),
                user=db_config['username'],
                password=db_config['password'],
                database=db_config['database']
            )
            
            # Yedekleme dosyası
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(backup_path, f"mysql_backup_{timestamp}.sql")
            
            # mysqldump kullanarak yedek al
            cmd = [
                'mysqldump',
                f"--host={db_config['host']}",
                f"--port={db_config.get('port', 3306)}",
                f"--user={db_config['username']}",
                f"--password={db_config['password']}",
                db_config['database']
            ]
            
            with open(backup_file, 'w', encoding='utf-8') as f:
                subprocess.run(cmd, stdout=f, check=True)
            
            conn.close()
            self._log(f"✅ MySQL yedekleme tamamlandı: {backup_file}")
            return True, backup_file
            
        except Exception as e:
            self._log(f"❌ MySQL yedekleme hatası: {str(e)}")
            return False, str(e)
    
    def backup_postgresql(self, db_config, backup_path):
        """PostgreSQL veritabanı yedekle"""
        # pg_dump komutunun sistemde var olup olmadığını kontrol et
        if not shutil.which("pg_dump"):
            error_msg = "❌ 'pg_dump' komutu bulunamadı. Lütfen PostgreSQL client araçlarının yüklü ve sistem PATH'inde olduğundan emin olun."
            self._log(error_msg)
            return False, error_msg

        try:
            self._log("🗄️ PostgreSQL veritabanı yedekleniyor...")
            
            # Yedekleme dosyası
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(backup_path, f"pgsql_backup_{timestamp}.sql")
            
            # pg_dump kullanarak yedek al
            env = os.environ.copy()
            env['PGPASSWORD'] = db_config['password']
            
            cmd = [
                'pg_dump',
                f"--host={db_config['host']}",
                f"--port={db_config.get('port', 5432)}",
                f"--username={db_config['username']}",
                f"--dbname={db_config['database']}",
                "--file", backup_file
            ]
            
            subprocess.run(cmd, env=env, check=True)
            
            self._log(f"✅ PostgreSQL yedekleme tamamlandı: {backup_file}")
            return True, backup_file
            
        except Exception as e:
            self._log(f"❌ PostgreSQL yedekleme hatası: {str(e)}")
            return False, str(e)

    def test_connection(self, db_config):
        """Veritabanı bağlantısını test et"""
        db_type = db_config.get('type')
        try:
            if db_type == 'mysql':
                conn = mysql.connector.connect(
                    host=db_config['host'],
                    port=int(db_config.get('port', 3306)),
                    user=db_config['username'],
                    password=db_config['password']
                )
                if db_config.get('database'):
                    conn.database = db_config['database']
                conn.close()
                return True, "MySQL bağlantısı başarılı!"
            elif db_type == 'postgresql':
                # pg_isready komutunun sistemde var olup olmadığını kontrol et
                if not shutil.which("pg_isready"):
                    error_msg = "❌ 'pg_isready' komutu bulunamadı. Lütfen PostgreSQL client araçlarının yüklü ve sistem PATH'inde olduğundan emin olun."
                    self._log(error_msg)
                    return False, error_msg

                # pg_isready kullanarak sunucunun çalışıp çalışmadığını kontrol et
                env = os.environ.copy()
                env['PGPASSWORD'] = db_config['password']
                cmd = [
                    'pg_isready',
                    f"--host={db_config['host']}",
                    f"--port={db_config.get('port', 5432)}",
                    f"--username={db_config['username']}",
                    f"--dbname={db_config.get('database', '')}"
                ]
                result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                if result.returncode == 0:
                    return True, "PostgreSQL bağlantısı başarılı!"
                else:
                    # Çıktıda genellikle "accepting connections" yazar. Hata varsa stderr'den alınabilir.
                    return False, f"PostgreSQL bağlantı hatası: {result.stderr.strip() or result.stdout.strip()}"
            else:
                return False, f"Desteklenmeyen veritabanı türü: {db_type}"
        except Exception as e:
            return False, f"{db_type.capitalize() if db_type else 'Veritabanı'} bağlantı hatası: {str(e)}"


class ArchiveManager:
    """ZIP arşivleme sınıfı"""
    
    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
    
    def _log(self, message):
        if self.log_callback:
            try:
                self.log_callback(message)
            except:
                pass
    
    def create_zip_archive(self, source_paths, output_zip):
        """Birden fazla kaynağı ZIP arşivine dönüştür"""
        try:
            self._log("🗜️ ZIP arşivi oluşturuluyor...")
            
            with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
                total_files = self._count_files(source_paths)
                processed_files = 0
                
                for source_path in source_paths:
                    if os.path.isdir(source_path):
                        # Klasörü ZIP'e ekle
                        for root, dirs, files in os.walk(source_path):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, os.path.dirname(source_path))
                                zipf.write(file_path, arcname)
                                
                                processed_files += 1
                                if processed_files % 10 == 0:  # Her 10 dosyada bir log
                                    self._log(f"📦 {processed_files}/{total_files} dosya arşive eklendi...")
                    
                    elif os.path.isfile(source_path):
                        # Tek dosyayı ZIP'e ekle
                        arcname = os.path.basename(source_path)
                        zipf.write(source_path, arcname)
                        processed_files += 1
                        self._log(f"📦 Veritabanı yedeği arşive eklendi: {arcname}")
            
            self._log(f"✅ ZIP arşivi oluşturuldu: {output_zip}")
            return True, output_zip
            
        except Exception as e:
            self._log(f"❌ ZIP oluşturma hatası: {str(e)}")
            return False, str(e)
    
    def _count_files(self, source_paths):
        """Toplam dosya sayısını hesapla"""
        count = 0
        for path in source_paths:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    count += len(files)
            elif os.path.isfile(path):
                count += 1
        return count


class AdvancedBackupManager(BackupManager):
    """Gelişmiş yedekleme özellikleri"""
    
    def __init__(self, progress_callback=None, log_callback=None):
        super().__init__(progress_callback, log_callback)
        self.db_manager = DatabaseManager(progress_callback, log_callback)
        self.archive_manager = ArchiveManager(progress_callback, log_callback)
        self.backup_history = []

    def create_complete_backup(self, server_info, backup_config, db_configs=None):
        """Yedeklemeyi başlat (dosya, db veya tam)"""
        if self.is_running:
            return False, "Zaten bir yedekleme çalışıyor!"

        self.is_running = True
        thread = threading.Thread(target=self._create_complete_backup_thread, args=(server_info, backup_config, db_configs))
        thread.daemon = True
        thread.start()
        return True, "Yedekleme başlatıldı!"


    def _create_complete_backup_thread(self, server_info, backup_config, db_configs=None):
        """Gelişmiş yedekleme işlemini yöneten ana thread"""
        try:
            backup_type = backup_config.get('type', 'files_only')
            self._log(f"🚀 Yedekleme işlemi başlatılıyor: {backup_type}")

            # Yedekleme için ana dizini oluştur
            backup_path = self._create_backup_path(backup_config['target_path'])
            
            # Dosyaların indirileceği alt klasör (eğer dosya yedeklemesi varsa)
            files_backup_path = os.path.join(backup_path, "files")
            
            db_backups = []
            has_critical_error = False

            # 1. Dosyaları yedekle (eğer isteniyorsa)
            if backup_type in ['files_only', 'full_backup']:
                self._log("🔗 Sunucuya bağlanılıyor...")
                self._progress(10, 100)
                
                if server_info['protocol'] == 'ftp':
                    success, conn = self._connect_ftp(server_info)
                else:
                    success, conn = self._connect_sftp(server_info)
                
                if not success:
                    raise Exception(f"Bağlantı hatası: {conn}")
                
                self._log("✅ Sunucu bağlantısı başarılı!")
                self._progress(20, 100)
                
                # Dosyaları `files_backup_path` içine indir
                os.makedirs(files_backup_path, exist_ok=True)
                if server_info['protocol'] == 'ftp': self._perform_ftp_backup(conn, server_info, {'target_path': files_backup_path, 'filter': backup_config.get('filter', '*.*')})
                else: self._perform_sftp_backup(conn, server_info, {'target_path': files_backup_path, 'filter': backup_config.get('filter', '*.*')})
                
                if server_info['protocol'] == 'ftp': conn.quit()
                else: conn.close()

            # 2. Veritabanlarını yedekle (eğer isteniyorsa)
            if backup_type in ['db_only', 'full_backup'] and db_configs:
                self._log("🗄️ Veritabanı yedeklemeleri başlıyor...")
                for db_config in db_configs:
                    if not self.is_running: break
                    
                    # Veritabanı yedeklemesini doğrudan ana yedekleme klasörüne yap
                    if db_config['type'] == 'mysql':
                        success, result = self.db_manager.backup_mysql(db_config, backup_path)
                    elif db_config['type'] == 'postgresql':
                        success, result = self.db_manager.backup_postgresql(db_config, backup_path)
                    
                    if success:
                        db_backups.append(result)
                    else:
                        self._log(f"❌ {db_config['name']} veritabanı yedeklenemedi: {result}")
                        has_critical_error = True

            if has_critical_error:
                raise Exception("Kritik bir veritabanı yedekleme hatası oluştu. İşlem durduruluyor.")
            if not self.is_running:
                raise Exception("İşlem durduruldu.")

            # 3. ZIP arşivi oluştur (eğer isteniyorsa)
            if backup_config.get('create_zip', False):
                self._progress(95, 100)
                zip_filename = f"backup_{os.path.basename(backup_path)}.zip"
                zip_output_path = os.path.join(os.path.dirname(backup_path), zip_filename)
                
                # Arşivlenecek kaynakları topla
                sources_to_archive = []
                if os.path.exists(files_backup_path) and os.listdir(files_backup_path):
                    sources_to_archive.append(files_backup_path)
                sources_to_archive.extend(db_backups)

                success, result = self.archive_manager.create_zip_archive(sources_to_archive, zip_output_path)
                
                if success:
                    self._log(f"🧹 Geçici dosyalar temizleniyor...")
                    # Ana yedekleme klasörünü ve içindekileri sil
                    if os.path.exists(backup_path): 
                        shutil.rmtree(backup_path)
            
            if self.is_running:
                self._log("✅ Yedekleme başarıyla tamamlandı!")
                self._progress(100, 100)
                if hasattr(self, 'on_complete_callback'): self.on_complete_callback("Tamamlandı")
        except Exception as e:
            self._log(f"❌ Beklenmeyen hata: {str(e)}")
            if hasattr(self, 'on_complete_callback'): self.on_complete_callback("Başarısız")
        finally:
            self.is_running = False


# Demo modu için basit bir yedekleyici
class DemoBackupManager(BackupManager):
    """Demo amaçlı basit yedekleyici - Gerçek bağlantı yapmaz"""
    
    def _backup_thread(self, server_info, backup_config):
        """Demo yedekleme işlemi"""
        try:
            steps = [
                ("🚀 Yedekleme başlatılıyor...", 5),
                ("🔗 Sunucuya bağlanılıyor...", 10),
                ("✅ Bağlantı başarılı!", 20),
                ("📁 Dosya ve klasörler taranıyor...", 30),
                ("📊 87 dosya ve 15 klasör bulundu", 40),
                ("⬇️ Dosya ve klasörler indiriliyor...", 50),
                ("📁 css/ klasörü oluşturuldu", 55),
                ("📁 images/ klasörü oluşturuldu", 60),
                ("📥 index.php indiriliyor...", 65),
                ("📥 style.css indiriliyor...", 70),
                ("📥 app.js indiriliyor...", 75),
                ("📁 admin/ klasörü oluşturuldu", 80),
                ("📥 admin/dashboard.php indiriliyor...", 85),
                ("✅ 82/87 öğe başarıyla yedeklendi", 90),
                ("📂 Yedekleme tamamlanıyor...", 95),
                ("✅ Demo yedekleme başarıyla tamamlandı!", 100)
            ]
            
            for message, progress in steps:
                if not self.is_running:
                    self._log("⏹️ Yedekleme kullanıcı tarafından durduruldu!")
                    break
                
                self._log(message)
                self._progress(progress, 100)
                time.sleep(1)
            
        except Exception as e:
            self._log(f"❌ Demo hatası: {str(e)}")
        finally:
            self.is_running = False


# Kullanım örneği
if __name__ == "__main__":
    def demo_log(message):
        print(f"[LOG] {message}")
    
    def demo_progress(value, max_value):
        print(f"[PROGRESS] {value}/{max_value}")
    
    # Demo
    manager = DemoBackupManager(demo_progress, demo_log)
    
    server_info = {
        'name': 'Demo Server',
        'protocol': 'ftp',
        'host': 'localhost',
        'port': '21',
        'username': 'user',
        'password': 'pass'
    }
    
    backup_config = {
        'type': 'Tam Yedek',
        'target_path': './backups',
        'filter': '*.*'
    }
    
    print("Demo yedekleme başlatılıyor...")
    manager.start_backup(server_info, backup_config)
    
    # Ana thread'in bitmesini bekle
    time.sleep(20)