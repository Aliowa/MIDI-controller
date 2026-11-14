"""3-switch USB MIDI footswitch controller (Pico 2 W, CircuitPython).

MIDI goes out over USB-MIDI. Settings and firmware updates are sent from the GUI
over the USB serial "data" channel using one JSON message per line:
  {"cmd":"ping"}                                  -> {"ok":true,"fw":"1.4","crc":"..."}
  {"cmd":"get"}                                   -> {"ok":true,"config":{...}}
  {"cmd":"set","config":{...}}                    -> {"ok":true}   (validated + saved)
  {"cmd":"reset"}                                 -> {"ok":true,"config":{...}} (defaults)
  {"cmd":"trig","sw":1..3,"action":"press"|"release"} -> {"ok":true} (fires switch MIDI)
  {"cmd":"fw_chunk","reset":bool,"data":"..."}    -> {"ok":true}   (buffers firmware over USB)
  {"cmd":"fw_commit"}                             -> {"ok":true}   (validates, writes /code.py, reloads)
  (device -> host) {"event":"sw","sw":1..3,"action":"pressed"|"released","midi":"..."}
"""
import binascii
import json
import time

import board
import digitalio
import supervisor
import usb_cdc
import usb_midi

FW_VERSION = "1.4"
CONFIG_FILE = "/config.json"
PINS = (board.GP10, board.GP11, board.GP12)
DEBOUNCE = 0.025
MAX_RX = 4096

# type: pc | pc_inc | pc_dec | cc | note
# mode (cc/note): momentary (value on press, 0 on release) | toggle | trigger
DEFAULT_CONFIG = {
    "switches": [
        {"type": "pc_dec", "channel": 1, "number": 0, "value": 127, "mode": "trigger"},
        {"type": "pc_inc", "channel": 1, "number": 0, "value": 127, "mode": "trigger"},
        {"type": "cc", "channel": 1, "number": 64, "value": 127, "mode": "momentary"},
    ]
}

TYPES = ("pc", "pc_inc", "pc_dec", "cc", "note")
MODES = ("momentary", "toggle", "trigger")


def clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))


def validate(cfg):
    out = []
    sws = cfg["switches"]
    if len(sws) != 3:
        raise ValueError("need exactly 3 switches")
    for s in sws:
        if s["type"] not in TYPES or s["mode"] not in MODES:
            raise ValueError("bad type/mode")
        out.append({
            "type": s["type"],
            "channel": clamp(s["channel"], 1, 16),
            "number": clamp(s["number"], 0, 127),
            "value": clamp(s["value"], 0, 127),
            "mode": s["mode"],
        })
    return {"switches": out}


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return validate(json.load(f))
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))


def write_flash(path, text):
    try:
        with open(path, "w") as f:
            f.write(text)
    except OSError as e:
        raise RuntimeError(
            "Flash is read-only (dev mode). Replug without holding SW1."
        ) from e


def save_config(cfg):
    write_flash(CONFIG_FILE, json.dumps(cfg))


config = load_config()
midi = usb_midi.ports[1] if len(usb_midi.ports) > 1 else None
serial = usb_cdc.data
if serial:
    try:
        serial.write_timeout = 0.01
    except Exception:
        pass

buttons = []
for p in PINS:
    b = digitalio.DigitalInOut(p)
    b.switch_to_input(pull=digitalio.Pull.UP)
    buttons.append(b)

state = [False] * 3        # debounced pressed state
last_raw = [False] * 3
last_change = [0.0] * 3
toggled = [False] * 3
active_press = [None] * 3  # (status_byte, number, type, ch) held in momentary mode
pc_current = [0] * 16      # per-channel program counter for pc_inc / pc_dec
fw_buf = []


def send(status, d1, d2=None):
    if midi:
        midi.write(bytes([status, d1] if d2 is None else [status, d1, d2]))


def on_press(i):
    s = config["switches"][i]
    ch = s["channel"] - 1
    t = s["type"]
    if t == "pc":
        num = s["number"]
        send(0xC0 | ch, num)
        pc_current[ch] = num
        midi_desc = "PC #%d (Ch %d)" % (num, ch + 1)
    elif t == "pc_inc":
        pc_current[ch] = (pc_current[ch] + 1) % 128
        num = pc_current[ch]
        send(0xC0 | ch, num)
        midi_desc = "PC +1 -> #%d (Ch %d)" % (num, ch + 1)
    elif t == "pc_dec":
        pc_current[ch] = (pc_current[ch] - 1) % 128
        num = pc_current[ch]
        send(0xC0 | ch, num)
        midi_desc = "PC -1 -> #%d (Ch %d)" % (num, ch + 1)
    else:
        status = (0xB0 if t == "cc" else 0x90) | ch
        if s["mode"] == "toggle":
            toggled[i] = not toggled[i]
            val = s["value"] if toggled[i] else 0
        else:
            val = s["value"]
            if s["mode"] == "momentary":
                active_press[i] = (status, s["number"], t, ch + 1)
        send(status, s["number"], val)
        midi_desc = "%s #%d = %d (%s, Ch %d)" % (
            t.upper(), s["number"], val, s["mode"], ch + 1
        )
    notify({"event": "sw", "sw": i + 1, "action": "pressed", "midi": midi_desc})


def on_release(i):
    if active_press[i] is not None:
        status, num, t, ch = active_press[i]
        active_press[i] = None
        send(status, num, 0)
        midi_desc = "%s #%d = 0 (momentary, Ch %d)" % (t.upper(), num, ch)
    else:
        midi_desc = None
    notify({"event": "sw", "sw": i + 1, "action": "released", "midi": midi_desc})


rx = b""


def reply(obj):
    if serial:
        try:
            serial.write((json.dumps(obj) + "\n").encode())
        except Exception:
            pass


def notify(obj):
    if serial and getattr(serial, "connected", False):
        reply(obj)


def fw_crc():
    try:
        with open("/code.py", "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        return "%08x" % (binascii.crc32(data) & 0xFFFFFFFF)
    except Exception:
        return ""


def handle(line):
    global config
    try:
        msg = json.loads(line)
        cmd = msg.get("cmd")
        if cmd == "ping":
            reply({"ok": True, "fw": FW_VERSION, "crc": fw_crc()})
        elif cmd == "get":
            reply({"ok": True, "config": config})
        elif cmd == "set":
            new = validate(msg["config"])
            config = new
            toggled[:] = [False] * 3
            save_config(new)
            reply({"ok": True})
        elif cmd == "reset":
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            toggled[:] = [False] * 3
            save_config(config)
            reply({"ok": True, "config": config})
        elif cmd == "trig":
            idx = clamp(msg["sw"], 1, 3) - 1
            if msg.get("action") == "release":
                on_release(idx)
            else:
                on_press(idx)
            reply({"ok": True})
        elif cmd == "fw_chunk":
            if msg.get("reset"):
                fw_buf.clear()
            fw_buf.append(msg["data"])
            reply({"ok": True})
        elif cmd == "fw_commit":
            src = "".join(fw_buf)
            fw_buf.clear()
            if not src.strip():
                raise ValueError("empty firmware")
            compile(src, "code.py", "exec")
            write_flash("/code.py", src)
            reply({"ok": True})
            time.sleep(0.05)
            supervisor.reload()
        else:
            reply({"ok": False, "error": "unknown cmd"})
    except Exception as e:  # keep running no matter what the host sends
        reply({"ok": False, "error": str(e)})


while True:
    now = time.monotonic()
    for i, b in enumerate(buttons):
        raw = not b.value
        if raw != last_raw[i]:
            last_raw[i] = raw
            last_change[i] = now
        elif raw != state[i] and now - last_change[i] >= DEBOUNCE:
            state[i] = raw
            (on_press if raw else on_release)(i)

    if serial and serial.in_waiting:
        rx += serial.read(serial.in_waiting)
        if len(rx) > MAX_RX:
            rx = b""
        while b"\n" in rx:
            line, rx = rx.split(b"\n", 1)
            if line.strip():
                handle(line.decode("utf-8", "replace"))

    time.sleep(0.001)
