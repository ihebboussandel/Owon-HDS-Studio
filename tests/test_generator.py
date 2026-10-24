from pathlib import Path
import tempfile
import unittest
import numpy as np

from hdsstudio.device import DemoTransport, generator_wave
from hdsstudio.commands import WAVES
from hdsstudio.generator import load_factor, read_generator_state, same_state
from hdsstudio.waveform import (TEMPLATES, WaveformLibrary, template_samples, read_waveform_csv,
                               save_waveform_csv, voltage_values)
from hdsstudio.worker import InstrumentWorker


class RecordingDemo(DemoTransport):
    def __init__(self):
        super().__init__()
        self.writes = []

    def write(self, command):
        self.writes.append(command)
        super().write(command)


class GeneratorTests(unittest.TestCase):
    def worker(self):
        worker = InstrumentWorker()
        worker.transport = RecordingDemo()
        return worker

    def test_snapshot_reads_existing_parameters_without_writes(self):
        demo = RecordingDemo()
        demo.settings[":function:frequency"] = "1234.5"
        demo.settings[":function:amplitude"] = "2.5"
        demo.settings[":channel"] = "ON"
        state = read_generator_state(demo)
        self.assertEqual(state.values["gen_freq"], "1234.5Hz")
        self.assertEqual(state.values["gen_amp"], "2.5V")
        self.assertTrue(state.output_on)
        self.assertFalse(demo.writes)

    def test_unloaded_generator_cannot_enable_output(self):
        worker = self.worker()
        with self.assertRaisesRegex(ValueError, "Load"):
            worker._job("command", ("output", ":CHANnel ON"))
        self.assertFalse(worker.transport.writes)

    def test_external_parameter_change_is_loaded_and_rejected_before_write(self):
        worker = self.worker()
        worker._load_generator()
        worker.transport.settings[":function:amplitude"] = "3"
        with self.assertRaisesRegex(ValueError, "changed"):
            worker._job("command", ("gen_freq", ":FUNCtion:FREQuency 2000"))
        self.assertFalse(worker.transport.writes)
        self.assertEqual(worker.generator_baseline.values["gen_amp"], "3V")

    def test_live_output_allows_explicit_parameter_changes_without_toggling_output(self):
        worker = self.worker()
        worker.transport.settings[":channel"] = "ON"
        worker._load_generator()
        worker._job("command", ("gen_freq", ":FUNCtion:FREQuency 2000"))
        self.assertEqual(worker.transport.writes, [":FUNCtion:FREQuency 2000"])
        self.assertEqual(worker.transport.settings[":channel"], "ON")

    def test_explicit_disable_is_available_before_loading(self):
        worker = self.worker()
        worker.transport.settings[":channel"] = "ON"
        worker._job("command", ("output", ":CHANnel OFF"))
        self.assertEqual(worker.transport.writes, [":CHANnel OFF"])
        self.assertFalse(worker.generator_baseline.output_on)

    def test_apply_with_output_off_does_not_enable_it(self):
        worker = self.worker()
        worker._load_generator()
        worker._job("command", ("gen_freq", ":FUNCtion:FREQuency 2000"))
        self.assertEqual(worker.transport.writes, [":FUNCtion:FREQuency 2000"])
        self.assertEqual(worker.transport.settings[":channel"], "OFF")

    def test_unsafe_combined_offset_amplitude_is_rejected(self):
        worker = self.worker()
        worker._load_generator()
        with self.assertRaisesRegex(ValueError, "envelope"):
            worker._job("command", ("gen_offset", ":FUNCtion:OFFSet 2.5"))
        self.assertFalse(worker.transport.writes)

    def test_implausible_firmware_units_cannot_be_used_for_enable(self):
        worker = self.worker()
        worker.transport.settings[":function:amplitude"] = "1000"
        with self.assertRaises(ValueError):
            worker._load_generator()
        self.assertIsNone(worker.generator_baseline)
        self.assertFalse(worker.transport.writes)

    def test_load_is_a_local_divider_calculation(self):
        self.assertEqual(load_factor(None), 1)
        self.assertEqual(load_factor(50), .5)
        self.assertAlmostEqual(load_factor(1000), 1000/1050)
        for value in (0, -50, 1e9, float("nan")):
            with self.assertRaises(ValueError):
                load_factor(value)

    def test_load_reference_and_vpp_readback_do_not_lock_generator(self):
        demo = RecordingDemo()
        demo.settings[":function:amplitude"] = "500mVpp"
        for raw, expected in (("HIGHz", "High Z"), ("High Z", "High Z"), ("50ohm", "50 Ω"),
                              ("10000", "10000 Ω"), ("1", "ON")):
            demo.settings[":function:load"] = raw
            state = read_generator_state(demo)
            self.assertEqual(state.values["gen_load"], expected)
            self.assertEqual(state.values["gen_amp"], "0.5V")
        self.assertFalse(demo.writes)

    def test_device_shape_previews_repeat_each_cycle_and_bessel_shapes_differ(self):
        phase = np.linspace(.1, 6.1, 200)
        for shape in WAVES:
            y = generator_wave(shape, phase)
            self.assertTrue(np.all(np.isfinite(y)))
            np.testing.assert_allclose(y, generator_wave(shape, phase+2*np.pi), atol=1e-12)
        self.assertFalse(np.allclose(generator_wave("Besselj", phase), generator_wave("Bessely", phase)))


class WaveformFileTests(unittest.TestCase):
    def file(self, directory, text):
        path = Path(directory)/"wave.csv"
        path.write_text(text, encoding="utf8")
        return path

    def test_one_column_csv_is_interpolated(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.file(td, "normalized\n0\n1\n0\n-1\n0\n")
            data = read_waveform_csv(path, 256)
            self.assertEqual(data.shape, (256,))
            self.assertEqual(data[0], 0)
            self.assertEqual(data[-1], 0)
            self.assertLessEqual(np.max(np.abs(data)), 1)

    def test_time_and_voltage_csv(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.file(td, "time_s,voltage_v\n0,-1\n0.1,0\n0.2,1\n")
            data = read_waveform_csv(path, 256, amplitude=2)
            self.assertAlmostEqual(data[0], -1)
            self.assertAlmostEqual(data[-1], 1)

    def test_export_import_preserves_normalized_samples_and_volts(self):
        y = np.sin(np.linspace(0, 2*np.pi, 1024))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"wave.csv"
            save_waveform_csv(path, y, amplitude=2, offset=.25)
            restored = read_waveform_csv(path, 1024)
            np.testing.assert_allclose(y, restored, atol=1e-12)
            self.assertEqual(path.read_text().splitlines()[0], "phase,normalized,voltage_V")
            volts = voltage_values(restored, 2, .25)
            np.testing.assert_allclose(volts, y+.25, atol=1e-12)

    def test_bad_csv_is_rejected_without_silent_normalization(self):
        for text in ("normalized\n0\n2\n", "phase,normalized\n0,0\n0,1\n",
                     "phase,normalized\n1,0\n0,1\n", "0,oops\n1,0\n2,1\n",
                     "normalized\nNaN\n0\n", "normalized\n0\ninf\n", "normalized\n0\n"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(ValueError):
                    read_waveform_csv(self.file(td, text))

    def test_export_envelope_bounds(self):
        with self.assertRaises(ValueError):
            voltage_values(np.zeros(1024), 5, 1)
        with self.assertRaises(ValueError):
            voltage_values(np.full(1024, 2), 1, 0)

    def test_design_templates_are_finite_bounded_and_distinct(self):
        waves = [template_samples(name, 8192) for name in TEMPLATES]
        for wave in waves:
            self.assertEqual(wave.shape, (8192,))
            self.assertTrue(np.all(np.isfinite(wave)))
            self.assertLessEqual(np.max(np.abs(wave)), 1)
        self.assertFalse(np.allclose(template_samples("Bessel J0"), template_samples("Bessel Y0")))
        self.assertFalse(np.allclose(template_samples("Rising sine burst"), template_samples("Falling sine burst")))

    def test_named_library_survives_restart_and_rejects_bad_samples(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"library.json"
            library = WaveformLibrary(path)
            samples = template_samples("Chirp", 1024)
            library.save("My sweep", samples, 2, .25)
            restored = WaveformLibrary(path)
            self.assertFalse(restored.warning)
            np.testing.assert_array_equal(restored.entries["My sweep"]["samples"], samples)
            self.assertEqual(restored.entries["My sweep"]["amplitude"], 2)
            with self.assertRaises(ValueError):
                library.save("bad", np.full(1024, float("nan")), 1, 0)
            self.assertNotIn("bad", WaveformLibrary(path).entries)
            path.write_text('{"bad": {}}')
            broken = WaveformLibrary(path)
            self.assertIn("Cannot read", broken.warning)
            with self.assertRaises(ValueError):
                broken.save("replacement", samples, 1, 0)


if __name__ == "__main__":
    unittest.main()
