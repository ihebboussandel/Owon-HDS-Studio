"""Host-side signal math and spectrum analysis; no instrument I/O."""
import ast
from dataclasses import dataclass
import operator
import numpy as np
from scipy import signal, integrate


def statistics(t, y):
    t, y = np.asarray(t), np.asarray(y)
    if len(t) < 4 or not np.all(np.isfinite(y)):
        raise ValueError("A finite waveform with at least four points is required")
    ac = y - y.mean()
    lo, hi = np.percentile(y, [10, 90])
    threshold = (lo + hi) / 2
    crossing = np.flatnonzero((y[:-1] < threshold) & (y[1:] >= threshold))
    freq = np.nan
    if len(crossing) >= 3 and np.ptp(y) > 1e-12:
        tx = t[crossing] + (threshold - y[crossing]) / (y[crossing + 1] - y[crossing]) * (t[crossing + 1] - t[crossing])
        periods = np.diff(tx)
        median = np.median(periods)
        if median > 0 and np.std(periods) / median < .15:
            freq = 1 / median
    return dict(Min=float(y.min()), Max=float(y.max()), Vpp=float(np.ptp(y)), Mean=float(y.mean()),
                RMS=float(np.sqrt(np.mean(y*y))), AC_RMS=float(np.sqrt(np.mean(ac*ac))),
                Std=float(np.std(y)), Frequency=float(freq), Period=float(1/freq) if freq > 0 else np.nan,
                Duty=float(100*np.mean(y > threshold)) if hi-lo > 1e-12 else np.nan,
                Crest=float(np.max(np.abs(y))/np.sqrt(np.mean(y*y))) if np.any(y) else np.nan)


class MathEngine:
    """Small AST interpreter: never evaluates Python code, attributes or imports."""
    def evaluate(self, expression, t, ch1, ch2):
        if len(expression) > 500:
            raise ValueError("Expression is too long")
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 120:
            raise ValueError("Expression is too complex")
        t = np.asarray(t, dtype=float)
        if t.ndim != 1 or len(t) < 4 or not np.all(np.isfinite(t)):
            raise ValueError("Math needs at least four finite time points")
        dt = float(np.median(np.diff(t)))
        if dt <= 0 or not np.allclose(np.diff(t), dt, rtol=1e-5, atol=dt*1e-6):
            raise ValueError("Math requires a uniform increasing time grid")
        env = {"CH1": np.asarray(ch1), "CH2": np.asarray(ch2), "t": t, "pi": np.pi, "e": np.e}

        def smooth(y, n=7):
            n = int(n)
            if not 1 <= n <= min(101, len(t)):
                raise ValueError("Smooth width must be 1–101 and fit the record")
            return np.convolve(np.pad(y, (n//2, n-1-n//2), mode="edge"), np.ones(n)/n, mode="valid")

        def filt(y, cutoff, kind):
            cutoff = float(cutoff)
            if not 0 < cutoff < .5/dt:
                raise ValueError(f"Filter cutoff must be below the export Nyquist frequency ({.5/dt:g} Hz)")
            return signal.sosfiltfilt(signal.butter(4, cutoff, btype=kind, fs=1/dt, output="sos"), y)

        def bandpass(y, low, high):
            if not 0 < float(low) < float(high) < .5/dt:
                raise ValueError("Bandpass edges must be ordered and below the export Nyquist frequency")
            return signal.sosfiltfilt(signal.butter(4, [low, high], btype="bandpass", fs=1/dt, output="sos"), y)

        funcs = {"abs": np.abs, "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
                 "sin": np.sin, "cos": np.cos, "tan": np.tan, "diff": lambda y: np.gradient(y, dt),
                 "integ": lambda y: integrate.cumulative_trapezoid(y, t, initial=0), "smooth": smooth,
                 "lowpass": lambda y, f: filt(y, f, "lowpass"), "highpass": lambda y, f: filt(y, f, "highpass"),
                 "envelope": lambda y: np.abs(signal.hilbert(y)),
                 "phase": lambda y: np.unwrap(np.angle(signal.hilbert(y))),
                 "ac": lambda y: y-np.mean(y), "clip": np.clip, "bandpass": bandpass,
                 "rms": lambda y: np.sqrt(np.mean(y*y)), "mean": np.mean, "min": np.min, "max": np.max}
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

        def visit(node, depth=0):
            if depth > 24:
                raise ValueError("Expression nesting is too deep")
            v = lambda x: visit(x, depth+1)
            if isinstance(node, ast.Expression):
                return v(node.body)
            if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) <= 1e12:
                return float(node.value)
            if isinstance(node, ast.Name) and node.id in env:
                return env[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return v(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp):
                a, b = v(node.left), v(node.right)
                if type(node.op) in ops:
                    return ops[type(node.op)](a, b)
                if isinstance(node.op, ast.Pow) and np.ndim(b) == 0 and abs(b) <= 12:
                    return np.power(a, b)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in funcs and not node.keywords:
                return funcs[node.func.id](*[v(x) for x in node.args])
            raise ValueError("Use CH1, CH2, t, numbers and the listed math functions only")

        with np.errstate(all="ignore"):
            result = np.broadcast_to(np.asarray(visit(tree), dtype=float), t.shape).copy()
        if not np.all(np.isfinite(result)):
            raise ValueError("Math produced undefined values; check division, log and square root inputs")
        return result


@dataclass
class Spectrum:
    frequency: np.ndarray
    rms: np.ndarray
    psd: np.ndarray
    bin_hz: float
    enbw_hz: float
    nyquist_hz: float
    peak_hz: float
    thd_percent: float
    harmonic_count: int


def spectrum(t, y, window="hann", remove_dc=True):
    t, y = np.asarray(t), np.asarray(y, dtype=float)
    if len(y) < 16 or y.shape != t.shape or not np.all(np.isfinite(y)):
        raise ValueError("Spectrum needs at least 16 finite samples")
    dt = float(np.median(np.diff(t)))
    if dt <= 0 or not np.allclose(np.diff(t), dt, rtol=1e-5, atol=dt*1e-6):
        raise ValueError("Spectrum requires uniform sample spacing")
    fs, n = 1/dt, len(y)
    w = signal.get_window(window, n, fftbins=True)
    if remove_dc:
        y = y - y.mean()
    transform = np.fft.rfft(y*w)
    factors = np.full(len(transform), 2.)
    factors[0] = 1
    if n % 2 == 0:
        factors[-1] = 1
    rms = np.abs(transform) / w.sum() * np.sqrt(factors)
    psd = np.abs(transform)**2 / (fs*np.sum(w*w)) * factors
    freq = np.fft.rfftfreq(n, dt)
    peak = int(np.argmax(rms[1:])+1)
    df = fs/n
    peak_hz = freq[peak] if rms[peak] > 1e-14 else np.nan
    # Integrate lobes around the fundamental/harmonics. No claim for unresolved
    # harmonics, aliasing, or signals whose fundamental isn't the dominant peak.
    radius = {"boxcar": 0, "hann": 1, "hamming": 1, "blackmanharris": 3, "flattop": 4}[window]
    power = lambda k: float(np.sum(psd[max(1,k-radius):min(len(psd),k+radius+1)]))
    fundamental_power = power(peak)
    harmonics = [h*peak for h in range(2, 6) if h*peak+radius < len(freq)]
    resolved = peak > 2*radius+1
    thd = 100*np.sqrt(sum(power(k) for k in harmonics)/fundamental_power) if fundamental_power > 1e-24 and harmonics and resolved else np.nan
    return Spectrum(freq, rms, psd, df, fs*np.sum(w*w)/w.sum()**2, fs/2, peak_hz,
                    float(thd), len(harmonics) if resolved else 0)


def spectrum_values(result, unit="dBV", resistance=50.):
    if resistance <= 0:
        raise ValueError("Reference resistance must be positive")
    if unit == "Vrms":
        return result.rms
    if unit == "dBV":
        return 20*np.log10(np.maximum(result.rms, 1e-15))
    if unit == "dBm":
        return 10*np.log10(np.maximum(result.rms**2/resistance/.001, 1e-30))
    if unit == "V²/Hz":
        return result.psd
    return 10*np.log10(np.maximum(result.psd, 1e-30))
