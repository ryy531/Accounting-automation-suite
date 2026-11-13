"""
Per-run log writer. Outputs JSON to <output_dir>/run.log.
"""
import json
from datetime import datetime
from pathlib import Path

class RunLogger:

    def __init__(self, log_path : Path):
        self.log_path = log_path
        self.entries = []
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
    

    def log(self, event_type : str, **kwargs):
        entry = {
            "timestamp" : datetime.now().isoformat(),
            "event" : event_type,
            **kwargs
        }
        self.entries.append(entry)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


    
    def write(self):
        with self.log_path.open("w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
