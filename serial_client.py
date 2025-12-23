# serial_client.py
import threading
import serial
import time

class SerialClient:
    def __init__(self, port, baudrate=115200, on_packet=None):
        self.port = port
        self.baudrate = baudrate
        self.on_packet = on_packet
        self._stop = False
        self._thread = None

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop:
            try:
                with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                    print("Serial connected", self.port)
                    while not self._stop:
                        line = ser.readline().decode(errors='ignore').strip()
                        if not line:
                            continue
                        # Example line: "45,16784383,600"
                        parts = line.split(',')
                        if len(parts) >= 3:
                            seq = int(parts[0])
                            src_ts = int(parts[1]) / 1000.0
                            value = float(parts[2])
                            pkt = {"device": "ref_ecg", "seq": seq, "src_ts": src_ts, "host_ts": time.time(), "value": value}
                        elif len(parts) == 2:
                            src_ts = float(parts[0]) / 1000.0
                            value = float(parts[1])
                            pkt = {"device": "ref_ecg", "src_ts": src_ts, "host_ts": time.time(), "value": value}
                        else:
                            value = float(parts[0])
                            pkt = {"device":"ref_ecg","src_ts": None, "host_ts": time.time(), "value": value}
                        if self.on_packet:
                            self.on_packet(pkt)
            except Exception as e:
                print("Serial error:", e)
                time.sleep(2)
