"""3-switch USB MIDI footswitch controller (Pico 2 W, CircuitPython).

MIDI goes out over USB-MIDI. Settings are changed from the GUI over the
USB serial "data" channel using one JSON message per line:
  {"cmd":"ping"}                      -> {"ok":true,"fw":"1.0"}
  {"cmd":"get"}                       -> {"ok":true,"config":{...}}
  {"cmd":"set","config":{...}}        -> {"ok":true}   (validated + saved)
  {"cmd":"reset"}                     -> {"ok":true,"config":{...}} (defaults)
"""
import json
import time

import board
import digitalio
import supervisor
import usb_cdc
import usb_midi

FW_VERSION = "1.0"
CONFIG_FILE = "/config.json"
PINS = (board.GP10, board.GP11, board.GP12)
DEBOUNCE = 0.025

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


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)


config = load_config()
midi = usb_midi.ports[1]
serial = usb_cdc.data

buttons = []
for p in PINS:
    b = digitalio.DigitalInOut(p)
    b.switch_to_input(pull=digitalio.Pull.UP)
    buttons.append(b)

state = [False] * 3        # debounced pressed state
last_raw = [False] * 3
last_change = [0.0] * 3
toggled = [False] * 3
pc_current = [0] * 16      # per-channel program counter for pc_inc / pc_dec


def send(status, d1, d2=None):
    midi.write(bytes([status, d1] if d2 is None else [status, d1, d2]))


def on_press(i):
    s = config["switches"][i]
    ch = s["channel"] - 1
    t = s["type"]
    if t == "pc":
        send(0xC0 | ch, s["number"])
        pc_current[ch] = s["number"]
    elif t == "pc_inc":
        pc_current[ch] = (pc_current[ch] + 1) % 128
        send(0xC0 | ch, pc_current[ch])
    elif t == "pc_dec":
        pc_current[ch] = (pc_current[ch] - 1) % 128
        send(0xC0 | ch, pc_current[ch])
    else:
        status = 0xB0 if t == "cc" else 0x90
        if s["mode"] == "toggle":
            toggled[i] = not toggled[i]
            val = s["value"] if toggled[i] else 0
        else:
            val = s["value"]
        send(status | ch, s["number"], val)


def on_release(i):
    s = config["switches"][i]
    if s["type"] in ("cc", "note") and s["mode"] == "momentary":
        status = 0xB0 if s["type"] == "cc" else 0x90
        send(status | (s["channel"] - 1), s["number"], 0)


rx = b""


def reply(obj):
    serial.write((json.dumps(obj) + "\n").encode())


def handle(line):
    global config
    try:
        msg = json.loads(line)
        cmd = msg.get("cmd")
        if cmd == "ping":
            reply({"ok": True, "fw": FW_VERSION})
        elif cmd == "get":
            reply({"ok": True, "config": config})
        elif cmd == "set":
            new = validate(msg["config"])
            save_config(new)
            config = new
            reply({"ok": True})
        elif cmd == "reset":
            config = json.loads(json.dumps(DEFAULT_CONFIG))
            save_config(config)
            reply({"ok": True, "config": config})
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
        while b"\n" in rx:
            line, rx = rx.split(b"\n", 1)
            if line.strip():
                handle(line.decode())
