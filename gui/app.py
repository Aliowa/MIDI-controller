"""MIDI footswitch programmer (Windows / macOS). Talks JSON-lines to the Pico data port."""
import base64
import binascii
import json
import re
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

TYPES = [
    ("Program Change (fixed)", "pc"),
    ("Program Change +1 (next)", "pc_inc"),
    ("Program Change -1 (prev)", "pc_dec"),
    ("Control Change (CC)", "cc"),
    ("Note", "note"),
]
MODES = [
    ("Momentary (value on press, 0 on release)", "momentary"),
    ("Toggle (value / 0 alternating)", "toggle"),
    ("Trigger (value on press only)", "trigger"),
]
NAMES = ["Switch 1", "Switch 2", "Switch 3"]


def inspect_firmware(src):
    norm = src.replace("\r\n", "\n")
    m = re.search(r'^FW_VERSION\s*=\s*["\']([^"\']+)["\']', norm, re.MULTILINE)
    fw = m.group(1) if m else "?"
    crc = "%08x" % (binascii.crc32(norm.encode("utf-8")) & 0xFFFFFFFF)
    return fw, crc


class Device:
    def __init__(self):
        self.ser = None
        self.fw = None
        self.crc = None

    def connect(self):
        """Probe CircuitPython serial ports (data port first) for a ping reply."""
        self.close()
        ports = [p for p in list_ports.comports() if p.vid == 0x239A]
        # Prefer the second CDC interface (data port) so we don't poke the REPL console first
        ports.sort(key=lambda p: (not (p.location or "").endswith(".0"), p.device), reverse=True)
        for p in ports:
            try:
                s = serial.Serial(p.device, 115200, timeout=0.6, write_timeout=1)
                s.write(b'{"cmd":"ping"}\n')
                line = s.readline()
                if b'"fw"' in line:
                    s.timeout = 3
                    self.ser = s
                    info = json.loads(line.decode())
                    self.fw = info.get("fw", "?")
                    self.crc = info.get("crc", "")
                    return p.device
                s.close()
            except (serial.SerialException, OSError, ValueError):
                pass
        return None

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None
        self.fw = None
        self.crc = None

    def call(self, msg):
        self.ser.reset_input_buffer()
        self.ser.write((json.dumps(msg) + "\n").encode())
        resp = json.loads(self.ser.readline().decode())
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "device error"))
        return resp

    def update_firmware(self, src):
        """Upload new code.py over the data port (v1.1+), with REPL fallback for v1.0."""
        chunk_size = 512
        try:
            for i in range(0, len(src), chunk_size):
                self.call({"cmd": "fw_chunk", "reset": i == 0, "data": src[i:i + chunk_size]})
            self.call({"cmd": "fw_commit"})
            return
        except RuntimeError as e:
            if "unknown cmd" not in str(e):
                raise
        self._update_via_repl(src)

    def _update_via_repl(self, src):
        """Fallback for FW v1.0: write /code.py over the CircuitPython console port."""
        data_port = self.ser.port if self.ser else None
        ports = [
            p.device for p in list_ports.comports()
            if p.vid == 0x239A and p.device != data_port
        ]
        if not ports:
            raise RuntimeError("Console serial port not found for v1.0 fallback update")
        b64 = base64.b64encode(src.encode("utf-8")).decode("ascii")
        with serial.Serial(ports[0], 115200, timeout=0.5, write_timeout=2) as repl:
            repl.dtr = True
            while repl.in_waiting:
                repl.read(repl.in_waiting)
            repl.write(b"\x03\x03")
            time.sleep(0.3)
            repl.write(b"\r\n")
            time.sleep(0.3)
            while repl.in_waiting:
                repl.read(repl.in_waiting)

            def send_line(line):
                raw = (line + "\r\n").encode("ascii")
                for i in range(0, len(raw), 64):
                    repl.write(raw[i:i + 64])
                    time.sleep(0.01)
                    while repl.in_waiting:
                        repl.read(repl.in_waiting)

            send_line("import binascii")
            send_line('f = open("/code.py", "wb")')
            for i in range(0, len(b64), 120):
                send_line('f.write(binascii.a2b_base64("%s"))' % b64[i:i + 120])
            send_line("f.close()")
            repl.write(b"\x04")
            time.sleep(0.2)


class SwitchEditor(QGroupBox):
    def __init__(self, title):
        super().__init__(title)
        self.type = QComboBox()
        for label, v in TYPES:
            self.type.addItem(label, v)
        self.mode = QComboBox()
        for label, v in MODES:
            self.mode.addItem(label, v)
        self.channel = QSpinBox()
        self.channel.setRange(1, 16)
        self.number = QSpinBox()
        self.number.setRange(0, 127)
        self.value = QSpinBox()
        self.value.setRange(0, 127)
        g = QGridLayout(self)
        fields = [
            ("Message", self.type),
            ("Channel", self.channel),
            ("Number (PC / CC / note)", self.number),
            ("Value", self.value),
            ("Mode", self.mode),
        ]
        for r, (name, w) in enumerate(fields):
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
        return {
            "type": self.type.currentData(),
            "mode": self.mode.currentData(),
            "channel": self.channel.value(),
            "number": self.number.value(),
            "value": self.value.value(),
        }


def fmt_sw(s):
    t = s["type"]
    if t in ("pc_inc", "pc_dec"):
        return "%s, Ch %d" % ("PC +1" if t == "pc_inc" else "PC -1", s["channel"])
    if t == "pc":
        return "PC #%d, Ch %d" % (s["number"], s["channel"])
    return "%s #%d = %d (%s), Ch %d" % (
        t.upper(), s["number"], s["value"], s["mode"], s["channel"]
    )


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

        self.btn_conn = QPushButton("Connect")
        self.btn_conn.clicked.connect(self.toggle_connection)
        btn_read = QPushButton("Read from device")
        btn_read.clicked.connect(self.read)
        btn_write = QPushButton("Write to device")
        btn_write.clicked.connect(self.write)
        btn_reset = QPushButton("Factory defaults")
        btn_reset.clicked.connect(self.reset)
        btn_fw = QPushButton("Update firmware")
        btn_fw.clicked.connect(self.update_fw)

        bar = QHBoxLayout()
        for b in (self.btn_conn, btn_read, btn_write, btn_reset, btn_fw):
            bar.addWidget(b)
        row = QHBoxLayout()
        for e in self.editors:
            row.addWidget(e)
        foot = QHBoxLayout()
        foot.addWidget(self.led)
        foot.addWidget(self.status, 1)

        root = QVBoxLayout()
        root.addLayout(bar)
        root.addLayout(row)
        root.addLayout(foot)
        w = QWidget()
        w.setLayout(root)
        self.setCentralWidget(w)
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
            self.status.setText("Connected on %s (FW v%s) - settings loaded  [%s]"
                                % (port, self.dev.fw, time.strftime("%H:%M:%S")))
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

    def write(self, _checked=False, *, confirm=True):
        def f():
            cur = self.dev.call({"cmd": "get"})["config"]["switches"]
            new = [e.dump() for e in self.editors]
            if cur == new:
                self.status.setText("Settings on device already match the GUI")
                if confirm:
                    QMessageBox.information(
                        self,
                        "No changes",
                        "Settings on the board already match the current GUI values.",
                    )
                return
            if confirm:
                diffs = [
                    "Switch %d:\n  Board: %s\n  New:   %s" % (i + 1, fmt_sw(a), fmt_sw(b))
                    for i, (a, b) in enumerate(zip(cur, new))
                    if a != b
                ]
                reply = QMessageBox.question(
                    self,
                    "Confirm write to device",
                    "Write new settings to the board?\n\n" + "\n\n".join(diffs),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.status.setText("Write cancelled")
                    return
            self.dev.call({"cmd": "set", "config": {"switches": new}})
            self.status.setText("Settings saved to device")
        self.guard(f)

    def reset(self, _checked=False, *, confirm=True):
        def f():
            if confirm:
                reply = QMessageBox.question(
                    self,
                    "Confirm factory defaults",
                    "Reset all 3 switches on the board to factory defaults?\n\n"
                    "This will overwrite your saved settings on the device.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.status.setText("Factory reset cancelled")
                    return
            cfg = self.dev.call({"cmd": "reset"})["config"]
            for e, s in zip(self.editors, cfg["switches"]):
                e.load(s)
            self.status.setText("Defaults restored")
        self.guard(f)

    def update_fw(self, _checked=False, *, confirm=True):
        def f():
            fw_path = BASE_DIR / "firmware" / "code.py"
            if not fw_path.exists():
                raise RuntimeError("firmware/code.py not found")
            src = fw_path.read_text(encoding="utf-8").replace("\r\n", "\n")
            new_fw, new_crc = inspect_firmware(src)
            ping = self.dev.call({"cmd": "ping"})
            cur_fw = ping.get("fw", "?")
            cur_crc = ping.get("crc", "")

            if cur_fw == new_fw and cur_crc and cur_crc == new_crc:
                msg = "Firmware is already up to date (FW v%s, CRC %s)." % (cur_fw, cur_crc)
                self.status.setText(msg)
                if confirm:
                    QMessageBox.information(self, "Already up to date", msg)
                return

            if confirm:
                note = " (code modified, same version)" if cur_fw == new_fw else ""
                prompt = (
                    "Update firmware on the board?\n\n"
                    "Current on board:   FW v%s%s\n"
                    "New (firmware/code.py): FW v%s (CRC %s)%s"
                    % (
                        cur_fw,
                        " (CRC %s)" % cur_crc if cur_crc else "",
                        new_fw,
                        new_crc,
                        note,
                    )
                )
                ans = QMessageBox.question(
                    self,
                    "Confirm firmware update",
                    prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    self.status.setText("Firmware update cancelled")
                    return

            self.status.setText("Uploading FW v%s to device..." % new_fw)
            QApplication.processEvents()
            self.dev.update_firmware(src)
            self.disconnect()
            self.status.setText("Rebooting device with FW v%s..." % new_fw)
            QApplication.processEvents()
            time.sleep(1.8)
            self.connect()
        self.guard(f)


if __name__ == "__main__":
    if sys.platform == "win32":  # own taskbar icon instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Aliowa.MidiFootswitch.Programmer.1")
    app = QApplication(sys.argv)
    icon_file = "icon.ico" if sys.platform == "win32" else "icon.png"
    app.setWindowIcon(QIcon(str(BASE_DIR / "assets" / icon_file)))
    m = Main()
    m.show()
    sys.exit(app.exec())
