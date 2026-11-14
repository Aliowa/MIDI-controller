"""MIDI footswitch programmer (Windows / macOS). Talks JSON-lines to the Pico data port."""
import json
import sys
import time

import serial
from serial.tools import list_ports
from PySide6.QtWidgets import (
    QApplication, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

TYPES = [("Program Change (fixed)", "pc"), ("Program Change +1 (next)", "pc_inc"),
         ("Program Change -1 (prev)", "pc_dec"), ("Control Change (CC)", "cc"),
         ("Note", "note")]
MODES = [("Momentary (value on press, 0 on release)", "momentary"),
         ("Toggle (value / 0 alternating)", "toggle"),
         ("Trigger (value on press only)", "trigger")]
NAMES = ["Switch 1", "Switch 2", "Switch 3"]


class Device:
    def __init__(self):
        self.ser = None

    def connect(self, quiet=False):
        """Probe every serial port; the Pico's data channel answers a ping."""
        self.close()
        for p in list_ports.comports():
            if p.vid != 0x239A:  # Adafruit / CircuitPython
                continue
            try:
                s = serial.Serial(p.device, 115200, timeout=0.6, write_timeout=1)
                s.write(b'{"cmd":"ping"}\n')
                if b'"fw"' in s.readline():
                    s.timeout = 3
                    self.ser = s
                    return p.device
                s.close()
            except (serial.SerialException, OSError):
                pass
        return None

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def call(self, msg):
        self.ser.reset_input_buffer()
        self.ser.write((json.dumps(msg) + "\n").encode())
        resp = json.loads(self.ser.readline().decode())
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "device error"))
        return resp


class SwitchEditor(QGroupBox):
    def __init__(self, title):
        super().__init__(title)
        self.type = QComboBox()
        for label, v in TYPES:
            self.type.addItem(label, v)
        self.mode = QComboBox()
        for label, v in MODES:
            self.mode.addItem(label, v)
        self.channel = QSpinBox(); self.channel.setRange(1, 16)
        self.number = QSpinBox(); self.number.setRange(0, 127)
        self.value = QSpinBox(); self.value.setRange(0, 127)
        g = QGridLayout(self)
        for r, (name, w) in enumerate([("Message", self.type), ("Channel", self.channel),
                                       ("Number (PC / CC / note)", self.number),
                                       ("Value", self.value), ("Mode", self.mode)]):
            g.addWidget(QLabel(name), r, 0)
            g.addWidget(w, r, 1)
        self.type.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self):
        t = self.type.currentData()
        self.number.setEnabled(t in ("pc", "cc", "note"))
        self.value.setEnabled(t in ("cc", "note"))
        self.mode.setEnabled(t in ("cc", "note"))

    def load(self, s):
        self.type.setCurrentIndex(self.type.findData(s["type"]))
        self.mode.setCurrentIndex(self.mode.findData(s["mode"]))
        self.channel.setValue(s["channel"])
        self.number.setValue(s["number"])
        self.value.setValue(s["value"])

    def dump(self):
        return {"type": self.type.currentData(), "mode": self.mode.currentData(),
                "channel": self.channel.value(), "number": self.number.value(),
                "value": self.value.value()}


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI Footswitch Programmer")
        self.dev = Device()
        self.status = QLabel("Not connected")
        self.led = QLabel()
        self.led.setFixedSize(18, 18)
        self.set_led(False)
        self.editors = [SwitchEditor(n) for n in NAMES]
        self.btn_conn = QPushButton("Connect"); self.btn_conn.clicked.connect(self.toggle_connection)
        btn_read = QPushButton("Read from device"); btn_read.clicked.connect(self.read)
        btn_write = QPushButton("Write to device"); btn_write.clicked.connect(self.write)
        btn_reset = QPushButton("Factory defaults"); btn_reset.clicked.connect(self.reset)
        bar = QHBoxLayout()
        for b in (self.btn_conn, btn_read, btn_write, btn_reset):
            bar.addWidget(b)
        row = QHBoxLayout()
        for e in self.editors:
            row.addWidget(e)
        root = QVBoxLayout()
        foot = QHBoxLayout(); foot.addWidget(self.led); foot.addWidget(self.status, 1)
        root.addLayout(bar); root.addLayout(row); root.addLayout(foot)
        w = QWidget(); w.setLayout(root); self.setCentralWidget(w)
        self.connect(quiet=True)  # one attempt at app start, never again automatically

    def set_led(self, on):
        c = "#2e7d32" if on else "#c62828"
        self.led.setStyleSheet("background:%s; border-radius:9px; border:1px solid #333;" % c)

    def toggle_connection(self):
        if self.dev.ser:
            self.disconnect()
            self.status.setText("Disconnected")
        else:
            self.connect()

    def disconnect(self):
        self.dev.close()
        self.set_led(False)
        self.btn_conn.setText("Connect")

    def closeEvent(self, event):
        self.disconnect()
        event.accept()

    def guard(self, fn):
        if not self.dev.ser:
            self.status.setText("Not connected - press Connect")
            return
        try:
            fn()
        except Exception as e:
            self.disconnect()
            self.status.setText("Error: %s" % e)
            QMessageBox.warning(self, "Device error", str(e))

    def connect(self, quiet=False):
        self.status.setText("Connecting...")
        QApplication.processEvents()
        port = self.dev.connect()
        if port:
            self.set_led(True)
            self.btn_conn.setText("Disconnect")
            self.read()
            self.status.setText("Connected on %s - settings loaded  [%s]"
                                % (port, time.strftime("%H:%M:%S")))
        else:
            self.set_led(False)
            self.status.setText("Device not found (plugged in, not in BOOTSEL, no other program using the port?)")
            if not quiet:
                QMessageBox.warning(self, "Not found", self.status.text())

    def read(self):
        def f():
            cfg = self.dev.call({"cmd": "get"})["config"]
            for e, s in zip(self.editors, cfg["switches"]):
                e.load(s)
            self.status.setText("Settings read")
        self.guard(f)

    def write(self):
        def f():
            self.dev.call({"cmd": "set", "config": {"switches": [e.dump() for e in self.editors]}})
            self.status.setText("Settings saved to device")
        self.guard(f)

    def reset(self):
        def f():
            cfg = self.dev.call({"cmd": "reset"})["config"]
            for e, s in zip(self.editors, cfg["switches"]):
                e.load(s)
            self.status.setText("Defaults restored")
        self.guard(f)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    m = Main(); m.show()
    sys.exit(app.exec())
