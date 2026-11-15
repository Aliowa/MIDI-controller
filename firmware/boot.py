# boot.py - runs once at power-up, before USB starts.
# Normal mode : the board is writable by the code (GUI can save settings),
#               CIRCUITPY drive is read-only for the PC.
# Dev mode    : hold SW1 (GP10) while plugging in -> PC can edit files on CIRCUITPY.
import binascii
import json
import board
import digitalio
import storage
import supervisor
import usb_cdc
import usb_midi

name = "MIDI Footswitch"
try:
    with open("/config.json") as f:
        raw = json.load(f).get("name", "")
    clean = "".join(c for c in str(raw).strip() if 32 <= ord(c) < 127)[:28].strip()
    if clean:
        name = clean
except Exception:
    pass

try:
    pid = 0x4000 | (binascii.crc32(name.encode("utf-8")) & 0x3FFF)
    supervisor.set_usb_identification(
        manufacturer="Aliowa",
        product=name,
        vid=0x239A,
        pid=pid,
    )
    usb_midi.set_names(audio_control_interface_name=name)
except Exception:
    pass

usb_midi.enable()
usb_cdc.enable(console=True, data=True)  # data channel = config link to the GUI

dev = digitalio.DigitalInOut(board.GP10)
dev.switch_to_input(pull=digitalio.Pull.UP)
dev_mode = not dev.value  # pressed = LOW
dev.deinit()

if not dev_mode:
    storage.remount("/", readonly=False)
