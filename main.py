# main.py
import json
import sys
from PyQt6 import QtWidgets
from gui import MainWindow
from simulated_device import SimulatedProducer
import os

if __name__ == "__main__":
    cfg_path = "config.json"
    if not os.path.exists(cfg_path):
        print("Please create config.json")
        sys.exit(1)
    with open(cfg_path, "r") as f:
        config = json.load(f)

    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow(config)
    w.show()

    # option: start simulated producers if enabled
    sims = []
    if config.get("simulate", {}).get("enabled", False):
        sims.append(SimulatedProducer(w.on_packet, device_name="ble_esp", rate_hz=250))
        sims.append(SimulatedProducer(w.on_packet, device_name="ref_ecg", rate_hz=250))
        for s in sims: s.start()

    sys.exit(app.exec())
