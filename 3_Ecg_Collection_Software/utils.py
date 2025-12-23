# utils.py
import time
import uuid
from datetime import datetime

def now_ts():
    return time.time()

def now_iso():
    return datetime.utcnow().isoformat() + "Z"

def new_session_id():
    return uuid.uuid4().hex
