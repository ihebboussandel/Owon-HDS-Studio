"""Portable capture exports and append-only reading logs."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np
from .device import Frame


def save_capture(path, frame, math_trace=None, expression=""):
    path = Path(path)
    document = dict(header=frame.header, metadata=frame.metadata, math_expression=expression if math_trace is not None else "")
    arrays = dict(time_s=frame.t, CH1_V=frame.channels[0], CH2_V=frame.channels[1])
    if math_trace is not None:
        arrays["MATH"] = math_trace
    for name, (lo, hi) in frame.envelopes.items():
        arrays[name+"_min_V"], arrays[name+"_max_V"] = lo, hi
    if path.suffix.lower() == ".npz":
        np.savez_compressed(path, **arrays, document_json=np.array(json.dumps(document)))
    else:
        with path.open("w", newline="", encoding="utf8") as f:
            writer = csv.writer(f)
            writer.writerow(arrays)
            writer.writerows(zip(*arrays.values()))
        path.with_suffix(path.suffix+".json").write_text(json.dumps(document, indent=2), encoding="utf8")
    return path


def load_capture(path):
    with np.load(path, allow_pickle=False) as data:
        t, a, b = (np.array(data[key], copy=True) for key in ("time_s", "CH1_V", "CH2_V"))
        doc = json.loads(str(data["document_json"]))
        if t.ndim != 1 or len(t) < 4 or a.shape != t.shape or b.shape != t.shape or not np.all(np.isfinite(t)) or np.any(np.diff(t) <= 0):
            raise ValueError("Invalid capture arrays")
        envelopes = {name: (np.array(data[name+"_min_V"]), np.array(data[name+"_max_V"]))
                     for name in ("CH1", "CH2") if name+"_min_V" in data}
    metadata = dict(doc["metadata"], replay=True, original_source=doc["metadata"].get("source"))
    metadata["source"] = "REPLAY • " + str(path)
    return Frame(t, np.array([a, b]), doc["header"], metadata, envelopes)


class ReadingLog:
    def __init__(self, path):
        self.file = Path(path).open("x", encoding="utf8", newline="")
        self.writer = csv.writer(self.file)
        self.writer.writerow(["host_utc", "reading_text", "numeric_SI", "unit", "identity", "function", "range", "relative"])

    def append(self, reading, value, unit, function, range_text, relative):
        self.writer.writerow([datetime.fromtimestamp(reading["host_time"], timezone.utc).isoformat(), reading["text"],
                              value, unit, reading["source"], function, range_text, relative])
        self.file.flush()

    def close(self):
        self.file.close()
