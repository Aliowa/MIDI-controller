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
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
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


def clean_name(val):
    return "".join(c for c in str(val or "").strip() if 32 <= ord(c) < 127)[:28].strip()


def inspect_firmware(src):
    norm = src.replace("\r\n", "\n")
    m = re.search(r'^FW_VERSION\s*=\s*["\']([^"\']+)["\']', norm, re.MULTILINE)
    fw = m.group(1) if m else "?"
    crc = "%08x" % (binascii.crc32(norm.encode("utf-8")) & 0xFFFFFFFF)
    return fw, crc


class Device:
    def __init__(self, on_event=None):
        self.ser = None
        self.port = None
        self.fw = None
        self.crc = None
        self.name = "MIDI Footswitch"
        self.on_event = on_event
        self._rx = b""

    def _candidate_ports(self):
        ports = [p for p in list_ports.comports() if p.vid == 0x239A]
        # Prefer the second CDC interface (data port) so we don't poke the REPL console first
        ports.sort(key=lambda p: (not (p.location or "").endswith(".0"), p.device), reverse=True)
        return ports

    def scan(self):
        """Return list of connected pedals: [{'port', 'name', 'fw', 'crc'}]."""
        found = []
        seen_locs = set()
        for p in self._candidate_ports():
            base_loc = (p.location or "").rsplit(".", 1)[0] if p.location else None
            if base_loc and base_loc in seen_locs:
                continue
            if self.ser and p.device == self.port:
                found.append({
                    "port": self.port,
                    "name": self.name or "MIDI Footswitch",
                    "fw": self.fw or "?",
                    "crc": self.crc or "",
                })
                if base_loc:
                    seen_locs.add(base_loc)
                continue
            try:
                s = serial.Serial(p.device, 115200, timeout=0.6, write_timeout=1)
                s.dtr = True
                s.reset_input_buffer()
                s.write(b'{"cmd":"ping"}\n')
                line = s.readline()
                s.close()
                if b'"fw"' in line:
                    info = json.loads(line.decode())
                    found.append({
                        "port": p.device,
                        "name": info.get("name") or "MIDI Footswitch",
                        "fw": info.get("fw", "?"),
                        "crc": info.get("crc", ""),
                    })
                    if base_loc:
                        seen_locs.add(base_loc)
            except (serial.SerialException, OSError, ValueError):
                pass
        return found

    def connect(self, target_port=None, prefer_name=None):
        """Probe CircuitPython serial ports (data port first) for a ping reply."""
        self.close()
        ports = self._candidate_ports()
        if target_port:
            ports = [p for p in ports if p.device == target_port]
        for p in ports:
            try:
                s = serial.Serial(p.device, 115200, timeout=0.6, write_timeout=1)
                s.dtr = True
                s.reset_input_buffer()
                s.write(b'{"cmd":"ping"}\n')
                line = s.readline()
                if b'"fw"' in line:
                    info = json.loads(line.decode())
                    dev_name = info.get("name") or "MIDI Footswitch"
                    if prefer_name and dev_name != prefer_name:
                        s.close()
                        continue
                    s.timeout = 3
                    self.ser = s
                    self._rx = b""
                    self.port = p.device
                    self.fw = info.get("fw", "?")
                    self.crc = info.get("crc", "")
                    self.name = dev_name
                    return p.device
                s.close()
            except (serial.SerialException, OSError, ValueError):
                pass
        return None

    def close(self):
        if self.ser:
            try:
                self.ser.dtr = False
            except Exception:
                pass
            self.ser.close()
            self.ser = None
        self._rx = b""
        self.port = None
        self.fw = None
        self.crc = None
        self.name = None

    def poll_events(self):
        if not self.ser:
            return
        n = self.ser.in_waiting
        if not n:
            return
        self._rx += self.ser.read(n)
        while b"\n" in self._rx:
            line, self._rx = self._rx.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if "event" in msg and self.on_event:
                self.on_event(msg)

    def _next_line(self):
        if b"\n" not in self._rx:
            self._rx += self.ser.readline()
        if b"\n" not in self._rx:
            self._rx = b""
            return b""
        line, self._rx = self._rx.split(b"\n", 1)
        return line

    def call(self, msg):
        self.poll_events()
        self.ser.write((json.dumps(msg) + "\n").encode())
        while True:
            line = self._next_line()
            if not line:
                raise RuntimeError("device timeout")
            resp = json.loads(line.decode("utf-8", "replace"))
            if "event" in resp:
                if self.on_event:
                    self.on_event(resp)
                continue
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
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
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
        self.btn_test = QPushButton("Test switch (send MIDI)")
        g.addWidget(self.btn_test, len(fields), 0, 1, 2)
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


def fmt_cfg(switches):
    return " | ".join("SW%d: %s" % (i + 1, fmt_sw(s)) for i, s in enumerate(switches))


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI Footswitch Programmer")
        self.dev = Device(on_event=self.on_device_event)
        self.status = QLabel("Not connected")
        self.led = QLabel()
        self.led.setFixedSize(18, 18)
        self.set_led(False)
        self.editors = [SwitchEditor(n) for n in NAMES]
        for i, e in enumerate(self.editors):
            e.btn_test.pressed.connect(lambda sw=i + 1: self.trig(sw, "press"))
            e.btn_test.released.connect(lambda sw=i + 1: self.trig(sw, "release"))

        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(220)
        self.dev_combo.activated.connect(self.on_select_device)
        btn_scan = QPushButton("Refresh")
        btn_scan.clicked.connect(self.refresh_devices)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(28)
        self.name_edit.setPlaceholderText("MIDI Footswitch")
        btn_rename = QPushButton("Rename pedal")
        btn_rename.clicked.connect(self.rename_pedal)

        dev_bar = QHBoxLayout()
        dev_bar.addWidget(QLabel("Pedal:"))
        dev_bar.addWidget(self.dev_combo, 1)
        dev_bar.addWidget(btn_scan)
        dev_bar.addWidget(QLabel("MIDI Name:"))
        dev_bar.addWidget(self.name_edit, 1)
        dev_bar.addWidget(btn_rename)

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

        console_box = QGroupBox("Debug console")
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(500)
        self.console.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.console.setMinimumHeight(110)
        cl = QVBoxLayout(console_box)
        cl.addWidget(self.console)

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
        root.addLayout(dev_bar, 0)
        root.addLayout(bar, 0)
        root.addLayout(row, 0)
        root.addWidget(console_box, 1)
        root.addLayout(foot, 0)
        w = QWidget()
        w.setLayout(root)
        self.setCentralWidget(w)
        self.resize(self.sizeHint().width(), 470)

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(40)
        self.poll_timer.timeout.connect(self._poll_device)

        self.connect(quiet=True)  # one attempt at app start, never again automatically

    def log(self, text):
        self.console.appendPlainText("[%s] %s" % (time.strftime("%H:%M:%S"), text))
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_device_event(self, evt):
        if evt.get("event") == "sw":
            if evt.get("midi"):
                self.log("<- SW%d %s -> MIDI: %s" % (evt["sw"], evt["action"], evt["midi"]))
            else:
                self.log("<- SW%d %s" % (evt["sw"], evt["action"]))

    def _poll_device(self):
        if not self.dev.ser:
            self.poll_timer.stop()
            return
        try:
            self.dev.poll_events()
        except Exception as e:
            self.disconnect()
            self.status.setText("Error: %s" % e)
            self.log("! Serial poll error: %s" % e)

    def set_led(self, on):
        c = "#2e7d32" if on else "#c62828"
        self.led.setStyleSheet("background:%s; border-radius:9px; border:1px solid #333;" % c)

    def refresh_devices(self, _checked=False, *, log_scan=True):
        devices = self.dev.scan()
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for d in devices:
            label = "%s (%s, FW v%s)" % (d["name"], d["port"], d["fw"])
            self.dev_combo.addItem(label, d["port"])
        if self.dev.port:
            idx = self.dev_combo.findData(self.dev.port)
            if idx >= 0:
                self.dev_combo.setCurrentIndex(idx)
        self.dev_combo.blockSignals(False)
        if log_scan:
            if devices:
                summary = ", ".join("%s (%s)" % (d["name"], d["port"]) for d in devices)
                self.log("= Found %d pedal(s): %s" % (len(devices), summary))
            else:
                self.log("! Scan: no pedals found")

    def on_select_device(self, idx):
        port = self.dev_combo.itemData(idx)
        if port and port != self.dev.port:
            self.log("-> Selected pedal on %s" % port)
            self.connect(target_port=port)

    def toggle_connection(self):
        if self.dev.ser:
            self.log("Button [Disconnect]: closing serial port")
            self.disconnect()
            self.status.setText("Disconnected")
        else:
            port = self.dev_combo.currentData()
            self.log("Button [Connect]")
            if not (port and self.connect(target_port=port, quiet=True)):
                self.connect()

    def disconnect(self):
        if hasattr(self, "poll_timer"):
            self.poll_timer.stop()
        self.dev.close()
        self.set_led(False)
        self.btn_conn.setText("Connect")

    def closeEvent(self, event):
        self.disconnect()
        event.accept()

    def guard(self, fn):
        if not self.dev.ser:
            self.status.setText("Not connected - press Connect")
            self.log("! Ignored: not connected")
            return
        try:
            fn()
        except Exception as e:
            self.disconnect()
            self.status.setText("Error: %s" % e)
            self.log("! Error: %s" % e)
            QMessageBox.warning(self, "Device error", str(e))

    def connect(self, quiet=False, target_port=None, prefer_name=None, log_probe=True):
        self.status.setText("Connecting...")
        if log_probe:
            self.log("-> Probing USB serial ports (ping)...")
        QApplication.processEvents()
        port = self.dev.connect(target_port=target_port, prefer_name=prefer_name)
        if port:
            self.set_led(True)
            self.btn_conn.setText("Disconnect")
            self.name_edit.setText(self.dev.name or "MIDI Footswitch")
            self.log("<- Connected '%s' on %s (FW v%s, CRC %s)"
                     % (self.dev.name, port, self.dev.fw, self.dev.crc or "n/a"))
            self.read(log_btn=False)
            self.refresh_devices(log_scan=False)
            self.poll_timer.start()
            self.status.setText("Connected '%s' on %s (FW v%s) - settings loaded  [%s]"
                                % (self.dev.name, port, self.dev.fw, time.strftime("%H:%M:%S")))
            return True
        self.set_led(False)
        self.status.setText("Device not found (plugged in, not in BOOTSEL, no other program using the port?)")
        if log_probe:
            self.log("! Device not found")
        if not quiet:
            QMessageBox.warning(self, "Not found", self.status.text())
        return False

    def _wait_reconnect(self, *, prefer_name=None, initial_delay=1.5, timeout=8.0):
        deadline = time.monotonic() + timeout
        time.sleep(initial_delay)
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if self.connect(quiet=True, prefer_name=prefer_name, log_probe=False):
                return True
            time.sleep(0.4)
        return self.connect(prefer_name=prefer_name)

    def read(self, _checked=False, *, log_btn=True):
        def f():
            if log_btn:
                self.log("Button [Read from device]")
            self.log("-> Read settings (get)")
            cfg = self.dev.call({"cmd": "get"})["config"]
            for e, s in zip(self.editors, cfg["switches"]):
                e.load(s)
            self.log("<- Board settings: " + fmt_cfg(cfg["switches"]))
            self.status.setText("Settings read")
        self.guard(f)

    def trig(self, sw, action):
        def f():
            if action == "press":
                self.log("Button [Test SW%d]: triggering press on board" % sw)
            self.dev.call({"cmd": "trig", "sw": sw, "action": action})
        self.guard(f)

    def rename_pedal(self, _checked=False, *, confirm=True):
        def f():
            self.log("Button [Rename pedal]")
            new_name = clean_name(self.name_edit.text())
            if not new_name:
                msg = "Please enter a non-empty MIDI name (up to 28 ASCII characters)."
                self.status.setText(msg)
                self.log("! " + msg)
                if confirm:
                    QMessageBox.warning(self, "Invalid name", msg)
                return
            self.name_edit.setText(new_name)
            cur_name = self.dev.name or "MIDI Footswitch"
            if new_name == cur_name:
                msg = "MIDI name is already '%s'." % cur_name
                self.status.setText(msg)
                self.log("= MIDI name unchanged ('%s')" % cur_name)
                if confirm:
                    QMessageBox.information(self, "No changes", msg)
                return
            if confirm:
                ans = QMessageBox.question(
                    self,
                    "Confirm rename pedal",
                    "Rename this pedal and restart its USB connection?\n\n"
                    "Current MIDI name: %s\n"
                    "New MIDI name:     %s\n\n"
                    "The pedal will reboot so Windows / macOS / DAWs see the new USB-MIDI device name."
                    % (cur_name, new_name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    self.status.setText("Rename cancelled")
                    self.log("x Rename cancelled by user")
                    return
            self.log("-> Rename pedal (rename): '%s' -> '%s'" % (cur_name, new_name))
            self.dev.call({"cmd": "rename", "name": new_name})
            self.disconnect()
            self.log("<- Renamed, rebooting USB device...")
            self.status.setText("Rebooting pedal as '%s'..." % new_name)
            QApplication.processEvents()
            self._wait_reconnect(prefer_name=new_name)
        self.guard(f)

    def write(self, _checked=False, *, confirm=True):
        def f():
            self.log("Button [Write to device]")
            self.log("-> Read current settings (get)")
            cur = self.dev.call({"cmd": "get"})["config"]["switches"]
            new = [e.dump() for e in self.editors]
            if cur == new:
                self.status.setText("Settings on device already match the GUI")
                self.log("= No changes (board already matches GUI)")
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
                    self.log("x Write cancelled by user")
                    return
            self.log("-> Write settings (set): " + fmt_cfg(new))
            self.dev.call({
                "cmd": "set",
                "config": {"name": self.dev.name or "MIDI Footswitch", "switches": new},
            })
            self.log("<- Settings saved to device")
            self.status.setText("Settings saved to device")
        self.guard(f)

    def reset(self, _checked=False, *, confirm=True):
        def f():
            self.log("Button [Factory defaults]")
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
                    self.log("x Factory reset cancelled by user")
                    return
            self.log("-> Reset to factory defaults (reset)")
            cfg = self.dev.call({"cmd": "reset"})["config"]
            for e, s in zip(self.editors, cfg["switches"]):
                e.load(s)
            self.log("<- Restored defaults: " + fmt_cfg(cfg["switches"]))
            self.status.setText("Defaults restored")
        self.guard(f)

    def update_fw(self, _checked=False, *, confirm=True):
        def f():
            self.log("Button [Update firmware]")
            fw_path = BASE_DIR / "firmware" / "code.py"
            if not fw_path.exists():
                raise RuntimeError("firmware/code.py not found")
            src = fw_path.read_text(encoding="utf-8").replace("\r\n", "\n")
            new_fw, new_crc = inspect_firmware(src)
            ping = self.dev.call({"cmd": "ping"})
            cur_fw = ping.get("fw", "?")
            cur_crc = ping.get("crc", "")
            self.log(
                "-> Check firmware (ping): board FW v%s (CRC %s), file FW v%s (CRC %s)"
                % (cur_fw, cur_crc or "n/a", new_fw, new_crc)
            )

            if cur_fw == new_fw and cur_crc and cur_crc == new_crc:
                msg = "Firmware is already up to date (FW v%s, CRC %s)." % (cur_fw, cur_crc)
                self.status.setText(msg)
                self.log("= Firmware is already up to date")
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
                    self.log("x Firmware update cancelled by user")
                    return

            self.status.setText("Uploading FW v%s to device..." % new_fw)
            self.log("-> Uploading firmware v%s (%d bytes)... (fw_chunk + fw_commit)" % (new_fw, len(src)))
            QApplication.processEvents()
            self.dev.update_firmware(src)
            self.disconnect()
            self.log("<- Firmware committed, rebooting device...")
            self.status.setText("Rebooting device with FW v%s..." % new_fw)
            QApplication.processEvents()
            self._wait_reconnect(initial_delay=2.0, timeout=10.0)
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
