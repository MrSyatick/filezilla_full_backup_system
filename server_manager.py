import ftplib
import paramiko
import os
from datetime import datetime

class ServerManager:
    def __init__(self):
        self.ftp_connections = {}
        self.sftp_connections = {}
    
    def test_connection(self, server_info):
        try:
            if server_info['protocol'] == 'ftp':
                return self._test_ftp(server_info)
            else:
                return self._test_sftp(server_info)
        except Exception as e:
            return False, str(e)
    
    def _test_ftp(self, server_info):
        try:
            ftp = ftplib.FTP()
            ftp.connect(server_info['host'], int(server_info['port']))
            ftp.login(server_info['username'], server_info['password'])
            ftp.quit()
            return True, "Bağlantı başarılı!"
        except Exception as e:
            return False, str(e)
    
    def _test_sftp(self, server_info):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                server_info['host'],
                port=int(server_info['port']),
                username=server_info['username'],
                password=server_info['password']
            )
            sftp = ssh.open_sftp()
            sftp.close()
            ssh.close()
            return True, "Bağlantı başarılı!"
        except Exception as e:
            return False, str(e)