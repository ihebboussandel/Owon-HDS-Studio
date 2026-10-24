import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from hdsstudio.analysis import MathEngine, spectrum, spectrum_values, statistics
from hdsstudio.commands import BY_KEY, SETTINGS, quantity, validate_command
from hdsstudio.device import DemoTransport, capture, decode_frame, lower_keys, trigger_readout
from hdsstudio.storage import ReadingLog, load_capture, save_capture
from hdsstudio.transport import USBTransport
from hdsstudio.worker import InstrumentWorker

FIXTURE = Path(__file__).parent/"fixtures"/"owon_hds2202s_v3_screen.json"


class CommandTests(unittest.TestCase):
    def test_si_units_and_case(self):
        self.assertEqual(quantity("2.5mV", "V"), .0025)
        self.assertEqual(quantity("25MHz", "Hz"), 25e6)
        self.assertAlmostEqual(quantity("2µs", "s"), 2e-6)
        self.assertEqual(quantity("-2e-3 V", "V"), -.002)
        for s in ("nan", "inf", "1bananas", "1V;:CHANnel ON"):
            with self.subTest(s=s), self.assertRaises(ValueError):
                quantity(s)

    def test_wire_units(self):
        self.assertEqual(BY_KEY["gen_freq"].write("1kHz"), ":FUNCtion:FREQuency 1000")
        self.assertEqual(BY_KEY["gen_amp"].write("200mV"), ":FUNCtion:AMPLitude 0.2")
        self.assertEqual(BY_KEY["gen_period"].write("500us"), ":FUNCtion:PERiod 0.0005")
        self.assertEqual(BY_KEY["ch1_scale"].write("500mV"), ":CH1:SCALe 0.5V")

    def test_every_control_has_valid_default_and_query(self):
        for spec in SETTINGS:
            with self.subTest(key=spec.key):
                command = spec.write(spec.default)
                self.assertEqual(validate_command(command), command)
                self.assertEqual(validate_command(spec.command+"?"), spec.command+"?")

    def test_reject_bad_commands_and_ranges(self):
        for s in ("*RST", ":CHANnel ON;*RST", ":CHANnel ON\n*RST", ":FUNCtion:FREQuency 26MHz",
                  ":CH1:OFFSet .5", ":FUNCtion:AMPLitude nan", ":DMM:CONFigure MAGIC", ":FUNCtion:LOAD 5"):
            with self.subTest(s=s), self.assertRaises(ValueError):
                validate_command(s)
        self.assertEqual(validate_command(":DMM:CONFigure:VOLTage AC"), ":DMM:CONFigure:VOLTage AC")


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.fs, self.n, self.f = 100000., 10000, 1000.
        self.t = np.arange(self.n)/self.fs
        self.y = 2*np.sin(2*np.pi*self.f*self.t)

    def test_measurements_and_dc(self):
        s = statistics(self.t, self.y+3)
        self.assertAlmostEqual(s["Mean"], 3, places=10)
        self.assertAlmostEqual(s["RMS"], np.sqrt(11), places=10)
        self.assertAlmostEqual(s["Frequency"], self.f, places=5)
        self.assertTrue(math.isnan(statistics(self.t, np.ones(self.n))["Frequency"]))

    def test_fft_rms_dbv_dbm_and_parseval(self):
        for window in ("hann", "hamming", "blackmanharris", "flattop", "boxcar"):
            with self.subTest(window=window):
                result = spectrum(self.t, self.y, window)
                k = int(np.argmax(result.rms))
                self.assertAlmostEqual(result.frequency[k], self.f, places=8)
                self.assertAlmostEqual(result.rms[k], np.sqrt(2), places=8)
                self.assertAlmostEqual(spectrum_values(result, "dBV")[k], 20*np.log10(np.sqrt(2)), places=8)
                self.assertAlmostEqual(spectrum_values(result, "dBm", 50)[k], 10*np.log10(2/50/.001), places=8)
                self.assertAlmostEqual(np.sum(result.psd)*result.bin_hz, np.mean(self.y**2), places=8)
                self.assertAlmostEqual(result.nyquist_hz, self.fs/2, places=7)

    def test_dc_nyquist_and_odd_records(self):
        dc = spectrum(self.t, np.ones(self.n)*2, "boxcar", remove_dc=False)
        self.assertAlmostEqual(dc.rms[0], 2)
        ny = spectrum(self.t, 3*(-1.)**np.arange(self.n), "boxcar")
        self.assertAlmostEqual(ny.rms[-1], 3)
        t = np.arange(9999)/self.fs
        y = 2*np.cos(2*np.pi*200*t/(9999/self.fs))
        odd = spectrum(t, y, "boxcar")
        self.assertAlmostEqual(odd.rms[200], np.sqrt(2))

    def test_thd(self):
        y = self.y + .2*np.sin(2*np.pi*3*self.f*self.t)
        result = spectrum(self.t, y)
        self.assertAlmostEqual(result.thd_percent, 10, places=7)
        self.assertEqual(result.harmonic_count, 4)

    def test_math_and_units(self):
        engine = MathEngine()
        np.testing.assert_allclose(engine.evaluate("(CH1 - CH2)/.1", self.t, self.y, self.y/2), self.y*5)
        np.testing.assert_allclose(engine.evaluate("rms(CH1)", self.t, self.y, self.y), np.sqrt(2))
        deriv = engine.evaluate("diff(CH1)", self.t, self.y, self.y)
        np.testing.assert_allclose(deriv[1:-1], 4*np.pi*self.f*np.cos(2*np.pi*self.f*self.t[1:-1]), rtol=.001, atol=.01)
        integ = engine.evaluate("integ(CH1)", self.t, np.ones(self.n), self.y)
        np.testing.assert_allclose(integ, self.t, atol=1e-12)
        low = engine.evaluate("lowpass(CH1, 2000)", self.t, self.y, self.y)
        self.assertGreater(np.std(low[200:-200]), 1.3)

    def test_math_rejects_code_and_invalid_math(self):
        for expr in ("__import__('os')", "CH1.__class__", "[x for x in CH1]", "CH1[0]", "CH1 / 0", "2**9999999",
                     "lowpass(CH1, 1000000)", "sqrt(-1)", "smooth(CH1, 1000)", "1e999", "bandpass(CH1, 2000, 1000)"):
            with self.subTest(expr=expr), self.assertRaises((ValueError, TypeError)):
                MathEngine().evaluate(expr, self.t, self.y, self.y)


class DeviceTests(unittest.TestCase):
    def test_recorded_trigger_labels(self):
        doc = json.loads(FIXTURE.read_text())
        trigger = trigger_readout(doc["header"])
        self.assertEqual(trigger["source"], "CH1")
        self.assertEqual(trigger["edge"], "↑ Rising")
        self.assertEqual(trigger["mode"], "Auto")
        self.assertEqual(trigger["coupling"], "DC")
        self.assertAlmostEqual(trigger["level_v"], 3.72)

    def test_trigger_missing_fields_zero_negative_and_sweep_location(self):
        for level, expected in (("0V", 0.), ("-250mV", -.25), ("not available", None)):
            trigger = trigger_readout(dict(trig=dict(sweep="NORMal", items=dict(channel="ch2", level=level, edge="FALL"))))
            self.assertEqual(trigger["level_v"], expected)
            self.assertEqual(trigger["mode"], "Normal")
            self.assertEqual(trigger["edge"], "↓ Falling")
        self.assertIsNone(trigger_readout({})["level_v"])
        self.assertEqual(trigger_readout({})["source"], "—")

    def fixture(self):
        d = json.loads(FIXTURE.read_text())
        payloads = {f"CH{i+1}":bytes.fromhex(p) for i,p in enumerate(d["payload_hex"])}
        scales = {f"CH{i+1}":p for i,p in enumerate(d["channel_scale_queries"])}
        return d, payloads, scales

    def test_recorded_hds2202s_scaling(self):
        d, payloads, scales = self.fixture()
        frame = decode_frame(d["header"], payloads, scales)
        # Recorded scope-average queries are 5.14 and 5.05 V; screen envelope
        # midpoint means are different. This guards sign, offset and factor-of-10.
        np.testing.assert_allclose(frame.channels.mean(axis=1), [5.1106666667, 5.0401333333], atol=1e-9)
        np.testing.assert_allclose(frame.channels.mean(axis=1), [5.14, 5.05], atol=.04)
        self.assertEqual(frame.t.shape, (300,))
        self.assertAlmostEqual(frame.metadata["export_rate_hz"], 50000)
        self.assertEqual(frame.metadata["internal_rate_text"], "500KSa/s")
        self.assertTrue(frame.metadata["screen_envelope"])
        self.assertIn("CH1", frame.envelopes)
        self.assertEqual(frame.metadata["raw_payload_hex"]["CH1"], d["payload_hex"][0])

    def test_scale_query_overrides_header_and_no_second_probe_factor(self):
        d, payloads, scales = self.fixture()
        d["header"]["CHANNEL"][0]["SCALE"] = "100V"
        frame = decode_frame(d["header"], payloads, scales)
        self.assertAlmostEqual(frame.channels[0].mean(), 5.1106666667, places=8)

    def test_disabled_channels_short_records_and_roll(self):
        d, payloads, scales = self.fixture()
        d["header"]["CHANNEL"][1]["DISPLAY"] = "OFF"
        frame = decode_frame(d["header"], {"CH1":payloads["CH1"]}, scales)
        self.assertTrue(np.all(np.isnan(frame.channels[1])))
        with self.assertRaises(ValueError):
            decode_frame(d["header"], {"CH1":b"bad"}, scales)
        d["header"]["SAMPLE"]["SLOWMOVE"] = 2
        with self.assertRaises(ValueError):
            decode_frame(d["header"], payloads, scales)

    def test_capture_frozen_verification_and_changed_settings(self):
        d, payloads, scales = self.fixture()
        d["header"]["RUNSTATUS"] = "STOP"
        class Fake:
            def block(self, cmd):
                return json.dumps(d["header"]).encode() if "HEAD" in cmd else payloads[re_channel(cmd)]
            def text(self, cmd):
                return scales[cmd.split(":")[1]]
        def re_channel(command):
            return command.split(":")[-1].rstrip("?")
        frame = capture(Fake(), "OWON,HDS2202S,test,V3")
        self.assertTrue(frame.metadata["frozen_repeat_verified"])
        class Changed(Fake):
            reads = 0
            def block(self, cmd):
                if "HEAD" in cmd:
                    self.reads += 1
                    h = json.loads(json.dumps(d["header"]))
                    if self.reads > 1:
                        h["TIMEBASE"]["SCALE"] = "1ms"
                    return json.dumps(h).encode()
                return super().block(cmd)
        with self.assertRaisesRegex(ValueError, "Settings changed"):
            capture(Changed(), "test")

    def test_demo_write_read_freeze(self):
        demo = DemoTransport()
        demo.write(":FUNCtion:FREQuency 2000")
        self.assertEqual(demo.text(":FUNCtion:FREQuency?"), "2000")
        self.assertAlmostEqual(quantity(demo.text(":FUNCtion:PERiod?"), "s"), .0005)
        frame = demo.frame()
        demo.write(":RUNning STOP")
        np.testing.assert_array_equal(frame.channels, demo.frame().channels)
        self.assertEqual(demo.frame().header["RUNSTATUS"], "STOP")

    def test_export_roundtrip_raw_provenance_and_csv(self):
        d, payloads, scales = self.fixture()
        frame = decode_frame(d["header"], payloads, scales)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"frame.npz"
            save_capture(path, frame)
            restored = load_capture(path)
            np.testing.assert_array_equal(frame.channels, restored.channels)
            self.assertEqual(frame.metadata["raw_payload_hex"], restored.metadata["raw_payload_hex"])
            self.assertTrue(restored.metadata["replay"])
            save_capture(Path(td)/"frame.csv", frame)
            self.assertTrue((Path(td)/"frame.csv.json").exists())
            self.assertEqual(len((Path(td)/"frame.csv").read_text().splitlines()), 301)


class TransportTests(unittest.TestCase):
    def test_fragmented_little_endian_blocks(self):
        usb = USBTransport()
        usb.send = lambda cmd: None
        payload = bytes(range(100))
        parts = iter([b"\x64", b"\0\0", b"\0"+payload[:13], payload[13:]])
        usb._read = lambda: next(parts)
        self.assertEqual(usb.block(":DATA:WAVE:SCREEN:CH1?"), payload)

    def test_bad_lengths_and_extra_bytes(self):
        for response in (b"\xff\xff\xff\xff", b"\x01\0\0\0ab"):
            usb = USBTransport()
            usb.send = lambda cmd: None
            usb._read = lambda: response
            with self.assertRaises(IOError):
                usb.block(":DATA:WAVE:SCREEN:CH1?")

    def test_timeout_drops_connection_and_queued_writes(self):
        worker = InstrumentWorker()
        class Fake:
            closed = False
            def close(self):
                self.closed = True
        fake = Fake()
        worker.transport = fake
        worker.submit("command", "output", ":CHANnel ON")
        worker._failure(TimeoutError("unsupported command"))
        self.assertTrue(fake.closed)
        self.assertIsNone(worker.transport)
        self.assertTrue(worker.jobs.empty())

    def test_generator_limits_and_probe_scale(self):
        worker = InstrumentWorker()
        worker.transport = DemoTransport()
        worker.transport.write(":FUNCtion SQUare")
        with self.assertRaises(ValueError):
            worker._validate_generator(":FUNCtion:FREQuency 10000000")
        worker._validate_generator(":FUNCtion:FREQuency 5000000")
        with self.assertRaises(ValueError):
            worker._validate_channel(":CH1:SCALe 0.01V")
        worker._validate_channel(":CH1:SCALe 0.1V")


if __name__ == "__main__":
    unittest.main()
