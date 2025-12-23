# gui.py
import sys
import threading
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QLineEdit, QLabel, QListWidget, QTextEdit
import matplotlib
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
import time
import pyttsx3

from ble_client import BLEClient
from serial_client import SerialClient
from session_manager import SessionManager
from utils import now_iso

class LivePlot(FigureCanvas):
    def __init__(self, title="ECG", max_points=2000):
        fig = Figure(figsize=(5,2))
        self.ax = fig.add_subplot(111)
        super().__init__(fig)
        self.title = title
        self.max_points = max_points
        self.ax.set_title(title)
        self.line, = self.ax.plot([],[])
        self.ax.set_ylim(-1000, 1000)
        self.ax.set_xlim(0, max_points)
        self.buffer = []

    def add_point(self, v):
        self.buffer.append(v)
        if len(self.buffer) > self.max_points:
            self.buffer = self.buffer[-self.max_points:]
        self.line.set_data(np.arange(len(self.buffer)), self.buffer)
        self.ax.set_xlim(0, max(self.max_points, len(self.buffer)))
        self.draw_idle()

class MainWindow(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("ECG Collector")
        self.setGeometry(100,100,900,600)
        self.config = config
        self.session = SessionManager(storage_base=config['storage']['data_dir'],
                                      flush_interval=config['storage'].get('flush_interval_sec',2))
        # UI
        self.device_list = QListWidget(self)
        self.device_list.setGeometry(10,10,260,150)
        self.scan_button = QPushButton("Start Scan", self); self.scan_button.setGeometry(10,170,120,30)
        self.start_button = QPushButton("Start Session", self); self.start_button.setGeometry(140,170,130,30)
        self.stop_button = QPushButton("Stop Session", self); self.stop_button.setGeometry(280,170,120,30)
        self.name_input = QLineEdit(self); self.name_input.setGeometry(10,210,260,30); self.name_input.setPlaceholderText("Session name")
        self.log = QTextEdit(self); self.log.setGeometry(10,250,400,330); self.log.setReadOnly(True)

        self.ble_plot = LivePlot("Steering ECG")
        self.ble_plot.setParent(self)
        self.ble_plot.setGeometry(420,10,460,260)
        self.ref_plot = LivePlot("Reference ECG")
        self.ref_plot.setParent(self)
        self.ref_plot.setGeometry(420,280,460,260)

        # clients
        self.ble_client = BLEClient(self.config['ble']['notify_uuid'], device_name=self.config['ble']['esp32_name'], on_packet=self.on_packet)
        self.serial_client = SerialClient(self.config['serial']['ref_ecg_port'], baudrate=self.config['serial'].get('baudrate',115200), on_packet=self.on_packet)
        self.running = False
        self.current_session_id = None
        self.engine = pyttsx3.init() if self.config['prompt'].get('tts_enabled', True) else None

        # connect signals
        self.scan_button.clicked.connect(self.start_scan)
        self.start_button.clicked.connect(self.start_session)
        self.stop_button.clicked.connect(self.stop_session)

        # prompt timer
        self.prompt_timer = QtCore.QTimer()
        self.prompt_timer.timeout.connect(self.prompt_user)

    def log_msg(self, s):
        self.log.append(f"[{now_iso()}] {s}")
        print(s)

    def start_scan(self):
        self.log_msg("Starting BLE & Serial clients (scan/connect)")
        self.ble_client.start()
        self.serial_client.start()
        self.log_msg("Clients started")

    def start_session(self):
        if self.running:
            self.log_msg("Session already running")
            return
        name = self.name_input.text().strip() or f"session_{int(time.time())}"
        sid, fp = self.session.start(name, meta={"notes":"collected via gui"})
        self.current_session_id = sid
        self.log_msg(f"Started session {sid} -> {fp}")
        self.running = True
        interval = self.config['prompt'].get('interval_sec', 300) * 1000
        self.prompt_timer.start(interval)
        # start small worker thread for UI updates if needed

    def stop_session(self):
        if not self.running:
            self.log_msg("No session running")
            return
        self.prompt_timer.stop()
        self.session.end()
        self.running = False
        self.log_msg("Session ended and flushed")

    def prompt_user(self):
        # TTS prompt and simple input dialog
        text = "Please rate your sleepiness from zero to ten now."
        if self.engine:
            self.engine.say(text)
            self.engine.runAndWait()
        # show simple dialog
        val, ok = QtWidgets.QInputDialog.getInt(self, "Sleepiness Prompt", "Rate 0-10:", min=0, max=10, step=1)
        if ok:
            ts = time.time()
            # store label as dataset
            self.session.storage.h5.require_group("labels")
            grp = self.session.storage.h5["labels"]
            # simple append arrays
            if "timestamps" not in grp:
                grp.create_dataset("timestamps", data=[ts], maxshape=(None,), chunks=True)
                grp.create_dataset("values", data=[val], maxshape=(None,), chunks=True)
            else:
                ts_ds = grp["timestamps"]; val_ds = grp["values"]
                old = ts_ds.shape[0]
                ts_ds.resize((old+1,))
                val_ds.resize((old+1,))
                ts_ds[old] = ts
                val_ds[old] = val
            self.log_msg(f"Label recorded: {val} @ {ts}")

    def on_packet(self, pkt):
        # pkt = {"device":..., "src_ts":..., "host_ts":..., "value": ...}
        device = pkt.get("device", "unknown")
        host_ts = pkt.get("host_ts", time.time())
        value = pkt.get("value", 0.0)
        # append to session storage
        if self.running and self.current_session_id:
            self.session.append_sample(device, host_ts, value)
        # update plots
        if device == "ble_esp":
            self.ble_plot.add_point(value)
        elif device == "ref_ecg":
            self.ref_plot.add_point(value)
        # log every N packets is too verbose; show occasional
        # self.log_msg(f"pkt {device} val={value}")

    def closeEvent(self, event):
        self.log_msg("Shutting down")
        self.ble_client.stop()
        self.serial_client.stop()
        self.session.storage.stop()
        event.accept()
