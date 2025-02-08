import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
import os

class CLIStreamHandler(logging.StreamHandler):
    """Handler customizado que evita interferir com a CLI"""
    def __init__(self, cli_prompt):
        super().__init__(sys.stdout)
        self.cli_prompt = cli_prompt
        self.lock = threading.Lock()

    def emit(self, record):
        with self.lock:
            try:
                # Limpa a linha atual
                sys.stdout.write('\r' + ' ' * (len(self.cli_prompt) + 100) + '\r')
                
                # Emite o log
                msg = self.format(record)
                sys.stdout.write(msg + '\n')
                
                # Reescreve o prompt e o texto atual
                if hasattr(threading.current_thread(), 'cli_input'):
                    sys.stdout.write(self.cli_prompt + threading.current_thread().cli_input)
                else:
                    sys.stdout.write(self.cli_prompt)
                    
                sys.stdout.flush()
            except Exception:
                self.handleError(record)

def setup_logging(log_dir='logs'):
    """Configura o sistema de logging de forma mais simples"""
    # Cria diretório de logs se não existir
    os.makedirs(log_dir, exist_ok=True)
    
    # Configura o formato dos logs
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # Handler para arquivo (logs detalhados)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'p2p.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Handler para console (logs importantes)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configura o logger root
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Remove handlers existentes
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Adiciona os novos handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return root_logger