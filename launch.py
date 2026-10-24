#!/usr/bin/env python3
"""Run HDS Studio; --preview-dir generates deterministic offscreen previews."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="HDS Studio — OWON HDS2202S workbench")
    parser.add_argument("--no-demo", action="store_true", help="Start disconnected")
    parser.add_argument("--preview-dir", type=Path, help="Render all tabs/themes offscreen, then exit")
    args = parser.parse_args()
    if args.preview_dir:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from hdsstudio.ui import StudioWindow
        from hdsstudio.device import DemoTransport
    except ImportError as exc:
        print(f"Missing dependency: {exc}\nInstall: python -m pip install -r {Path(__file__).with_name('requirements.txt')}", file=sys.stderr)
        return 1
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = StudioWindow(start_demo=not args.no_demo and not args.preview_dir, persist=not bool(args.preview_dir))
    window.show()
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        demo = DemoTransport()
        demo.open()
        window.generator_autoload = False
        window.on_connected("Demo", demo.text("*IDN?"))
        from hdsstudio.generator import read_generator_state
        window.on_generator_state(read_generator_state(demo))
        window.waveform_editor.load_template("Gaussian pulse")
        window.waveform_editor.save_cycle("Gaussian pulse demo")
        window.receive_frame(demo.frame())
        tasks = [(theme, tab) for theme in ("Midnight", "Daylight", "Phosphor") for tab in range(13)]

        def render_next():
            if not tasks:
                window.close()
                return
            theme, tab = tasks.pop(0)
            window.theme.setCurrentText(theme)
            if tab in (2, 6, 7, 8, 9, 10, 11, 12):
                demo.write(":FUNCtion RAMP" if tab == 9 else ":FUNCtion PULSe" if tab in (8, 10) else ":FUNCtion SINE")
                if tab in (8, 10):
                    demo.write(":FUNCtion:PULSe:DTYCycle 25")
                    window.generator_controls.configuration_cache["PULSE"] = "edges" if tab == 10 else "pwm"
                if tab == 9:
                    demo.write(":FUNCtion:SYMMetry 50")
                    window.generator_controls.configuration_cache["RAMP"] = "triangle"
                window.on_generator_state(read_generator_state(demo))
                window.generator_controls.update_configurations()
            window.tabs.setCurrentIndex(2 if tab >= 6 else tab)
            window.generator_views.setCurrentIndex(2 if tab == 7 else 1 if tab in (6, 11, 12) else 0)
            window.generator_controls.setCurrentIndex(1 if tab in (8, 9, 10) else 0)
            if tab in (8, 10):
                # A preview has no active text edit to preserve during readback.
                for row in window.rows.values():
                    row.edit.clearFocus()
                for key in ("gen_duty", "gen_rise", "gen_fall"):
                    window.on_reply(key, demo.text(window.rows[key].spec.command+"?"))
            if tab == 9:
                window.on_reply("gen_sym", demo.text(":FUNCtion:SYMMetry?"))
            if tab == 6:
                index = window.waveform_selector.count()-1
                window.waveform_selector.setCurrentIndex(index)
                window.choose_generator_waveform(index)
            if tab in (11, 12):
                window.waveform_editor.load_template("Stair up / down" if tab == 11 else "Sinc",
                                                    dict(levels=7, turn_pct=40) if tab == 11 else
                                                    dict(lobes=6, width_pct=70, window="Hann"))
            if tab == 1:
                import time
                window.clear_meter()
                resistance = theme == "Phosphor"
                for i in range(80):
                    value = (12.345 if resistance else 5.002)+.003*__import__('math').sin(i/8)
                    window.receive_reading(dict(text=f"{value:.5f}", host_time=time.time()+i*.2, source="DEMO",
                                                function="RESistance" if resistance else "VOLTage DC",
                                                range="20kOhm" if resistance else "V"))
            window.render_spectrum(advance=True)
            def save():
                from PySide6.QtWidgets import QScrollArea
                parent = window.generator_controls.parentWidget()
                while parent and not isinstance(parent, QScrollArea):
                    parent = parent.parentWidget()
                if parent:
                    parent.verticalScrollBar().setValue(parent.verticalScrollBar().maximum() if tab == 10 else 0)
                parent = window.waveform_editor.parentWidget()
                while parent and not isinstance(parent, QScrollArea):
                    parent = parent.parentWidget()
                if parent:
                    parent.verticalScrollBar().setValue(parent.verticalScrollBar().maximum() if tab in (6, 11, 12) else 0)
                app.processEvents()
                for plot in window.plots:
                    plot.viewport().repaint()
                path = args.preview_dir/f"{theme.lower()}_{tab}_{('scope','multimeter','generator','math','spectrum','instrument','waveform_editor','modulation','pulse_timing','ramp_setup','edge_timing','custom_stairs','custom_sinc')[tab]}.png"
                if not window.grab().save(str(path)):
                    raise RuntimeError("Failed to save preview " + str(path))
                print(path)
                QTimer.singleShot(20, render_next)
            QTimer.singleShot(100, save)

        QTimer.singleShot(200, render_next)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
