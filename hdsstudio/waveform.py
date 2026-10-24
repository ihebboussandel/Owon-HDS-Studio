"""Offline one-cycle waveform files. These functions never access USB."""
import csv
import json
from pathlib import Path
import numpy as np
from .waveform_shapes import TEMPLATES, template_samples, validate_recipe

MAX_POINTS = 8192

class WaveformLibrary:
    """Named local cycles. Saving never communicates with an instrument."""
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.entries = {}
        self.warning = ""
        if self.path and self.path.exists():
            try:
                if self.path.stat().st_size > 12_000_000:
                    raise ValueError("Waveform library exceeds 12 MB")
                entries = json.loads(self.path.read_text(encoding="utf8"))
                if not isinstance(entries, dict) or len(entries) > 50:
                    raise ValueError("Waveform library must contain at most 50 named cycles")
                for name, entry in entries.items():
                    self._validate(name, entry)
                self.entries = entries
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.warning = "Cannot read waveform library: " + str(exc)

    @staticmethod
    def _validate(name, entry):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Give this cycle a name of 1–80 characters")
        voltage_values(entry["samples"], entry["amplitude"], entry["offset"])
        if entry.get("recipe") is not None:
            validate_recipe(entry["recipe"])

    def save(self, name, samples, amplitude, offset, recipe=None):
        name = name.strip()
        entry = dict(samples=np.asarray(samples, dtype=float).tolist(), amplitude=float(amplitude), offset=float(offset))
        if recipe is not None:
            entry["recipe"] = validate_recipe(recipe)
        self._validate(name, entry)
        if name not in self.entries and len(self.entries) >= 50:
            raise ValueError("Library is full (50 cycles); reuse an existing name or export CSV")
        updated = dict(self.entries, **{name: entry})
        if self.path:
            if self.warning:
                raise ValueError(self.warning + "; export CSV to preserve this cycle")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(updated, allow_nan=False), encoding="utf8")
            temporary.replace(self.path)
        self.entries = updated
        return name


def voltage_values(samples, amplitude, offset):
    y = np.asarray(samples, dtype=float)
    if y.ndim != 1 or not 16 <= len(y) <= MAX_POINTS or not np.all(np.isfinite(y)) or np.max(np.abs(y)) > 1+1e-9:
        raise ValueError("Use 16–8192 finite normalized samples between −1 and +1")
    amplitude, offset = float(amplitude), float(offset)
    if not .02 <= amplitude <= 5 or not np.isfinite(offset) or abs(offset)+amplitude/2 > 2.5+1e-9:
        raise ValueError("Keep amplitude at 20 mVpp–5 Vpp and the full envelope within ±2.5 V")
    return offset+amplitude/2*y


def read_waveform_csv(path, count=1024, unit="normalized", amplitude=1., offset=0.):
    path = Path(path)
    if path.stat().st_size > 4_000_000:
        raise ValueError("CSV is too large; use at most 100,000 rows and 4 MB")
    text = path.read_text(encoding="utf-8-sig")
    lines = [s for s in text.splitlines() if s.strip() and not s.lstrip().startswith("#")]
    if not lines:
        raise ValueError("CSV is empty")
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines[:10]), delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(lines, dialect))
    if len(rows) > 100001:
        raise ValueError("CSV has more than 100,000 data rows")
    try:
        [float(x) for x in rows[0]]
        header = None
    except ValueError:
        header = [x.strip().lower() for x in rows.pop(0)]
        known = {"phase", "time", "time_s", "position", "normalized", "voltage_v", "voltage", "volts", "amplitude", "sample", "index", "value", "x", "y"}
        if not all(x in known for x in header):
            raise ValueError("Unrecognized CSV header or a non-numeric first data row")
    if len(rows) < 2:
        raise ValueError("CSV needs at least two data rows")
    if unit not in ("normalized", "V"):
        raise ValueError("Choose normalized or voltage samples")
    columns = len(rows[0])
    if not 1 <= columns <= 3 or any(len(row) != columns for row in rows):
        raise ValueError("Use one amplitude column, two position/amplitude columns, or the exported phase/normalized/voltage_V format")
    xcol, ycol = (None, 0) if columns == 1 else (0, 1)
    if header and "normalized" in header:
        ycol, unit = header.index("normalized"), "normalized"
        xcol = header.index("phase") if "phase" in header else xcol
    elif header and "voltage_v" in header:
        ycol, unit = header.index("voltage_v"), "V"
    try:
        values = np.array([[float(x) for x in row] for row in rows], dtype=float)
    except ValueError as exc:
        raise ValueError("CSV values must be decimal numbers using a decimal point") from exc
    if not np.all(np.isfinite(values)):
        raise ValueError("CSV contains NaN or infinite values")
    x = np.linspace(0, 1, len(values)) if xcol is None else values[:, xcol]
    if np.any(np.diff(x) <= 0):
        raise ValueError("Time/phase positions must be strictly increasing")
    x = (x-x[0])/(x[-1]-x[0])
    y = values[:, ycol]
    if unit == "V":
        voltage_values(np.zeros(16), amplitude, offset)
        y = (y-offset)/(amplitude/2)
    if np.max(np.abs(y)) > 1+1e-9:
        raise ValueError("Samples exceed −1…+1. For voltage CSV choose Volts and set an amplitude/offset that contains the data")
    if not 16 <= int(count) <= MAX_POINTS:
        raise ValueError("Sample count must be 16–8192")
    result = np.interp(np.linspace(0, 1, int(count)), x, y)
    return np.clip(result, -1, 1)


def save_waveform_csv(path, samples, amplitude=1., offset=0.):
    volts = voltage_values(samples, amplitude, offset)
    phase = np.linspace(0, 1, len(volts))
    with Path(path).open("w", encoding="utf8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phase", "normalized", "voltage_V"])
        writer.writerows(zip(phase, samples, volts))
