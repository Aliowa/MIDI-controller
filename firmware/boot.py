# boot.py - runs once at power-up, before USB starts.
# Normal mode : the board is writable by the code (GUI can save settings),
#               CIRCUITPY drive is read-only for the PC.
# Dev mode    : hold SW1 (GP10) while plugging in -> PC can edit files on CIRCUITPY.
import board
import digitalio
import storage
import usb_cdc
import usb_midi

usb_midi.enable()
usb_cdc.enable(console=True, data=True)  # data channel = config link to the GUI

dev = digitalio.DigitalInOut(board.GP10)
dev.switch_to_input(pull=digitalio.Pull.UP)
dev_mode = not dev.value  # pressed = LOW
dev.deinit()

if not dev_mode:
    storage.remount("/", readonly=False)
