from pathlib import Path
import tempfile
import unittest
import numpy as np
from scipy.signal import hilbert
from hdsstudio.commands import BY_KEY, quantity
from hdsstudio.device import DemoTransport
from hdsstudio.generator import read_generator_state, voltage_pair, levels_pair, voltage_plan
from hdsstudio.modulation import design_modulation, export_modulation
from hdsstudio.worker import InstrumentWorker


class TracedDemo(DemoTransport):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.envelopes = []

    def write(self, command):
        self.writes.append(command)
        super().write(command)
        self.envelopes.append(voltage_pair(quantity(self.settings[":function:amplitude"], "V"),
                                           quantity(self.settings[":function:offset"], "V")))


class GeneratorDesignTests(unittest.TestCase):
    def worker(self):
        worker = InstrumentWorker()
        worker.transport = TracedDemo()
        worker._load_generator()
        return worker

    def test_amplitude_accepts_vpp_and_si_prefixes(self):
        for text in ("500mVpp", "500 mVpp", "0.5 Vp-p", "500mV", "0.5"):
            self.assertEqual(BY_KEY["gen_amp"].write(text), ":FUNCtion:AMPLitude 0.5")

    def test_voltage_conversions_and_invalid_levels(self):
        self.assertEqual(voltage_pair(2, .25), (1.25, -.75))
        self.assertEqual(levels_pair(1.25, -.75), (2, .25))
        for pair in ((-.5, .5), (2.6, -1), (.01, 0)):
            with self.assertRaises(ValueError):
                levels_pair(*pair)

    def test_paired_apply_orders_writes_and_preserves_live_output(self):
        worker = self.worker()
        worker.transport.settings.update({":function:amplitude": "0.02", ":function:offset": "2.49", ":channel": "ON"})
        worker._load_generator()
        worker._job("generator_apply", ("levels", 0, dict(amplitude=5, offset=0)))
        self.assertEqual(worker.transport.writes, [":FUNCtion:OFFSet 0", ":FUNCtion:AMPLitude 5"])
        self.assertTrue(worker.generator_baseline.output_on)
        self.assertEqual(worker.transport.envelopes[-1], (2.5, -2.5))
        worker.transport.writes.clear()
        worker._job("generator_apply", ("levels", 1, dict(amplitude=.02, offset=-2.49)))
        self.assertEqual(worker.transport.writes, [":FUNCtion:AMPLitude 0.02", ":FUNCtion:OFFSet -2.49"])
        self.assertTrue(worker.generator_baseline.output_on)

    def test_invalid_pair_and_external_change_send_no_writes(self):
        worker = self.worker()
        with self.assertRaises(ValueError):
            worker._job("generator_apply", ("levels", 0, dict(amplitude=5, offset=1)))
        worker.transport.settings[":function:offset"] = ".25"
        with self.assertRaisesRegex(ValueError, "changed"):
            worker._job("generator_apply", ("levels", 0, dict(amplitude=1, offset=0)))
        self.assertFalse(worker.transport.writes)

    def test_ignored_first_write_stops_voltage_sequence(self):
        worker = self.worker()
        worker.transport.settings.update({":function:amplitude": "0.02", ":function:offset": "2.49"})
        worker._load_generator()
        worker.transport.write = lambda command: worker.transport.writes.append(command)
        with self.assertRaisesRegex(ValueError, "remaining writes were stopped"):
            worker._job("generator_apply", ("levels", 0, dict(amplitude=5, offset=0)))
        self.assertEqual(worker.transport.writes, [":FUNCtion:OFFSet 0"])

    def test_frequency_and_pulse_width_duty_apply_readback(self):
        worker = self.worker()
        worker.transport.settings[":function"] = "PULSe"
        worker._load_generator()
        worker._job("generator_apply", ("frequency", 0, dict(frequency=2000)))
        worker._job("generator_apply", ("pulse", 0, dict(key="gen_width", value=125e-6)))
        self.assertAlmostEqual(quantity(worker.transport.text(":FUNCtion:PULSe:DTYCycle?")), 25)
        worker._job("generator_apply", ("pulse", 1, dict(key="gen_duty", value=40)))
        self.assertAlmostEqual(quantity(worker.transport.text(":FUNCtion:WIDTh?"), "s"), 200e-6)
        before = list(worker.transport.writes)
        with self.assertRaisesRegex(ValueError, "period"):
            worker._job("generator_apply", ("pulse", 2, dict(key="gen_width", value=.001)))
        self.assertEqual(worker.transport.writes, before)

    def test_frequency_limits_follow_actual_waveform(self):
        worker = self.worker()
        worker.transport.settings[":function"] = "RAMP"
        worker._load_generator()
        with self.assertRaisesRegex(ValueError, "frequency"):
            worker._job("generator_apply", ("frequency", 0, dict(frequency=2e6)))
        self.assertFalse(worker.transport.writes)

    def test_shape_specific_parameters_cannot_be_sent_to_other_waveforms(self):
        worker = self.worker()
        for command in (":FUNCtion:SYMMetry 50", ":FUNCtion:RISing 1e-8", ":FUNCtion:FALing 1e-8",
                        ":FUNCtion:PULSe:DTYCycle 25", ":FUNCtion:WIDTh 0.0001"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "Apply"):
                worker._job("command", ("console", command))
        self.assertFalse(worker.transport.writes)

    def test_pulse_edges_and_ramp_symmetry_apply_in_the_correct_mode(self):
        worker = self.worker()
        worker.transport.settings[":function"] = "PULSe"
        worker._load_generator()
        worker._job("command", ("gen_rise", ":FUNCtion:RISing 20ns"))
        self.assertEqual(worker.transport.writes[-1], ":FUNCtion:RISing 2e-08")
        before = list(worker.transport.writes)
        with self.assertRaisesRegex(ValueError, "period"):
            worker._job("command", ("gen_fall", ":FUNCtion:FALing 2ms"))
        self.assertEqual(worker.transport.writes, before)
        worker.transport.settings[":function"] = "RAMP"
        worker._load_generator()
        worker._job("command", ("gen_sym", ":FUNCtion:SYMMetry 33%"))
        self.assertEqual(worker.transport.writes[-1], ":FUNCtion:SYMMetry 33")
        self.assertEqual(worker.transport.text(":CHANnel?"), "OFF")


class ModulationTests(unittest.TestCase):
    def test_am_envelope_and_carrier_units(self):
        t, y, upper, lower = design_modulation("SINE", 1000, 2, .1, depth=.5)
        m = np.sin(2*np.pi*100*t)
        np.testing.assert_allclose(y, .1+(1+.5*m)*np.sin(2*np.pi*1000*t), atol=1e-12)
        np.testing.assert_allclose(upper, .1+1+.5*m)
        np.testing.assert_allclose(lower, .1-1-.5*m)

    def test_fm_phase_derivative_follows_instantaneous_frequency(self):
        t, y, _, _ = design_modulation("SINE", 1000, 2, 0, mode="FM", deviation_hz=200)
        phase = np.unwrap(np.angle(hilbert(y)))
        frequency = np.gradient(phase, t)/(2*np.pi)
        target = 1000+200*np.sin(2*np.pi*100*t)
        np.testing.assert_allclose(frequency[500:-500], target[500:-500], atol=.2)

    def test_modulation_bounds_and_insufficient_sample_rate(self):
        for kwargs in (dict(amplitude=5), dict(deviation_hz=1000, mode="FM"), dict(depth=1.1),
                       dict(mod_hz=.01), dict(carrier_hz=float("nan"))):
            parameters = dict(shape="SINE", carrier_hz=1000, amplitude=1, offset=0)
            parameters.update(kwargs)
            with self.assertRaises(ValueError):
                design_modulation(**parameters)

    def test_modulation_csv_retains_seconds_and_volts(self):
        t, y, _, _ = design_modulation("SINE", 1000, 1, 0, mode="FM")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"modulation.csv"
            export_modulation(path, t, y)
            self.assertEqual(path.read_text().splitlines()[0], "time_s,voltage_V")
            data = np.loadtxt(path, delimiter=",", skiprows=1)
            np.testing.assert_array_equal(data[:, 0], t)
            np.testing.assert_array_equal(data[:, 1], y)


if __name__ == "__main__":
    unittest.main()
