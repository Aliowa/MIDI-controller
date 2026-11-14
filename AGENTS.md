# AGENTS.md - guide for AI agents and developers

3-switch USB MIDI footswitch (Raspberry Pi **Pico 2 W**, CircuitPython) for Neural DSP, plus a desktop GUI (Windows + macOS) to program it over USB.

## Layout

| Path | Purpose |
|------|---------|
| `firmware/boot.py` | Runs at power-up: enables USB-MIDI + second serial (data) port, makes flash writable by code |
| `firmware/code.py` | Main loop: reads 3 switches, sends MIDI, handles JSON config commands |
| `gui/app.py` | PySide6 programmer (talks to the data serial port) |
| `docs/schematic.svg`, `docs/README.md` | Wiring: SW1-3 on GP10/GP11/GP12 to GND (internal pull-ups, no resistors); reserved I2C header GP4/GP5 |
| `tools/circuitpython-pico2w-10.3.2.uf2` | CircuitPython firmware image for the board |
| `requirements.txt` | Python deps for the GUI |

## Dependencies

- Python 3.11+ (dev machine: 3.13 on Windows)
- GUI: `PySide6`, `pyserial` (packaging: `pyinstaller`; board libs helper: `circup`)
- Board: CircuitPython **10.3.2** for `raspberry_pi_pico2_w`. Only built-in modules are used (`usb_midi`, `usb_cdc`, `digitalio`, `json`); no `/lib` files needed.
- Drivers: none on Windows 10/11 or macOS (class-compliant USB MIDI + CDC).

## Setup (macOS or Windows)

`.venv` is not committed (it is OS-specific); each machine creates its own with one script that works on any OS (needs only Python 3.11+):

```
python3 tools/dev.py setup    # Windows: python tools\dev.py setup
python3 tools/dev.py run      # start the GUI
python3 tools/dev.py build    # standalone app into dist/ (.app on macOS, .exe on Windows)
```

macOS: if setup fails installing PySide6, use python.org Python 3.11+ (not the system Python).

## Flashing the board

1. Hold **BOOTSEL**, plug in USB (use a DATA cable). Drive `RP2350` appears.
2. Copy the `.uf2` from `tools/` onto it. Board reboots as drive `CIRCUITPY`.
3. Copy `firmware/boot.py` and `firmware/code.py` to `CIRCUITPY`.
4. **Hard reset** (unplug/replug). `boot.py` only runs on hard reset, not on save.

## IMPORTANT behaviors

- After `boot.py` runs, `CIRCUITPY` is **read-only to the PC** (so the firmware can save `config.json`). To edit firmware files: hold **SW1 (GP10 to GND)** while plugging in. If no switch is wired, jumper GP10 (pin 14) to GND (pin 13).
- Recovery: BOOTSEL mode + flash `flash_nuke.uf2` (Raspberry Pi) then reflash CircuitPython.
- Board exposes 2 serial ports: **console** (REPL/debug, first) and **data** (second, used by GUI). GUI finds the right one by USB VID `0x239A` and probing `{"cmd":"ping"}`.
  - Windows: `COM3` = console, `COM4` = data (numbers vary).
  - macOS: `/dev/cu.usbmodem*` (two entries; data is usually the higher/second one).
- MIDI device name appears as **CircuitPython Audio** (Windows/Reaper) / `CircuitPython usb midi` (macOS).

## Serial protocol (data port, 115200, one JSON object per line)

```
{"cmd":"ping"}                  -> {"ok":true,"fw":"1.0"}
{"cmd":"get"}                   -> {"ok":true,"config":{"switches":[...3...]}}
{"cmd":"set","config":{...}}    -> {"ok":true}     (validated, saved to /config.json)
{"cmd":"reset"}                 -> {"ok":true,"config":{...}}
```

Switch fields: `type` (`pc|pc_inc|pc_dec|cc|note`), `channel` 1-16, `number` 0-127, `value` 0-127, `mode` (`momentary|toggle|trigger`).

## Debugging checklist (mac or any host)

1. List ports: `python -m serial.tools.list_ports -v` (look for VID:PID 239A:xxxx, two ports).
2. Console output: `screen /dev/cu.usbmodemXXXX 115200` (first port). Ctrl-C then Ctrl-D reloads.
3. Test protocol without GUI: send `{"cmd":"ping"}\n` to the data port.
4. MIDI check: macOS *Audio MIDI Setup* / *MIDI Monitor*, or Reaper (Preferences, Audio, MIDI Inputs, enable device).
5. Only one program should hold the data port; GUI releases it on Disconnect/close.
6. No data port visible? `boot.py` did not run: do a hard reset (unplug/replug).

## Conventions

- Keep firmware dependency-free (stdlib/CircuitPython built-ins only).
- Keep protocol changes backward compatible; bump `FW_VERSION` in `code.py`.
- Pin map must stay in sync across `firmware/code.py`, `firmware/boot.py`, `docs/`.
- Hardware note: switches are normally-open momentary SPST (no resistors, active-low).
