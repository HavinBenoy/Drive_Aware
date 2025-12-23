# ble_client.py
import asyncio
import threading
from bleak import BleakScanner, BleakClient
import time

class BLEClient:
    def __init__(self, notify_uuid, device_name=None, on_packet=None):
        self.notify_uuid = notify_uuid
        self.device_name = device_name
        self.on_packet = on_packet
        self._stop = False
        self._thread = None
        self._address = None

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)

    def _run_loop(self):
        asyncio.run(self._async_run())

    async def _async_run(self):
        while not self._stop:
            try:
                # discover device
                device = None
                if self.device_name:
                    devices = await BleakScanner.discover(timeout=2.0)
                    for d in devices:
                        if self.device_name in (d.name or ""):
                            device = d
                            break
                if device is None:
                    await asyncio.sleep(2.0)
                    continue
                self._address = device.address
                async with BleakClient(self._address) as client:
                    print("BLE connected:", self._address)
                    await client.start_notify(self.notify_uuid, self._handle_notification)
                    while not self._stop:
                        await asyncio.sleep(1)
                    await client.stop_notify(self.notify_uuid)
            except Exception as e:
                print("BLE error:", e)
                await asyncio.sleep(2.0)

    def _handle_notification(self, sender, data: bytearray):
        """Parse payload. Default expects ASCII 'seq,ts_ms,value\\n' or 'ts,value'"""
        try:
            s = data.decode(errors='ignore').strip()
            # Example ASCII: "123,16784383,512\n" -> seq, source_ts_ms, value
            parts = s.split(',')
            if len(parts) >= 3:
                seq = int(parts[0])
                source_ts_ms = int(parts[1])
                value = float(parts[2])
                src_ts = source_ts_ms / 1000.0
                host_ts = time.time()
                pkt = {"device":"ble_esp","seq":seq, "src_ts": src_ts, "host_ts": host_ts, "value": value}
            elif len(parts) == 2:
                src_ts = float(parts[0]) / 1000.0
                value = float(parts[1])
                pkt = {"device":"ble_esp", "src_ts": src_ts, "host_ts": time.time(), "value": value}
            else:
                # fallback parse as single numeric sample
                value = float(s)
                pkt = {"device":"ble_esp","src_ts": None, "host_ts": time.time(), "value": value}
            if self.on_packet:
                self.on_packet(pkt)
        except Exception as e:
            print("parse notif error:", e)
