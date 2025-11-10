import json
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import xml.etree.ElementTree as ET

class ConfigManager:
    def __init__(self):
        self.config_dir = os.path.join(os.path.expanduser("~"), ".backupmaster")
        self.config_file = os.path.join(self.config_dir, "config.json")
        self.servers_file = os.path.join(self.config_dir, "servers.enc")
        self.key_file = os.path.join(self.config_dir, "key.key")
        
        os.makedirs(self.config_dir, exist_ok=True)
        self._ensure_key()
    
    def _ensure_key(self):
        if not os.path.exists(self.key_file):
            key = Fernet.generate_key()
            with open(self.key_file, 'wb') as f:
                f.write(key)
    
    def _get_cipher(self):
        with open(self.key_file, 'rb') as f:
            key = f.read()
        return Fernet(key)
    
    def save_servers(self, servers):
        cipher = self._get_cipher()
        encrypted_data = cipher.encrypt(json.dumps(servers).encode())
        with open(self.servers_file, 'wb') as f:
            f.write(encrypted_data)
    
    def load_servers(self):
        if not os.path.exists(self.servers_file):
            return []
        
        cipher = self._get_cipher()
        with open(self.servers_file, 'rb') as f:
            encrypted_data = f.read()
        
        try:
            decrypted_data = cipher.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except:
            return []

    def export_servers(self, filepath):
        """Sunucuları dışa aktar (şifresiz JSON olarak)"""
        try:
            servers = self.load_servers()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(servers, f, ensure_ascii=False, indent=2)
            return True, "Sunucular başarıyla dışa aktarıldı"
        except Exception as e:
            return False, f"Dışa aktarma hatası: {str(e)}"

    def import_servers(self, filepath):
        """Sunucuları içe aktar (şifresiz JSON'dan)"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                imported_servers = json.load(f)
            
            current_servers = self.load_servers()
            # Mevcut sunuculara ekle (çakışmaları önle)
            for server in imported_servers:
                if server not in current_servers:
                    current_servers.append(server)
            
            self.save_servers(current_servers)
            return True, f"{len(imported_servers)} sunucu başarıyla içe aktarıldı"
        except Exception as e:
            return False, f"İçe aktarma hatası: {str(e)}"

    def import_from_filezilla(self, filepath):
        """FileZilla'dan sunucuları içe aktar"""
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            current_servers = self.load_servers()
            imported_count = 0
            for server_elem in root.findall('.//Server'):
                server = {
                    'name': server_elem.find('Name').text if server_elem.find('Name') is not None else 'FileZilla_Server',
                    'host': server_elem.find('Host').text if server_elem.find('Host') is not None else '',
                    'port': server_elem.find('Port').text if server_elem.find('Port') is not None else '21',
                    'protocol': 'ftp', # FileZilla XML'de protokol tipi genellikle belirtilmez, FTP varsayalım
                    'username': server_elem.find('User').text if server_elem.find('User') is not None else '',
                    'password': server_elem.find('Pass').text if server_elem.find('Pass') is not None else '',
                    'web_root': server_elem.find('RemoteDir').text if server_elem.find('RemoteDir') is not None else '/',
                    'databases': []
                }
                
                # Benzersiz isim oluştur
                original_name = server['name']
                counter = 1
                while any(s['name'] == server['name'] for s in current_servers):
                    server['name'] = f"{original_name}_{counter}"
                    counter += 1
                
                current_servers.append(server)
                imported_count += 1
            
            if imported_count > 0:
                self.save_servers(current_servers)
                return True, f"FileZilla'dan {imported_count} sunucu içe aktarıldı"
            else:
                return False, "FileZilla dosyasında sunucu bulunamadı"
                
        except Exception as e:
            return False, f"FileZilla içe aktarma hatası: {str(e)}"