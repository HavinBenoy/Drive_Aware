# simulated_device.py
import threading
import time
import math
import random

class SimulatedProducer:
    def __init__(self, on_packet, device_name="ble_esp", rate_hz=250):
        self.on_packet = on_packet
        self.device = device_name
        self.rate_hz = rate_hz
        self._stop = False
        self._t = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._stop = False
        self._thread.start()

    def stop(self):
        self._stop = True
        self._thread.join(timeout=1)

    def _run(self):
        seq = 0
        while not self._stop:
            # simple ECG-like waveform (sin + spike)
            val = 200.0 * math.sin(2*math.pi*1.0*self._t) + (50.0 if random.random() < 0.01 else 0)
            pkt = {"device": self.device, "seq": seq, "src_ts": time.time(), "host_ts": time.time(), "value": val}
            self.on_packet(pkt)
            seq += 1
            self._t += 1.0/self.rate_hz
            time.sleep(1.0/self.rate_hz)
