# session_manager.py
from storage import Storage
from utils import new_session_id
import numpy as np

class SessionManager:
    def __init__(self, storage_base="sessions", flush_interval=2):
        self.storage = Storage(base_dir=storage_base, flush_interval=flush_interval)
        self.current = None

    def start(self, name, meta=None):
        sid = new_session_id()
        self.current = sid
        filepath = self.storage.start_session(sid, name, meta or {})
        return sid, filepath

    def append_sample(self, device_name, ts, value):
        self.storage.append(device_name, np.array([ts], dtype='f8'), np.array([value], dtype='f4'))

    def end(self):
        sid = self.current
        if sid:
            self.storage.end_session(sid)
            self.current = None
