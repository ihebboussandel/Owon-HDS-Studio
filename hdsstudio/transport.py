"""Bounded, exclusive HDS2202S bulk-USB transport.

Based on the project's exercised ThermoLab OwonUSB transport conventions.
One worker owns the device. Any timeout invalidates the connection, preventing
late responses to unsupported firmware commands from becoming other readings.
"""
from pathlib import Path
import time
from .commands import validate_command


class USBTransport:
    def __init__(self, trace=None):
        self.device = None
        self.interface = None
        self.claimed = False
        self.detached = False
        self.last_write = 0.
        self.trace = trace or (lambda _: None)

    def open(self):
        try:
            import usb.core
            import usb.util
        except ImportError as exc:
            raise RuntimeError("Install PyUSB and the libusb system library for USB mode") from exc
        self.util = usb.util
        devices = list(usb.core.find(find_all=True, idVendor=0x5345, idProduct=0x1234) or [])
        if len(devices) != 1:
            raise RuntimeError(f"Expected one HDS2202S, found {len(devices)}. Connect USB and select System → USB → HID on the scope.")
        self.device = devices[0]
        try:
            for interface in self.device.get_active_configuration():
                ins = [e for e in interface if e.bmAttributes & 3 == 2 and e.bEndpointAddress & 0x80]
                outs = [e for e in interface if e.bmAttributes & 3 == 2 and not e.bEndpointAddress & 0x80]
                if not ins or not outs:
                    continue
                self.interface = interface.bInterfaceNumber
                self.ep_in, self.ep_out = ins[0].bEndpointAddress, outs[0].bEndpointAddress
                try:
                    active = self.device.is_kernel_driver_active(self.interface)
                except NotImplementedError:
                    active = False
                if active:
                    driver = self._kernel_driver()
                    if driver != "usb_serial_simple":
                        raise RuntimeError(f"USB interface is owned by {driver!r}; close other instrument software")
                    self.device.detach_kernel_driver(self.interface)
                    self.detached = True
                self.util.claim_interface(self.device, self.interface)
                self.claimed = True
                self.trace("USB interface claimed")
                return
            raise RuntimeError("No bulk interface. Select HID USB mode on the front panel")
        except Exception as exc:
            self.close()
            if getattr(exc, "errno", None) == 13 or getattr(exc, "backend_error_code", None) == -3:
                raise RuntimeError("USB permission denied. See README → Linux USB access, then reconnect the cable") from exc
            raise

    def _kernel_driver(self):
        for node in Path("/sys/bus/usb/devices").glob("*"):
            try:
                if int((node/"busnum").read_text()) != self.device.bus or int((node/"devnum").read_text()) != self.device.address:
                    continue
                for entry in node.glob(node.name+":*"):
                    if int((entry/"bInterfaceNumber").read_text(), 16) == self.interface:
                        return (entry/"driver").resolve().name if (entry/"driver").exists() else None
            except (OSError, ValueError):
                continue
        return None

    def send(self, command):
        command = validate_command(command)
        if not self.claimed:
            raise RuntimeError("USB is disconnected")
        delay = .02 - (time.monotonic()-self.last_write)
        if delay > 0:
            time.sleep(delay)
        self.trace("→ " + command)
        payload = command.encode("ascii")
        count = self.device.write(self.ep_out, payload, timeout=1200)
        self.last_write = time.monotonic()
        if count != len(payload):
            raise IOError("Incomplete USB command write")

    def write(self, command):
        if command.rstrip().endswith("?"):
            raise ValueError("Use query for commands returning a response")
        self.send(command)

    def _read(self):
        result = bytes(self.device.read(self.ep_in, 65536, timeout=1200))
        if not result:
            raise IOError("Empty USB response")
        return result

    def text(self, command):
        self.send(command)
        result = self._read().decode("ascii").strip("\x00\r\n ")
        if not result:
            raise IOError("Empty text response; command may be unsupported")
        self.trace("← " + result)
        return result

    def block(self, command):
        self.send(command)
        buffer = bytearray()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            buffer.extend(self._read())
            if len(buffer) >= 4:
                size = int.from_bytes(buffer[:4], "little")
                if not 1 <= size <= 65532:
                    raise IOError(f"Invalid OWON block length: {size}")
                if len(buffer) >= 4+size:
                    if len(buffer) != 4+size:
                        raise IOError("Unexpected trailing waveform bytes")
                    self.trace(f"← {size} bytes")
                    return bytes(buffer[4:])
        raise TimeoutError("Incomplete waveform response")

    def close(self):
        if self.device is None:
            return
        try:
            if self.claimed:
                self.util.release_interface(self.device, self.interface)
        finally:
            self.claimed = False
            try:
                if self.detached:
                    self.device.attach_kernel_driver(self.interface)
            finally:
                self.detached = False
                self.util.dispose_resources(self.device)
                self.device = None
                self.trace("USB interface released")
