# storage.py
import os
import h5py
import threading
import json
import sqlite3
from utils import now_iso
import numpy as np

class Storage:
    def __init__(self, base_dir="sessions", flush_interval=2):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.flush_interval = flush_interval
        self.lock = threading.Lock()
        self.session_file = None
        self.h5 = None
        self.datasets = {}  # device_name -> (ts_ds, val_ds)
        self.buffers = {}   # device_name -> {"ts":[], "val":[]}
        self._stop = False
        self._flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self._flush_thread.start()
        self._init_db()

    def _init_db(self):
        self.db_path = os.path.join(self.base_dir, "sessions.sqlite")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, name TEXT, start_iso TEXT, end_iso TEXT, filepath TEXT, meta TEXT)''')
        self.conn.commit()

    def start_session(self, session_id, name, meta=None):
        with self.lock:
            filename = f"{session_id}.h5"
            self.session_file = os.path.join(self.base_dir, filename)
            self.h5 = h5py.File(self.session_file, "w")
            self.h5.attrs['session_id'] = session_id
            self.h5.attrs['name'] = name
            self.h5.attrs['start_iso'] = now_iso()
            self.buffers.clear()
            self.datasets.clear()
            self._save_session_index(session_id, name, meta or {})
        return self.session_file

    def _save_session_index(self, session_id, name, meta):
        c = self.conn.cursor()
        c.execute("INSERT OR REPLACE INTO sessions (session_id,name,start_iso,filepath,meta) VALUES (?,?,?,?,?)",
                  (session_id, name, now_iso(), os.path.basename(self.session_file), json.dumps(meta)))
        self.conn.commit()

    def append(self, device_name, ts_array, val_array):
        """ts_array, val_array are numpy arrays or lists"""
        with self.lock:
            if device_name not in self.buffers:
                self.buffers[device_name] = {"ts": [], "val": []}
            self.buffers[device_name]["ts"].extend(ts_array.tolist() if hasattr(ts_array, "tolist") else list(ts_array))
            self.buffers[device_name]["val"].extend(val_array.tolist() if hasattr(val_array, "tolist") else list(val_array))

    def _ensure_dataset(self, device_name, n_init=0):
        if device_name in self.datasets:
            return
        grp = self.h5.require_group(device_name) # To create the group or return it if the group exists
        ts_ds = grp.create_dataset("timestamps", shape=(0,), maxshape=(None,), dtype='f8', chunks=True)
        val_ds = grp.create_dataset("values", shape=(0,), maxshape=(None,), dtype='f4', chunks=True)
        self.datasets[device_name] = (ts_ds, val_ds)

    def flush(self):
        with self.lock:
            if self.h5 is None:
                return
            for device_name, buf in list(self.buffers.items()):
                if not buf["ts"]:
                    continue
                self._ensure_dataset(device_name)
                ts_ds, val_ds = self.datasets[device_name]
                n = len(buf["ts"])
                old_n = ts_ds.shape[0]
                ts_ds.resize((old_n + n,))
                val_ds.resize((old_n + n,))
                ts_ds[old_n:old_n+n] = np.array(buf["ts"], dtype='f8')
                val_ds[old_n:old_n+n] = np.array(buf["val"], dtype='f4')
                buf["ts"].clear()
                buf["val"].clear()
            # persist attributes
            self.h5.flush()

    def _periodic_flush(self):
        import time
        while not self._stop:
            try:
                self.flush()
            except Exception as e:
                print("Flush error:", e)
            time.sleep(self.flush_interval)

    def end_session(self, session_id):
        with self.lock:
            if self.h5 is not None:
                self.h5.attrs['end_iso'] = now_iso()
                self.h5.flush()
                self.h5.close()
                self.h5 = None
            # update DB end time
            c = self.conn.cursor()
            c.execute("UPDATE sessions SET end_iso=? WHERE session_id=?", (now_iso(), session_id))
            self.conn.commit()

    def stop(self):
        self._stop = True
        self._flush_thread.join(timeout=2)
        if self.h5:
            self.h5.close()
        self.conn.close()
