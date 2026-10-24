import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtWidgets import QApplication
from hdsstudio.waveform import WaveformLibrary, template_samples, TEMPLATES
from hdsstudio.waveform_shapes import SCHEMAS, template_parameters
from hdsstudio.waveform_editor import WaveformEditor


class ShapeDesignTests(unittest.TestCase):
    def test_every_template_has_a_schema_and_bounded_defaults(self):
        self.assertEqual(set(TEMPLATES), set(SCHEMAS))
        for name in TEMPLATES:
            with self.subTest(name=name):
                y = template_samples(name, 1024)
                self.assertEqual(y.shape, (1024,))
                self.assertTrue(np.isfinite(y).all())
                self.assertLessEqual(abs(y).max(), 1)

    def test_staircase_level_counts_direction_and_dwell(self):
        for levels in (2, 7, 32, 64):
            up = template_samples('Stair up', parameters=dict(levels=levels))
            down = template_samples('Stair down', parameters=dict(levels=levels))
            np.testing.assert_allclose(np.unique(up), np.linspace(-1, 1, levels))
            np.testing.assert_array_equal(down, -up)
            self.assertTrue((np.diff(up) >= 0).all())
        biased = template_samples('Stair up', parameters=dict(levels=7, dwell_power=2))
        equal = template_samples('Stair up', parameters=dict(levels=7))
        self.assertGreater(np.count_nonzero(biased == -1), np.count_nonzero(equal == -1))

    def test_staircase_turnaround_controls_peak_position(self):
        y = template_samples('Stair up / down', 1001, dict(levels=7, turn_pct=30))
        self.assertEqual(len(np.unique(y)), 7)
        self.assertEqual(y[0], -1)
        self.assertEqual(y[-1], -1)
        self.assertEqual(y[300], 1)
        self.assertTrue((np.diff(y[:301]) >= 0).all())
        self.assertTrue((np.diff(y[300:]) <= 0).all())

    def test_sinc_zero_spacing_width_and_taper(self):
        y = template_samples('Sinc', 1001, dict(lobes=5))
        self.assertEqual(y[500], 1)
        np.testing.assert_allclose(y, y[::-1], atol=1e-14)
        np.testing.assert_allclose(y[600:1001:100], 0, atol=1e-14)
        narrow = template_samples('Sinc', 1001, dict(lobes=5, width_pct=50))
        self.assertTrue((narrow[:250] == 0).all())
        self.assertAlmostEqual(narrow[550], 0)
        hann = template_samples('Sinc', 1001, dict(lobes=5, window='Hann'))
        self.assertLess(abs(hann[650]), abs(y[650]))
        self.assertEqual(hann[500], 1)

    def test_bessel_parameters_and_positive_y_domain(self):
        for name in ('Bessel J', 'Bessel Y'):
            base = template_samples(name)
            for parameters in (dict(order=2), dict(span=12), dict(decay=2), dict(start=1)):
                with self.subTest(name=name, parameters=parameters):
                    y = template_samples(name, parameters=parameters)
                    self.assertTrue(np.isfinite(y).all())
                    self.assertFalse(np.allclose(y, base))
        with self.assertRaises(ValueError):
            template_samples('Bessel Y', parameters=dict(start=0))

    def test_burst_envelope_and_gaussian_width(self):
        rising = template_samples('Rising sine burst', 1001, dict(cycles=5, power=2, floor_pct=20))
        x = np.linspace(0, 1, 1001)
        np.testing.assert_allclose(rising, (.2+.8*x*x)*np.sin(10*np.pi*x), atol=1e-14)
        narrow = template_samples('Gaussian pulse', 1001, dict(sigma_pct=5, baseline='Unipolar'))
        broad = template_samples('Gaussian pulse', 1001, dict(sigma_pct=20, baseline='Unipolar'))
        self.assertGreater(np.count_nonzero(broad > .5), np.count_nonzero(narrow > .5))
        self.assertGreaterEqual(narrow.min(), 0)

    def test_chirp_sweep_and_pulse_duty_are_configurable(self):
        self.assertFalse(np.allclose(template_samples('Chirp'), template_samples('Chirp', parameters=dict(sweep='Logarithmic'))))
        for duty in (20, 70):
            y = template_samples('Pulse', 8192, dict(duty_pct=duty))
            self.assertAlmostEqual(np.count_nonzero(y > 0)/len(y), duty/100, delta=.001)

    def test_invalid_parameters_and_under_sampled_records_are_rejected(self):
        cases = [('Stair up', 1024, dict(levels=3.5)), ('Sinc', 1024, dict(lobes=float('nan'))),
                 ('Sine', 1024, dict(unknown=1)), ('Sine', 256, dict(cycles=64)),
                 ('Sinc', 256, dict(lobes=16, width_pct=5)),
                 ('Stair up', 128, dict(levels=64, dwell_power=4)),
                 ('Chirp', 1024, dict(sweep='invalid'))]
        for name, count, parameters in cases:
            with self.subTest(name=name, parameters=parameters), self.assertRaises(ValueError):
                template_samples(name, count, parameters)
        for count in (15, 8193, 1024.5):
            with self.assertRaises(ValueError):
                template_samples('Sine', count)

    def test_previous_template_names_remain_supported(self):
        for old, new in [('Pulse 25%', 'Pulse'), ('Bessel J0', 'Bessel J'), ('Bessel Y0', 'Bessel Y')]:
            np.testing.assert_array_equal(template_samples(old), template_samples(new))

    def test_library_preserves_recipes_and_old_sample_only_entries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'library.json'
            library = WaveformLibrary(path)
            recipe = dict(template='Stair up', parameters=dict(levels=6, dwell_power=2))
            samples = template_samples('Stair up', parameters=recipe['parameters'])
            library.save('steps', samples, 1, 0, recipe)
            library.save('legacy', samples, 1, 0)
            restored = WaveformLibrary(path)
            self.assertEqual(restored.entries['steps']['recipe'], recipe)
            self.assertNotIn('recipe', restored.entries['legacy'])
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                library.save('bad', samples, 1, 0, dict(template='Sinc', parameters=dict(lobes=0)))
            self.assertEqual(original, path.read_bytes())
            data = json.loads(original)
            data['steps']['recipe']['parameters']['levels'] = 0
            path.write_text(json.dumps(data))
            self.assertIn('Cannot read', WaveformLibrary(path).warning)


class ShapeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = WaveformEditor()

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def test_shape_specific_controls_regenerate_and_have_appropriate_steps(self):
        e = self.editor
        e.templates.setCurrentText('Stair up / down')
        self.assertEqual(set(e.parameter_widgets), {'levels', 'dwell_power', 'turn_pct'})
        e.parameter_widgets['levels'].setValue(6)
        self.assertEqual(len(np.unique(e.samples)), 6)
        self.assertEqual(e.parameter_widgets['levels'].singleStep(), 1)
        self.assertEqual(e.parameter_widgets['dwell_power'].singleStep(), .25)
        e.templates.setCurrentText('Sinc')
        self.assertEqual(set(e.parameter_widgets), {'lobes', 'width_pct', 'center_pct', 'window'})
        self.assertEqual(e.parameter_widgets['width_pct'].singleStep(), 5)
        before = e.samples.copy()
        e.parameter_widgets['window'].setCurrentText('Hann')
        self.assertFalse(np.allclose(e.samples, before))

    def test_template_switch_restores_its_own_parameters(self):
        e = self.editor
        e.load_template('Stair up')
        e.parameter_widgets['levels'].setValue(6)
        before = e.samples.copy()
        e.templates.setCurrentText('Sinc')
        e.parameter_widgets['lobes'].setValue(3)
        e.templates.setCurrentText('Stair up')
        self.assertEqual(e.parameter_widgets['levels'].value(), 6)
        np.testing.assert_array_equal(e.samples, before)
        e.templates.setCurrentText('Sinc')
        self.assertEqual(e.parameter_widgets['lobes'].value(), 3)

    def test_drawing_detaches_and_undo_restores_editable_recipe(self):
        e = self.editor
        e.load_template('Stair down', dict(levels=5))
        before = e.samples.copy()
        e.start_stroke()
        e.draw_point(.4, .123)
        self.assertIsNone(e.recipe)
        drawn = e.samples.copy()
        e.parameter_widgets['levels'].setValue(8)
        np.testing.assert_array_equal(e.samples, drawn)
        self.assertIn('Generate shape', e.source_note.text())
        e.undo_stroke()
        np.testing.assert_array_equal(e.samples, before)
        self.assertEqual(e.recipe['parameters']['levels'], 5)
        self.assertEqual(e.parameter_widgets['levels'].value(), 5)

    def test_imported_data_survive_template_selection_until_generate(self):
        e = self.editor
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'custom.csv'
            path.write_text('normalized\n0\n1\n0\n-1\n0\n')
            e.load_csv(path)
        imported = e.samples.copy()
        e.templates.setCurrentText('Stair up')
        e.parameter_widgets['levels'].setValue(5)
        np.testing.assert_array_equal(e.samples, imported)
        e.generate_button.click()
        self.assertEqual(e.recipe['parameters']['levels'], 5)
        self.assertEqual(len(np.unique(e.samples)), 5)
        e.undo_stroke()
        np.testing.assert_array_equal(e.samples, imported)
        self.assertIsNone(e.recipe)

    def test_parameter_errors_keep_samples_and_block_export_until_resolved(self):
        e = self.editor
        e.count.setCurrentText('256')
        before = e.samples.copy()
        e.parameter_widgets['cycles'].setValue(64)
        np.testing.assert_array_equal(e.samples, before)
        self.assertFalse(e.keep_button.isEnabled())
        self.assertFalse(e.export_button.isEnabled())
        with self.assertRaises(ValueError):
            e.save_cycle('invalid')
        with patch('hdsstudio.waveform_editor.QFileDialog.getSaveFileName') as dialog:
            e.export_csv()
            self.assertFalse(dialog.called)
        e.count.setCurrentText('1024')
        self.assertIsNone(e.parameter_error)
        self.assertEqual(e.recipe['parameters']['cycles'], 64)
        self.assertTrue(e.export_button.isEnabled())

    def test_parametric_resampling_retains_exact_step_levels_and_rejects_invalid_count(self):
        e = self.editor
        e.load_template('Stair up', dict(levels=7))
        e.count.setCurrentText('256')
        self.assertEqual(len(np.unique(e.samples)), 7)
        e.load_template('Sine', dict(cycles=64))  # too few samples; previous design remains
        self.assertEqual(e.recipe['template'], 'Stair up')
        e.count.setCurrentText('1024')
        e.load_template('Sine', dict(cycles=64))
        before = e.samples.copy()
        e.count.setCurrentText('256')
        self.assertEqual(e.count.currentText(), '1024')
        np.testing.assert_array_equal(e.samples, before)

    def test_saved_recipe_restores_controls_but_drawn_cycle_stays_sample_only(self):
        e = self.editor
        e.load_template('Sinc', dict(lobes=3, width_pct=70, window='Hann'))
        before = e.samples.copy()
        e.save_cycle('sinc')
        e.start_stroke()
        e.draw_point(.3, .8)
        drawn = e.samples.copy()
        e.save_cycle('drawn')
        e.load_cycle('sinc')
        np.testing.assert_array_equal(e.samples, before)
        self.assertEqual(e.parameter_widgets['lobes'].value(), 3)
        self.assertEqual(e.parameter_widgets['window'].currentText(), 'Hann')
        e.load_cycle('drawn')
        self.assertIsNone(e.recipe)
        np.testing.assert_array_equal(e.samples, drawn)


if __name__ == '__main__':
    unittest.main()
