# Contributing to HDS Studio

Thank you for considering a contribution. HDS Studio is intended to make the OWON HDS2202S usable from Linux, and community improvements are very welcome. A small documentation fix or a clear bug report is useful too.

Please open an issue to share a problem or idea, or submit a pull request with your improvement. Helpful areas include Linux USB access, testing with actual HDS2202S firmware, improving the interface, adding analysis tools, and researching missing protocol features.

## Running and checking changes

Use Python 3.10 or newer, install `requirements.txt` in a virtual environment, and run `python launch.py`. Demo mode lets you develop without hardware. Run the existing checks with:

```bash
python -m unittest discover -s tests -v
```

If the interface changes, refresh its demo screenshots with:

```bash
python launch.py --preview-dir screenshots
```

Please describe the problem, your change, and how you checked it in your pull request. Add focused tests for protocol, parsing, numerical, or device-state changes when appropriate.

## Sharing hardware findings

For a device issue, it helps to include your Linux distribution, app/Python version, scope model and firmware version, the selected front-panel mode, and the exact error or command trace. Remove any private information before sharing captures or logs. If no hardware was available, simply say that your testing used Demo mode.

Keep device commands traceable to manufacturer documentation or a reproducible hardware observation. Please keep read-only queries separate from output-changing operations, preserve explicit generator output control, and label local previews as previews. Source impedance, load reference, and custom-waveform transfer are different capabilities: a proposed implementation should explain what the hardware actually supports.

There is currently no verified mode-switch or custom-waveform upload command. If you discover one, a protocol reference and a repeatable test on the HDS2202S would be particularly valuable. Thank you for helping improve Linux support for this scope.
