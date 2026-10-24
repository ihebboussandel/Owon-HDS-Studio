import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from hdsstudio.device import DemoTransport
from hdsstudio.ui import StudioWindow
from hdsstudio.themes import THEMES
from hdsstudio.storage import load_capture, save_capture
from hdsstudio.generator import read_generator_state


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = StudioWindow(start_demo=False, persist=False)
        self.window.show()
        self.errors = []
        self.window.worker.error.connect(self.errors.append)

    def pump(self, seconds=.1):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            self.app.processEvents()
            time.sleep(.005)

    def wait_until(self, predicate, timeout=3):
        end = time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            self.pump(.02)
        self.assertTrue(predicate())

    def tearDown(self):
        self.window.close()
        self.wait_until(lambda: not self.window.worker.isRunning(), timeout=4)
        self.pump(.05)

    def test_live_demo_all_workspaces_and_themes(self):
        self.window.worker.submit("connect", "Demo")
        self.wait_until(lambda: self.window.frame_count>=2)
        self.assertIn("DEMO", self.window.source_badge.text())
        self.assertIsNotNone(self.window.math_trace)
        for name in THEMES:
            self.window.theme.setCurrentText(name)
            for index in range(6):
                self.window.tabs.setCurrentIndex(index)
                self.pump(.04)
            self.assertEqual(self.window.generator_plot.getAxis("bottom").labelUnits, "s")
        self.window.tabs.setCurrentIndex(1)
        self.wait_until(lambda: len(self.window.dmm_history)>0)
        self.assertIn("V", self.window.meter_value.text())
        self.window.tabs.setCurrentIndex(2)
        self.window.send_command("output", ":CHANnel ON")
        self.wait_until(lambda: "ON" == self.window.output_badge.text().split(" · ")[-1])
        self.window.send_command("output", ":CHANnel OFF")
        self.wait_until(lambda: "OFF" == self.window.output_badge.text().split(" · ")[-1])
        self.assertFalse(self.errors)

    def test_math_error_reference_cursors_and_fft(self):
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.receive_frame(demo.frame())
        self.window.set_reference()
        self.assertIsNotNone(self.window.reference)
        self.window.cursor_toggle.setChecked(True)
        self.assertIn("Δt", self.window.cursor_readout.text())
        self.window.tabs.setCurrentIndex(4)
        self.window.spec_max.setChecked(True)
        self.window.spec_average.setChecked(True)
        self.window.render_spectrum()
        self.assertIsNotNone(self.window.spectrum_hold)
        self.assertTrue(len(self.window.waterfall_rows))
        self.window.math_expression.setText("CH1 / 0")
        self.window.render_math()
        self.assertIsNone(self.window.math_trace)
        self.assertIn("undefined", self.window.math_status.text())
        self.window.spec_source.setCurrentText("MATH")
        self.assertIn("unavailable", self.window.spec_metrics.text())

    def test_capture_recording_and_dmm_overload(self):
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        with tempfile.TemporaryDirectory() as td:
            self.window.record_dir = Path(td)
            self.window.receive_frame(demo.frame())
            self.assertTrue((Path(td)/"frame_0000001.npz").exists())
            self.window.stop_recording()
        self.window.receive_reading(dict(text="OVERLOAD", host_time=time.time(), source="DEMO"))
        self.assertEqual(self.window.meter_value.text(), "OVERLOAD")
        self.window.receive_reading(dict(text="250mV", host_time=time.time(), source="DEMO"))
        self.assertAlmostEqual(self.window.dmm_history[-1][1], .25)

    def test_workspace_preset_does_not_write_settings(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td)/"workspace.json")
            self.window.rows["gen_freq"].edit.setText("2kHz")
            self.window.math_expression.setText("CH1 * 2")
            with patch("hdsstudio.ui.QFileDialog.getSaveFileName", return_value=(path, "JSON (*.json)")):
                self.window.save_preset()
            self.window.rows["gen_freq"].edit.setText("5kHz")
            with patch("hdsstudio.ui.QFileDialog.getOpenFileName", return_value=(path, "JSON (*.json)")), patch.object(self.window.worker, "submit") as submit:
                self.window.load_preset()
                self.assertFalse(any(call.args and call.args[0] == "command" for call in submit.call_args_list))
            self.assertEqual(self.window.rows["gen_freq"].text(), "2kHz")
            self.assertTrue(self.window.rows["gen_freq"].dirty)
            self.assertEqual(self.window.math_expression.text(), "CH1 * 2")

    def test_replay_rejects_queued_live_frames_and_pause_buttons_sync(self):
        demo = DemoTransport()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"frame.npz"
            save_capture(path, demo.frame())
            replay = load_capture(path)
            self.window.replaying = True
            self.window.receive_frame(replay)
            before = self.window.frame_count
            self.window.receive_frame(demo.frame())
            self.assertEqual(self.window.frame_count, before)
            self.assertIn("REPLAY", self.window.source_badge.text())
            self.window.toggle_live(True)
            self.assertTrue(self.window.meter_stream.isChecked())
            self.assertFalse(self.window.replaying)
            self.window.toggle_live(False)
            self.assertFalse(self.window.live_button.isChecked())

    def test_readback_does_not_erase_a_newer_edit(self):
        row = self.window.rows["gen_freq"]
        row.pending_value = "1kHz"
        row.edit.setText("3kHz")
        row.mark_dirty()
        self.window.on_reply("gen_freq", "1000")
        self.assertEqual(row.text(), "3kHz")
        self.assertIn("1000", row.actual.text())

    def test_trigger_labels_follow_header_and_hide_in_xy(self):
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        frame = demo.frame()
        frame.header["Trig"]["Items"].update(Channel="CH2", Edge="FALL", Level="-250mV", Sweep="Normal", Coupling="AC")
        self.window.receive_frame(frame)
        text = self.window.trigger_status.text()
        for value in ("CH2", "Falling", "Normal", "-250 mV", "AC"):
            self.assertIn(value, text)
        self.assertAlmostEqual(self.window.trigger_line.value(), -.25)
        self.assertTrue(self.window.trigger_line.isVisible())
        self.window.rows["tr_level"].edit.setText("9V")
        self.window.render_scope()
        self.assertAlmostEqual(self.window.trigger_line.value(), -.25)
        self.window.view_mode.setCurrentIndex(1)
        self.assertFalse(self.window.trigger_line.isVisible())
        self.window.view_mode.setCurrentIndex(0)
        self.assertTrue(self.window.trigger_line.isVisible())

    def test_trigger_missing_header_does_not_leave_stale_marker(self):
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.receive_frame(demo.frame())
        self.assertTrue(self.window.trigger_line.isVisible())
        frame = demo.frame()
        del frame.header["Trig"]
        self.window.receive_frame(frame)
        self.assertFalse(self.window.trigger_line.isVisible())
        self.assertIn("Level —", self.window.trigger_status.text())

    def test_usb_tab_mode_hint_never_enables_generator(self):
        self.window.on_connected("USB • HDS2202S", "OWON,HDS2202S,test,V3")
        with patch.object(self.window.worker, "submit") as submit:
            for index, target in ((1, "Multimeter"), (2, "Generator"), (0, "Oscilloscope"), (4, "Oscilloscope")):
                self.window.tabs.setCurrentIndex(index)
                self.assertIn(target, self.window.mode_status.text())
                self.assertIn("Mode key", self.window.mode_status.text())
            self.assertFalse(any(call.args and call.args[0] == "command" for call in submit.call_args_list))

    def test_bare_resistance_reply_updates_display_plot_and_all_statistics(self):
        self.window.tabs.setCurrentIndex(1)
        self.pump(.03)
        self.window.receive_reading(dict(text="12.345", function="RESistance", range="20kOhm", host_time=time.time(), source="test"))
        self.assertIn("kΩ", self.window.meter_value.text())
        self.assertEqual(self.window.meter_plot.getAxis("left").labelUnits, "Ω")
        self.assertAlmostEqual(self.window.dmm_history[-1][1], 12345.)
        for widget in self.window.meter_stat_values.values():
            self.assertIn("Ω", widget.toolTip())
            self.assertNotIn("unspecified", widget.text())

    def test_changing_range_keeps_si_trend_and_changing_function_resets_it(self):
        def read(text, function, range_text):
            self.window.receive_reading(dict(text=text, function=function, range=range_text, host_time=time.time(), source="test"))
        read("250", "VOLTage", "mV")
        read("0.25", "VOLTage", "V")
        self.assertEqual(len(self.window.dmm_history), 2)
        self.assertAlmostEqual(self.window.dmm_history[0][1], self.window.dmm_history[1][1])
        read("123.4", "RESistance", "AUTO")
        self.assertEqual(len(self.window.dmm_history), 1)
        self.assertEqual(self.window.dmm_unit, "Ω")

    def test_meter_readings_cannot_resize_or_move_window(self):
        self.window.resize(1120, 740)
        self.window.tabs.setCurrentIndex(1)
        self.pump(.05)
        initial_size, initial_position = self.window.size(), self.window.pos()
        for function, range_text, text in (("RESistance", "AUTO", "1234567890123.4567"),
                                           ("VOLTage", "mV", "-123.456789"),
                                           ("CAPacitance", "nF", "0.0001234567"),
                                           ("CONTinuity", "AUTO", "1.2345e+24")):
            self.window.receive_reading(dict(text=text, function=function, range=range_text, host_time=time.time(), source="test"))
            self.pump(.03)
            self.assertEqual(self.window.size(), initial_size)
            self.assertEqual(self.window.pos(), initial_position)

    def test_dmm_log_preserves_raw_response_and_correct_si_unit(self):
        import csv
        from hdsstudio.storage import ReadingLog
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/"resistance.csv"
            self.window.reading_log = ReadingLog(path)
            self.window.receive_reading(dict(text="12.345", function="RESistance", range="20kOhm", host_time=time.time(), source="test"))
            self.window.stop_meter_log()
            with path.open(newline="", encoding="utf8") as f:
                row = list(csv.DictReader(f))[0]
            self.assertEqual(row["reading_text"], "12.345")
            self.assertEqual(float(row["numeric_SI"]), 12345.)
            self.assertEqual(row["unit"], "Ω")

    def test_generator_controls_start_unloaded_and_take_device_values(self):
        self.assertFalse(self.window.enable_output.isEnabled())
        self.assertEqual(self.window.rows["gen_amp"].text(), "1V")
        self.assertEqual(self.window.rows["gen_amp"].actual.text(), "not loaded")
        self.assertTrue(self.window.rows["gen_amp"].edit.isEnabled())
        self.assertTrue(self.window.waveform_selector.isEnabled())
        demo = DemoTransport()
        demo.settings[":function:amplitude"] = "2.25"
        self.window.generator_autoload = False
        self.window.on_connected("Demo", demo.text("*IDN?"))
        with patch.object(self.window.worker, "submit") as submit:
            self.window.on_generator_state(read_generator_state(demo))
            self.assertEqual(self.window.rows["gen_amp"].text(), "2.25V")
            self.assertTrue(self.window.enable_output.isEnabled())
            self.assertTrue(self.window.rows["gen_amp"].apply_button.isEnabled())
            self.assertFalse(self.window.rows["gen_width"].apply_button.isEnabled())
            self.assertFalse(any(call.args and call.args[0] == "command" for call in submit.call_args_list))

    def test_waveform_selector_remains_usable_after_failed_readback(self):
        with patch.object(self.window.worker, "submit") as submit:
            self.window.tabs.setCurrentIndex(2)
            self.window.on_generator_state(None)
            self.window.show_error("Unrecognized generator load reply")
            for index in range(12):
                self.window.waveform_selector.setCurrentIndex(index)
                self.window.choose_generator_waveform(index)
                self.assertEqual(self.window.generator_views.currentIndex(), 0)
                x, y = self.window.generator_curve.getData()
                self.assertEqual(len(x), 1200)
                self.assertTrue(np.all(np.isfinite(y)))
                self.assertFalse(self.window.apply_waveform.isEnabled())
            self.assertIn("Selection and local editing remain available", self.window.generator_sync_status.text())
            self.assertFalse(any(call.args[0] == "command" for call in submit.call_args_list))

    def test_keyboard_waveform_selection_works_and_unloaded_enter_cannot_write(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.tabs.setCurrentIndex(2)
        self.pump(.02)
        with patch.object(self.window.worker, "submit") as submit:
            selector = self.window.waveform_selector
            selector.setFocus()
            QTest.keyClick(selector, Qt.Key_Down)
            self.assertEqual(self.window.rows["gen_wave"].text(), "SQUare")
            row = self.window.rows["gen_amp"]
            row.edit.setFocus()
            QTest.keyClick(row.edit, Qt.Key_Return)
            self.assertFalse(submit.called)

    def test_waveform_selector_switches_between_draw_library_csv_and_device(self):
        from hdsstudio.commands import WAVES
        with patch.object(self.window.worker, "submit") as submit:
            self.window.tabs.setCurrentIndex(2)
            self.window.waveform_selector.setCurrentIndex(15)
            self.window.choose_generator_waveform(15)
            self.assertTrue(self.window.waveform_editor.draw.isChecked())
            self.assertEqual(self.window.generator_views.currentIndex(), 1)
            self.assertFalse(self.window.apply_waveform.isEnabled())
            editor = self.window.waveform_editor
            editor.load_template("Gaussian pulse")
            editor.save_cycle("My pulse")
            saved = editor.samples.copy()
            index = self.window.waveform_selector.count()-1
            self.assertIn("My pulse", self.window.waveform_selector.itemText(index))
            editor.clear()
            self.window.waveform_selector.setCurrentIndex(index)
            self.window.choose_generator_waveform(index)
            np.testing.assert_array_equal(editor.samples, saved)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td)/"wave.csv"
                path.write_text("normalized\n0\n1\n0\n-1\n0\n")
                with patch("hdsstudio.waveform_editor.QFileDialog.getOpenFileName", return_value=(str(path), "")):
                    self.window.waveform_selector.setCurrentIndex(14)
                    self.window.choose_generator_waveform(14)
                    self.assertIn("Imported wave.csv", editor.status.text())
            for index in (0, 4, 11, 2):
                self.window.waveform_selector.setCurrentIndex(index)
                self.window.choose_generator_waveform(index)
                self.assertEqual(self.window.rows["gen_wave"].text(), WAVES[index])
                self.assertEqual(self.window.generator_views.currentIndex(), 0)
            self.assertFalse(any(call.args[0] == "command" for call in submit.call_args_list))

    def test_waveform_apply_and_load_control_send_actual_commands(self):
        self.window.generator_autoload = False
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.on_generator_state(read_generator_state(demo))
        with patch.object(self.window.worker, "submit") as submit:
            self.window.tabs.setCurrentIndex(2)
            self.window.waveform_selector.setCurrentIndex(11)
            self.window.choose_generator_waveform(11)
            self.assertTrue(self.window.apply_waveform.isEnabled())
            self.window.apply_waveform.click()
            submit.assert_called_with("command", "gen_wave", ":FUNCtion Sinc")
            self.window.generator_controls.setCurrentIndex(2)
            load = self.window.rows["gen_load"]
            self.assertTrue(load.isVisible())
            self.assertTrue(load.apply_button.isEnabled())
            load.edit.setCurrentText("ON")
            load.apply_button.click()
            submit.assert_called_with("command", "gen_load", ":FUNCtion:LOAD ON")

    def test_device_readback_does_not_replace_custom_design_selection(self):
        self.window.generator_autoload = False
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.tabs.setCurrentIndex(2)
        self.window.waveform_selector.setCurrentIndex(15)
        self.window.choose_generator_waveform(15)
        self.window.on_generator_state(read_generator_state(demo))
        self.assertEqual(self.window.waveform_selector.currentIndex(), 15)
        self.assertEqual(self.window.generator_views.currentIndex(), 1)
        self.assertFalse(self.window.apply_waveform.isEnabled())

    def test_loaded_live_generator_allows_explicit_parameter_apply(self):
        demo = DemoTransport()
        demo.settings[":channel"] = "ON"
        self.window.generator_autoload = False
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.on_generator_state(read_generator_state(demo))
        self.assertFalse(self.window.enable_output.isEnabled())
        self.assertTrue(self.window.disable_output.isEnabled())
        self.assertTrue(self.window.rows["gen_amp"].apply_button.isEnabled())
        self.assertTrue(self.window.generator_controls.voltage_apply.isEnabled())
        self.assertIn("OUTPUT ON", self.window.generator_sync_status.text())

    def edit_generator(self, key, text):
        from PySide6.QtTest import QTest
        edit = self.window.rows[key].edit
        edit.setFocus()
        edit.selectAll()
        QTest.keyClicks(edit, text)
        self.app.processEvents()

    def test_voltage_edit_modes_link_both_ways_without_rewriting_typed_text(self):
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        self.edit_generator("gen_amp", "500 mVpp")
        self.assertEqual(self.window.rows["gen_amp"].text(), "500 mVpp")
        self.assertAlmostEqual(panel.get("gen_high"), .25)
        self.assertAlmostEqual(panel.get("gen_low"), -.25)
        self.edit_generator("gen_offset", "100mV")
        self.assertAlmostEqual(panel.get("gen_high"), .35)
        self.assertAlmostEqual(panel.get("gen_low"), -.15)
        panel.voltage_mode.setCurrentIndex(1)
        self.edit_generator("gen_high", "750 mV")
        self.edit_generator("gen_low", "-250mV")
        self.assertEqual(self.window.rows["gen_high"].text(), "750 mV")
        self.assertAlmostEqual(panel.get("gen_amp"), 1)
        self.assertAlmostEqual(panel.get("gen_offset"), .25)
        self.window.on_generator_state(None)
        self.assertEqual(self.window.rows["gen_high"].text(), "750 mV")

    def test_frequency_period_and_pulse_width_duty_are_linked(self):
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        self.edit_generator("gen_freq", "2.5 kHz")
        self.assertEqual(self.window.rows["gen_freq"].text(), "2.5 kHz")
        self.assertAlmostEqual(panel.get("gen_period"), 400e-6)
        panel.frequency_mode.setCurrentIndex(1)
        self.edit_generator("gen_period", "200 us")
        self.assertEqual(self.window.rows["gen_period"].text(), "200 us")
        self.assertAlmostEqual(panel.get("gen_freq"), 5000)
        self.window.waveform_selector.setCurrentIndex(3)
        self.window.choose_generator_waveform(3)
        panel.setCurrentIndex(1)
        panel.pulse_mode.setCurrentIndex(1)
        self.edit_generator("gen_width", "50 us")
        self.assertAlmostEqual(panel.get("gen_duty"), 25)
        panel.pulse_mode.setCurrentIndex(0)
        self.edit_generator("gen_duty", "40%")
        self.assertAlmostEqual(panel.get("gen_width"), 80e-6)

    def test_live_amplitude_pair_apply_updates_device_and_preserves_output(self):
        self.window.tabs.setCurrentIndex(2)
        self.window.worker.submit("connect", "Demo")
        self.wait_until(lambda: self.window.generator_loaded)
        self.window.enable_output.click()
        self.wait_until(lambda: self.window.generator_baseline.output_on)
        self.edit_generator("gen_amp", "500 mVpp")
        panel = self.window.generator_controls
        self.assertTrue(panel.voltage_apply.isEnabled())
        panel.voltage_apply.click()
        self.wait_until(lambda: not panel.pending and self.window.generator_baseline.values["gen_amp"] == "0.5V")
        self.assertTrue(self.window.generator_baseline.output_on)
        self.assertFalse(any(self.window.rows[k].dirty for k in ("gen_amp", "gen_offset", "gen_high", "gen_low")))
        self.assertFalse(self.errors)

    def test_waveform_specific_timing_menu_tracks_selection(self):
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        self.assertFalse(panel.isTabVisible(1))
        for index, pulse, ramp in ((3, True, False), (2, False, True), (1, False, False), (0, False, False)):
            self.window.waveform_selector.setCurrentIndex(index)
            self.window.choose_generator_waveform(index)
            self.assertEqual(panel.isTabVisible(1), pulse or ramp)
            self.assertEqual(not panel.pulse_box.isHidden(), pulse)
            self.assertEqual(not panel.ramp_box.isHidden(), ramp)

    def test_group_readback_does_not_clear_edits_made_during_apply(self):
        panel = self.window.generator_controls
        self.window.tabs.setCurrentIndex(2)
        self.edit_generator("gen_amp", "2Vpp")
        old_revision = panel.revision["levels"]
        panel.pending["levels"] = old_revision
        self.edit_generator("gen_amp", "3Vpp")
        self.window.on_generator_committed("levels", old_revision)
        self.assertTrue(self.window.rows["gen_amp"].dirty)
        self.assertEqual(self.window.rows["gen_amp"].text(), "3Vpp")

    def test_modulation_design_and_export_never_write_to_hardware(self):
        self.window.tabs.setCurrentIndex(2)
        with patch.object(self.window.worker, "submit") as submit:
            self.window.generator_views.setCurrentIndex(2)
            editor = self.window.modulation_editor
            self.assertIsNotNone(editor.result)
            editor.mode.setCurrentText("FM")
            editor.deviation.setValue(200)
            self.assertIsNotNone(editor.result)
            self.assertFalse(editor.depth.isEnabled())
            with tempfile.TemporaryDirectory() as td:
                path = Path(td)/"fm.csv"
                with patch("hdsstudio.modulation_editor.QFileDialog.getSaveFileName", return_value=(str(path), "")):
                    editor.export.click()
                self.assertTrue(path.exists())
                self.assertEqual(path.read_text().splitlines()[0], "time_s,voltage_V")
            self.assertFalse(submit.called)

    def test_pulse_readback_and_percent_input_refresh_the_preview(self):
        self.window.tabs.setCurrentIndex(2)
        self.window.waveform_selector.setCurrentIndex(3)
        self.window.choose_generator_waveform(3)
        self.window.on_reply("gen_duty", "25")
        _, volts = self.window.generator_curve.getData()
        self.assertAlmostEqual(np.mean(volts > 0), .25, delta=.005)
        panel = self.window.generator_controls
        panel.setCurrentIndex(1)
        self.edit_generator("gen_duty", "40%")
        _, volts = self.window.generator_curve.getData()
        self.assertAlmostEqual(np.mean(volts > 0), .4, delta=.005)
        self.window.generator_views.setCurrentIndex(2)
        self.window.generator_views.setCurrentIndex(0)
        self.assertIn("Built-in waveform preview", self.window.waveform_selection_note.text())

    def test_parameter_steps_are_independent_and_only_edit_drafts(self):
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        with patch.object(self.window.worker, "submit") as submit:
            panel.steppers["gen_freq"].plus.click()
            self.assertAlmostEqual(panel.get("gen_freq"), 1000.1)
            panel.steppers["gen_freq"].step.setCurrentText("100 Hz")
            panel.steppers["gen_freq"].plus.click()
            panel.steppers["gen_amp"].plus.click()
            panel.steppers["gen_offset"].plus.click()
            self.assertAlmostEqual(panel.get("gen_freq"), 1100.1)
            self.assertAlmostEqual(panel.get("gen_amp"), 1.01)
            self.assertAlmostEqual(panel.get("gen_offset"), .01)
            self.assertAlmostEqual(panel.get("gen_high"), .515)
            self.assertEqual(panel.steppers["gen_width"].step.currentText(), "1 us")
            self.assertEqual(panel.steppers["gen_rise"].step.currentText(), "1 ns")
            self.assertEqual(panel.steppers["gen_duty"].step.currentText(), "1%")
            self.assertFalse(submit.called)

    def test_step_keyboard_modifiers_preserve_time_units_and_linked_duty(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.tabs.setCurrentIndex(2)
        self.window.waveform_selector.setCurrentIndex(3)
        self.window.choose_generator_waveform(3)
        panel = self.window.generator_controls
        panel.configure(panel.configuration.findData("width"))
        self.edit_generator("gen_width", "50us")
        with patch.object(self.window.worker, "submit") as submit:
            QTest.keyClick(self.window.rows["gen_width"].edit, Qt.Key_Up, Qt.ShiftModifier)
            self.assertAlmostEqual(panel.get("gen_width"), 60e-6)
            QTest.keyClick(self.window.rows["gen_width"].edit, Qt.Key_Down, Qt.ControlModifier)
            self.assertAlmostEqual(panel.get("gen_width"), 59.9e-6)
            self.assertAlmostEqual(panel.get("gen_duty"), 5.99)
            panel.steppers["gen_rise"].plus.click()
            self.assertAlmostEqual(panel.get("gen_rise"), 11e-9)
            self.assertFalse(submit.called)

    def test_step_limits_reject_voltage_and_width_overflow_without_clamping(self):
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        self.edit_generator("gen_amp", "5 Vpp")
        panel.steppers["gen_amp"].plus.click()
        self.assertEqual(self.window.rows["gen_amp"].text(), "5 Vpp")
        self.assertIn("step would exceed", panel.status.text())
        self.window.waveform_selector.setCurrentIndex(3)
        self.window.choose_generator_waveform(3)
        panel.configure(panel.configuration.findData("width"))
        self.edit_generator("gen_width", "999 us")
        panel.steppers["gen_width"].plus.click()
        self.assertAlmostEqual(panel.get("gen_width"), 999e-6)
        self.assertIn("step would exceed", panel.timing_status.text())

    def test_square_pwm_configuration_prepares_pulse_without_hardware_writes(self):
        self.window.tabs.setCurrentIndex(2)
        self.window.waveform_selector.setCurrentIndex(1)
        self.window.choose_generator_waveform(1)
        panel = self.window.generator_controls
        with patch.object(self.window.worker, "submit") as submit:
            panel.configure(panel.configuration.findData("pwm"))
            self.assertEqual(self.window.rows["gen_wave"].text(), "PULSe")
            self.assertEqual(panel.configuration.currentData(), "pwm")
            self.assertEqual(panel.currentIndex(), 1)
            self.assertEqual(panel.pulse_mode.currentIndex(), 0)
            self.assertTrue(self.window.rows["gen_wave"].dirty)
            self.assertFalse(submit.called)

    def test_ramp_configurations_stage_symmetry_and_keep_integer_steps(self):
        self.window.generator_autoload = False
        demo = DemoTransport()
        demo.settings[":function"] = "RAMP"
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.on_generator_state(read_generator_state(demo))
        self.window.tabs.setCurrentIndex(2)
        panel = self.window.generator_controls
        with patch.object(self.window.worker, "submit") as submit:
            panel.configure(panel.configuration.findData("falling"))
            self.assertEqual(panel.get("gen_sym"), 0)
            self.assertTrue(self.window.rows["gen_sym"].apply_button.isEnabled())
            self.assertFalse(submit.called)
            panel.steppers["gen_sym"].adjust(1, .1)
            self.assertEqual(panel.get("gen_sym"), 1)
            self.window.rows["gen_sym"].apply_button.click()
            submit.assert_called_with("command", "gen_sym", ":FUNCtion:SYMMetry 1")

    def test_workspace_restores_configuration_and_independent_step_preferences(self):
        panel = self.window.generator_controls
        panel.steppers["gen_amp"].step.setCurrentText("100 mVpp")
        panel.steppers["gen_freq"].step.setCurrentText("10 kHz")
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td)/"steps.json")
            with patch("hdsstudio.ui.QFileDialog.getSaveFileName", return_value=(path, "")):
                self.window.save_preset()
            panel.steppers["gen_amp"].step.setCurrentText("1 Vpp")
            panel.steppers["gen_freq"].step.setCurrentText("1 Hz")
            with patch("hdsstudio.ui.QFileDialog.getOpenFileName", return_value=(path, "")), patch.object(self.window.worker, "submit") as submit:
                self.window.load_preset()
                self.assertFalse(submit.called)
            self.assertEqual(panel.steppers["gen_amp"].step.currentText(), "100 mVpp")
            self.assertEqual(panel.steppers["gen_freq"].step.currentText(), "10 kHz")

    def test_custom_editor_drawing_undo_and_csv_never_send_commands(self):
        editor = self.window.waveform_editor
        before = editor.samples.copy()
        with patch.object(self.window.worker, "submit") as submit:
            editor.start_stroke()
            editor.draw_point(.2, -.8)
            editor.draw_point(.7, .8)
            self.assertFalse(np.array_equal(before, editor.samples))
            self.assertLessEqual(np.max(np.abs(editor.samples)), 1)
            editor.undo_stroke()
            np.testing.assert_array_equal(editor.samples, before)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td)/"custom.csv"
                path.write_text("normalized\n0\n1\n0\n-1\n0\n")
                editor.load_csv(path)
                self.assertIn("nothing was transmitted", editor.status.text())
            self.assertFalse(editor.transfer.isEnabled())
            self.assertFalse(submit.called)

    def test_custom_shape_controls_and_recipe_restore_do_not_send_usb_commands(self):
        self.window.tabs.setCurrentIndex(2)
        self.window.generator_views.setCurrentIndex(1)
        e = self.window.waveform_editor
        with patch.object(self.window.worker, "submit") as submit:
            e.load_template("Stair up / down", dict(levels=7, turn_pct=40))
            e.parameter_widgets["levels"].setValue(5)
            e.save_cycle("Five steps")
            e.load_template("Sinc")
            e.parameter_widgets["window"].setCurrentText("Hann")
            e.load_cycle("Five steps")
            self.assertEqual(e.parameter_widgets["levels"].value(), 5)
            self.pump(.03)
            self.assertGreaterEqual(self.window.generator_views.height(), e.minimumSizeHint().height()+40)
            self.assertFalse(submit.called)
            self.assertFalse(e.transfer.isEnabled())

    def test_load_preview_is_separate_from_device_load_flag(self):
        self.window.generator_autoload = False
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.on_generator_state(read_generator_state(demo))
        with patch.object(self.window.worker, "submit") as submit:
            self.window.preview_load_mode.setCurrentIndex(1)
            self.window.preview_load_ohm.setValue(50)
            _, nominal = self.window.generator_curve.getData()
            _, estimate = self.window.generator_load_curve.getData()
            np.testing.assert_allclose(estimate, nominal*.5)
            self.assertEqual(self.window.rows["gen_load"].text(), "OFF")
            self.assertFalse(submit.called)

    def test_pending_generator_edits_block_enable_until_applied_or_queried(self):
        self.window.generator_autoload = False
        demo = DemoTransport()
        self.window.on_connected("Demo", demo.text("*IDN?"))
        self.window.on_generator_state(read_generator_state(demo))
        row = self.window.rows["gen_amp"]
        row.edit.setText("0.2V")
        row.mark_dirty()
        self.assertFalse(self.window.enable_output.isEnabled())
        with patch.object(self.window.worker, "submit") as submit:
            self.window.send_command("output", ":CHANnel ON")
            self.assertFalse(submit.called)

    def test_draw_with_mouse_changes_samples_on_the_canvas(self):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtTest import QTest
        self.window.tabs.setCurrentIndex(2)
        self.window.generator_views.setCurrentIndex(1)
        self.pump(.05)
        editor = self.window.waveform_editor
        editor.draw.setChecked(True)
        before = editor.samples.copy()
        point = editor.plot.mapFromScene(editor.plot.getViewBox().mapViewToScene(QPointF(.4, -.6)))
        QTest.mousePress(editor.plot.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        QTest.mouseRelease(editor.plot.viewport(), Qt.LeftButton, Qt.NoModifier, point)
        self.assertFalse(np.array_equal(before, editor.samples))
        index = int(.4*(len(editor.samples)-1))
        self.assertLess(editor.samples[index-3:index+4].min(), -.5)

    def test_mode_notification_is_nonblocking_and_tracks_usb_workspace(self):
        self.window.generator_autoload = False
        with patch.object(self.window.worker, "submit") as submit:
            self.window.on_connected("USB • HDS2202S", "OWON,HDS2202S,test,V3")
            size = self.window.size()
            self.assertTrue(self.window.mode_notification.isVisible())
            self.assertIn("Oscilloscope", self.window.mode_notification.title.text())
            self.window.tabs.setCurrentIndex(1)
            self.assertIn("Multimeter", self.window.mode_notification.title.text())
            self.window.mode_notification.hide()
            self.window.tabs.setCurrentIndex(2)
            self.assertTrue(self.window.mode_notification.isVisible())
            self.assertIn("Generator", self.window.mode_notification.title.text())
            self.assertEqual(size, self.window.size())
            self.assertFalse(any(call.args and call.args[0] == "command" for call in submit.call_args_list))

    def test_mode_notification_does_not_appear_for_demo_or_instrument_console(self):
        self.window.generator_autoload = False
        self.window.on_connected("Demo", "Demo")
        self.window.tabs.setCurrentIndex(1)
        self.assertFalse(self.window.mode_notification.isVisible())
        self.window.on_connected("USB • HDS2202S", "test")
        self.assertTrue(self.window.mode_notification.isVisible())
        self.window.tabs.setCurrentIndex(5)
        self.assertFalse(self.window.mode_notification.isVisible())


if __name__ == "__main__":
    unittest.main()
