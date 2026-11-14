# Third-party notices

This project redistributes or uses the following third-party material.
Full license texts are in [licenses/](licenses/).

## Redistributed in this repository

| Item | Where | License | Notes |
|------|-------|---------|-------|
| "Rocket Launch" icon, Google Material Symbols | `assets/icon.svg`, `assets/icon.png`, `assets/icon.ico` | Apache License 2.0 ([text](licenses/Apache-2.0.txt)) | Copyright Google LLC. **Modified**: the glyph is recolored white and placed on a rounded gradient tile, then rasterized to PNG/ICO. Source: https://github.com/google/material-design-icons |
| CircuitPython firmware image (`raspberry_pi_pico2_w`, 10.3.2) | `tools/circuitpython-pico2w-10.3.2.uf2` | MIT ([text](licenses/CircuitPython-MIT.txt)) plus licenses of bundled components | Copyright Damien P. George, Adafruit Industries and contributors. Unmodified binary from https://circuitpython.org. Per-component licenses: https://github.com/adafruit/circuitpython/blob/main/LICENSE and https://docs.circuitpython.org/en/latest/README.html#licensing |

## Downloaded at setup time (not stored in this repository)

| Package | License | Notes |
|---------|---------|-------|
| PySide6 (Qt for Python) | LGPL-3.0 / GPL / commercial | Installed by `pip`. If you distribute a built app (`tools/dev.py build`), you must comply with the LGPL for Qt/PySide6 (provide the license text and allow the user to replace the libraries; the default PyInstaller folder build keeps them as separate files). See https://doc.qt.io/qtforpython-6/licenses.html |
| pyserial | BSD-3-Clause ([text](licenses/pyserial-BSD-3-Clause.txt)) | Copyright Chris Liechti |
| PyInstaller | GPL-2.0 with bootloader exception | Build tool only; the exception allows distributing built apps under any license |
| circup | MIT | Dev helper |
