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
        self.ax.set_ylim(-2000, 2000)
        self.ax.set_xlim(0, max_points)
        self.buffer = []

    def add_point(self, v):
        self.buffer.append(v)
        if len(self.buffer) > self.max_points:
            self.buffer = self.buffer[-self.max_points:]
        self.line.set_data(np.arange(len(self.buffer)), self.buffer)
        self.ax.set_xlim(0, max(self.max_points, len(self.buffer)))
        try:
            self.draw_idle()
        except Exception:
            pass

class PrecheckWorker(QtCore.QThread):
    result_signal = QtCore.pyqtSignal(object)  # emit dict
    def __init__(self, duration=8, expected_rates=None, tolerance=0.3):
        super().__init__()
        self.duration = duration
        self.expected_rates = expected_rates or {"ble_esp":250, "ref_ecg":250}
        self.tolerance = tolerance
        self.buffers = {"ble_esp": [], "ref_ecg": []}
        self._stop = False

    def run(self):
        start = time.time()
        while (time.time() - start) < self.duration and not self._stop:
            time.sleep(0.05)
        stats = {}
        for dev, arr in self.buffers.items():
            n = len(arr)
            mean = sum(arr)/n if n else 0.0
            var = sum((x-mean)**2 for x in arr)/n if n else 0.0
            std = var**0.5
            stats[dev] = {"count": n, "mean": mean, "std": std, "rate_est": n / max(1e-6, self.duration)}
        passed = True
        details = {}
        for dev, s in stats.items():
            exp = self.expected_rates.get(dev, None)
            if s["count"] < max(10, 0.1 * (exp or 100) * self.duration):
                passed = False
                details[dev] = f"low_packets ({s['count']})"
                continue
            if exp:
                rate = s["rate_est"]
                if abs(rate - exp) > (self.tolerance * exp):
                    passed = False
                    details[dev] = f"bad_rate ({rate:.1f} Hz expected {exp})"
                    continue
            details[dev] = f"ok (count={s['count']}, rate={s['rate_est']:.1f}Hz)"
        self.result_signal.emit({"passed": passed, "stats": stats, "details": details})

    def add_sample(self, device, value):
        if device in self.buffers:
            self.buffers[device].append(value)

class MainWindow(QMainWindow):
    packet_signal = QtCore.pyqtSignal(object)
    status_signal = QtCore.pyqtSignal(object)

    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("ECG Collector")
        self.setGeometry(50,50,1000,700)
        self.config = config
        self.session = SessionManager(storage_base=config['storage']['data_dir'],
                                      flush_interval=config['storage'].get('flush_interval_sec',2))
        self.setup_ui()
        # signals
        self.packet_signal.connect(self.handle_packet)
        self.status_signal.connect(self.handle_status)
        # clients
        self.ble_client = BLEClient(self.config['ble']['notify_uuid'],
                                    device_name=self.config['ble']['esp32_name'],
                                    on_packet=self.packet_signal.emit)
        self.ble_client.on_status_change = self.status_signal.emit
        self.serial_client = SerialClient(self.config['serial']['ref_ecg_port'],
                                          baudrate=self.config['serial'].get('baudrate',115200),
                                          on_packet=self.packet_signal.emit)
        self.serial_client.on_status_change = self.status_signal.emit
        # engine
        self.engine = pyttsx3.init() if self.config['prompt'].get('tts_enabled', True) else None
        self.running = False
        self.current_session_id = None
        self.preworker = None
        self.watchdog_timer = QtCore.QTimer()
        self.watchdog_timer.timeout.connect(self.watchdog_check)
        self.watchdog_timer.start(2000)

    def setup_ui(self):
        self.device_list_label = QLabel("Devices & Status", self); self.device_list_label.setGeometry(10,10,300,20)
        self.ble_status_lbl = QLabel("BLE: disconnected", self); self.ble_status_lbl.setGeometry(10,40,300,20)
        self.ref_status_lbl = QLabel("Serial: disconnected", self); self.ref_status_lbl.setGeometry(10,70,300,20)

        self.scan_button = QPushButton("Start Scan", self); self.scan_button.setGeometry(10,100,120,30)
        self.scan_button.clicked.connect(self.start_scan)

        self.precheck_button = QPushButton("Run Pre-Session Check", self); self.precheck_button.setGeometry(140,100,200,30)
        self.precheck_button.clicked.connect(self.run_precheck)

        self.name_input = QLineEdit(self); self.name_input.setGeometry(10,140,260,30); self.name_input.setPlaceholderText("Session name")
        self.start_button = QPushButton("Start Session", self); self.start_button.setGeometry(10,180,120,40)
        self.start_button.clicked.connect(self.start_session)
        self.start_button.setEnabled(False)

        self.stop_button = QPushButton("Stop Session", self); self.stop_button.setGeometry(140,180,120,40)
        self.stop_button.clicked.connect(self.stop_session)

        self.ble_plot = LivePlot("Steering ECG (BLE)", max_points=2000)
        self.ble_plot.setParent(self)
        self.ble_plot.setGeometry(330,10,640,320)
        self.ref_plot = LivePlot("Reference ECG (Wired)", max_points=2000)
        self.ref_plot.setParent(self)
        self.ref_plot.setGeometry(330,340,640,320)

        self.log = QTextEdit(self); self.log.setGeometry(10,240,300,420); self.log.setReadOnly(True)

        self.prompt_button = QPushButton("Prompt Now", self); self.prompt_button.setGeometry(10,220,120,30)
        self.prompt_button.clicked.connect(self.prompt_user)

    def log_msg(self, s):
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self.log.append(f"[{ts}] {s}")
        print(s)

    def start_scan(self):
        self.log_msg("Starting BLE and Serial clients...")
        self.ble_client.start()
        self.serial_client.start()
        # clients will emit status updates via status_signal
        self.log_msg("Clients started (they will connect if available).")

    @QtCore.pyqtSlot(object)
    def handle_status(self, status):
        dev = status.get("device")
        connected = status.get("connected", False)
        if dev == "ble_esp":
            self.ble_status_lbl.setText(f"BLE: {'connected' if connected else 'disconnected'}")
        elif dev == "ref_ecg":
            self.ref_status_lbl.setText(f"Serial: {'connected' if connected else 'disconnected'}")
        self.log_msg(f"Status update: {dev} connected={connected}")

    @QtCore.pyqtSlot(object)
    def handle_packet(self, pkt):
        device = pkt.get("device","unknown")
        host_ts = pkt.get("host_ts", time.time())
        value = pkt.get("value", 0.0)
        if self.running and self.current_session_id:
            self.session.append_sample(device, host_ts, value)
        if device == "ble_esp":
            self.ble_plot.add_point(value)
        elif device == "ref_ecg":
            self.ref_plot.add_point(value)

        # If precheck running, forward sample to worker
        if self.preworker and self.preworker.isRunning():
            self.preworker.add_sample(device, value)

    def run_precheck(self):
        if not (self.ble_client.is_connected and self.serial_client.is_connected):
            QtWidgets.QMessageBox.warning(self, "Precheck", "Both devices must be connected before precheck.")
            self.log_msg("Precheck aborted: devices not connected.")
            return
        dur = self.config.get('precheck', {}).get('duration_sec', 8)
        tolerance = self.config.get('precheck', {}).get('tolerance_rate', 0.3)
        expected = {"ble_esp": self.config['ble'].get('expected_rate',250),
                    "ref_ecg": self.config['serial'].get('expected_rate',250)}
        self.preworker = PrecheckWorker(duration=dur, expected_rates=expected, tolerance=tolerance)
        self.preworker.result_signal.connect(self.on_precheck_result)
        self.log_msg(f"Running precheck for {dur} seconds...")
        self.preworker.start()

    @QtCore.pyqtSlot(object)
    def on_precheck_result(self, res):
        passed = res.get("passed", False)
        self.log_msg(f"Precheck finished: passed={passed}, details={res.get('details')}")
        if passed:
            QtWidgets.QMessageBox.information(self, "Precheck", "Pre-session check PASSED. Start Session enabled.")
            self.start_button.setEnabled(True)
        else:
            QtWidgets.QMessageBox.critical(self, "Precheck", f"Precheck FAILED: {res.get('details')}")
            self.start_button.setEnabled(False)

    def start_session(self):
        if not (self.ble_client.is_connected and self.serial_client.is_connected):
            QtWidgets.QMessageBox.warning(self, "Start session", "Devices are not connected.")
            self.log_msg("Start aborted: devices not connected.")
            return
        if not self.start_button.isEnabled():
            QtWidgets.QMessageBox.warning(self, "Start session", "Precheck not passed. Run precheck first.")
            self.log_msg("Start aborted: precheck not passed.")
            return
        name = self.name_input.text().strip() or f"session_{int(time.time())}"
        sid, path = self.session.start(name, meta={"notes":"collected with GUI"})
        self.current_session_id = sid
        self.running = True
        self.log_msg(f"Session started: id={sid}, file={path}")
        # start prompt timer
        interval = self.config['prompt'].get('interval_sec', 300) * 1000
        self.prompt_timer = QtCore.QTimer()
        self.prompt_timer.timeout.connect(self.prompt_user)
        self.prompt_timer.start(interval)

    def stop_session(self):
        if not self.running:
            self.log_msg("No session running")
            return
        self.prompt_timer.stop()
        self.session.end()
        self.running = False
        self.log_msg("Session ended and flushed")

    def prompt_user(self):
        text = "Please rate your sleepiness from zero to ten now."
        if self.engine:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as e:
                self.log_msg(f"TTS error: {e}")
        val, ok = QtWidgets.QInputDialog.getInt(self, "Sleepiness Prompt", "Rate 0-10:", min=0, max=10, step=1)
        if ok:
            ts = time.time()
            # store label
            h5 = self.session.storage.h5
            if h5 is not None:
                grp = h5.require_group("labels")
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
                h5.flush()
            self.log_msg(f"Label recorded: {val} @ {ts}")

    def watchdog_check(self):
        t = time.time()
        for client, label in [(self.ble_client, self.ble_status_lbl), (self.serial_client, self.ref_status_lbl)]:
            last = getattr(client, "last_packet_time", None)
            if not client.is_connected:
                label.setStyleSheet("color: gray;")
            else:
                if last is None or (t - last) > 3.0:
                    label.setStyleSheet("color: orange;")
                    self.log_msg(f"Warning: {client} no packets for {(t-last) if last else 'N/A'} sec")
                else:
                    label.setStyleSheet("color: green;")

    def closeEvent(self, event):
        self.ble_client.stop()
        self.serial_client.stop()
        self.session.storage.stop()
        event.accept()