# ==============
# Хелперы для чтения sql фалйов
# ==============
from django.conf import settings
from pathlib import Path

class SQLHelpers:
    def __init__(self):      
        self.sql_base_path:Path = settings.SQL_FILES_PATH
        
    @property
    def create_ststement_table(self):
        q =  self.sql_base_path / 'bs' / 'statements.sql'
        return q.read_text(encoding='utf-8')
    
    
        
        
    def read(self, *parts: str) -> str:
        """SQL-файл из папки sql: read('bs', 'bs_gl.sql')."""
        return (self.sql_base_path.joinpath(*parts)).read_text(encoding='utf-8')
