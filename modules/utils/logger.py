import logging
import sys
from pathlib import Path
from datetime import datetime

from services._common import mw_home

class ModelWeaverLogger:
    """Système de logging structuré pour ModelWeaver."""
    
    def __init__(self, name="modelweaver", log_dir=None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # Chemin des logs
        log_path = mw_home() / "logs" if log_dir is None else Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        
        # Formatage
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        
        # File Handler
        file_name = f"mw_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_path / file_name)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

    def info(self, msg): self.logger.info(msg)
    def debug(self, msg): self.logger.debug(msg)
    def warn(self, msg): self.logger.warning(msg)
    def error(self, msg): self.logger.error(msg)
    def critical(self, msg): self.logger.critical(msg)

# Instance globale
log = ModelWeaverLogger()
