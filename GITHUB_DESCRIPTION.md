# GitHub repository description

Suggested repository name: **`hds2202s-studio`**

## About text

Copy this into GitHub's repository description field:

```text
Linux-focused Python desktop app for the OWON HDS2202S: oscilloscope, multimeter, generator controls, CSV waveform design/export, software math, FFT spectrum analysis, recording, and three themes. Community improvements welcome.
```

## Suggested topics

```text
linux linux-hardware python owon hds2202s oscilloscope multimeter function-generator scpi usb pyside6 pyqtgraph signal-processing fft spectrum-analyzer
```

## Project summary

HDS Studio is an independent community project intended to bring Linux support to the OWON HDS2202S. It combines oscilloscope, multimeter, and waveform-generator workspaces with host-side signal processing, FFT spectrum analysis, recording, and capture replay. The interface includes Midnight, Daylight, and Phosphor themes, and a synthetic demo mode for exploring the app without hardware.

The application uses PySide6, pyqtgraph, NumPy, SciPy, and PyUSB. Remote controls include command readback, while waveform files retain their instrument header and capture provenance. The oscilloscope view includes live trigger labels and a voltage-threshold marker. Multimeter units follow function/range readback, with stable statistics cards. Generator selection and local editing remain available without hardware readback; applying changes requires loaded device settings. Its selector includes all 12 device shapes, CSV import, drawing, and saved local cycles. An offline editor adds 17 templates with dedicated parameters—staircase levels/dwell/turnaround, sinc width/zeros/taper, Bessel order/span/decay, burst envelopes, Gaussian width, and chirp sweep law—and a persistent waveform library that retains editable recipes. Recipe-aware undo restores recent edits. Imported and drawn samples are preserved until Generate shape is chosen; custom-waveform USB upload is unavailable. Signal/Timing/Load menus follow the waveform, with linked frequency/period, amplitude-offset/high-low, and pulse width/duty editing. Waveform configurations add PWM duty, pulse width, rise/fall timing, and triangle/sawtooth setups. Each numeric parameter has independent step sizes, ± buttons, and keyboard fine/coarse adjustment; workspace presets retain these preferences. Explicit Apply works with output on and verifies paired voltage readback. A local AM/FM designer adds modulation shape, frequency, depth/deviation, and CSV export; native hardware modulation is unverified. The device load ON/OFF control is separate from the numeric local load preview. A dismissible notification reminds USB users to press Mode on the instrument, since the firmware protocol does not support switching its screen mode from the app. The repository includes 118 automated tests and 39 screenshots covering every workspace and theme.

## A kind invitation to contribute

If you own this scope or would like to help improve its Linux support, please feel welcome to share your own improvements. Issues, ideas, documentation, hardware test results, and pull requests are all appreciated. Your contributions can help make the software more useful for everyone. Thank you!

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and hardware-reporting notes.

## Status for the repository page

The demo UI and automated checks have been exercised locally, including decoding a previously recorded HDS2202S waveform fixture. Live USB hardware operation still requires on-device validation. Screen-export math and spectrum results are estimates; full details are in the user guide.

## Links within this repository

- [Project README](README.md)
- [User guide](docs/USER_GUIDE.md)
- [Screenshot gallery](screenshots/README.md)
- [Contributing](CONTRIBUTING.md)
