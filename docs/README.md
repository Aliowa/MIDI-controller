# 3-Switch USB MIDI Controller (Pico 2 W) for Neural DSP

Schematic: [schematic.svg](schematic.svg)

## Wiring (no resistors, no extra parts)

Each switch is a 2-terminal momentary SPST. One terminal goes to a GPIO, the other to GND. Enable the GPIO internal pull-up in firmware (`Pull.UP`). Pressed = LOW.

| Part | Terminal | Pico pin | GPIO |
|------|----------|----------|------|
| SW1 (Prev / Bank-) | A | 14 | GP10 |
| SW2 (Next / Bank+) | A | 15 | GP11 |
| SW3 (Tap / Tuner)  | A | 16 | GP12 |
| SW1, SW2, SW3 | B (common) | 13 | GND |

Pin 13 is a GND pin right next to GP10-GP12. Terminal polarity doesn't matter on a momentary switch. All three B terminals can be daisy-chained to one GND wire.

## Reserved: screen header (not populated)

| Header | Pico pin | Signal |
|--------|----------|--------|
| 1 SDA | 6  | GP4 (I2C0 SDA) |
| 2 SCL | 7  | GP5 (I2C0 SCL) |
| 3 3V3 | 36 | 3V3 OUT |
| 4 GND | 38 | GND |

Leave GP4, GP5, 3V3 and pins 36/38 unused until a screen is added.

## USB

The micro-USB cable to the PC carries both power and USB-MIDI. Neural DSP should see the board as a MIDI input.

## Notes

- Hold-to-use footswitches are normally open; either terminal works.
- Add a 100 nF cap per switch later only if debounce in software isn't enough (not needed now).

## Icon

Rocket Launch icon from Google Material Symbols (Apache 2.0) on a gradient tile. Sources in `assets/`.
