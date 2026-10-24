import unittest

from hdsstudio.multimeter import decode_reading, function_info, read_multimeter


class MultimeterTests(unittest.TestCase):
    def test_resistance_without_a_unit_suffix(self):
        result = decode_reading(dict(text="1234.5", function="RESistance", range="AUTO"))
        self.assertEqual(result.unit, "Ω")
        self.assertEqual(result.value, 1234.5)
        self.assertEqual(result.display, "1234.5 Ω")
        self.assertEqual(result.function, "Resistance")

    def test_range_prefixes_preserve_display_and_convert_si(self):
        for function, range_text, text, value, unit, display in (
            ("RES", "20kOhm", "12.345", 12345., "Ω", "12.345 kΩ"),
            ("RESistance", "200MΩ", "1.5", 1.5e6, "Ω", "1.5 MΩ"),
            ("VOLTage DC", "mV", "123.45", .12345, "V", "123.45 mV"),
            ("CURRent AC", "200mA", "12.34", .01234, "A", "12.34 mA"),
            ("CAPacitance", "200nF", "47.2", 47.2e-9, "F", "47.2 nF"),
        ):
            with self.subTest(range=range_text):
                result = decode_reading(dict(text=text, function=function, range=range_text))
                self.assertAlmostEqual(result.value, value)
                self.assertEqual(result.unit, unit)
                self.assertEqual(result.display, display)

    def test_explicit_unit_wins_over_range_and_is_not_scaled_twice(self):
        result = decode_reading(dict(text="250mV", function="VOLTage", range="200mV"))
        self.assertEqual(result.unit, "V")
        self.assertEqual(result.value, .25)
        for text in ("12.34 kohm", "12.34 kOhms", "12.34 kΩ"):
            self.assertEqual(decode_reading(dict(text=text)).value, 12340.)

    def test_supported_function_units(self):
        for function, unit in (("DCV", "V"), ("ACV", "V"), ("DCA", "A"), ("ACA", "A"),
                               ("DIODe", "V"), ("CONTinuity", "Ω"), ("CAPacitance", "F")):
            with self.subTest(function=function):
                result = decode_reading(dict(text="1.5", function=function, range="AUTO"))
                self.assertEqual(result.unit, unit)
                self.assertEqual(result.value, 1.5)

    def test_unknown_or_conflicting_context_never_uses_dropdown_defaults(self):
        unknown = decode_reading(dict(text="123.4"))
        self.assertIsNone(unknown.value)
        self.assertIn("determine the unit", unknown.status)
        conflicting = decode_reading(dict(text="123.4", function="RESistance", range="mV"))
        self.assertIsNone(conflicting.value)
        self.assertIn("disagree", conflicting.status)

    def test_overload_and_unitful_comma_response(self):
        for text in ("OL", "OVERLOAD", "nan", "1e37 V"):
            self.assertIsNone(decode_reading(dict(text=text, function="VOLTage")).value)
        self.assertAlmostEqual(decode_reading(dict(text="1.25,µA DC")).value, 1.25e-6)

    def test_worker_reads_scope_context_without_writes(self):
        class Fake:
            commands = []
            def text(self, command):
                self.commands.append(command)
                return {":DMM:CONFigure?": "RESistance", ":DMM:RANGE?": "20kOhm", ":DMM:MEAS?": "1.2345"}[command]
        fake = Fake()
        packet = read_multimeter(fake, "OWON,HDS2202S,test,V3")
        self.assertEqual(packet["function"], "RESistance")
        self.assertEqual(packet["range"], "20kOhm")
        self.assertEqual(packet["source"], "OWON,HDS2202S,test,V3")
        self.assertAlmostEqual(decode_reading(packet).value, 1234.5)
        self.assertTrue(all(cmd.endswith("?") for cmd in fake.commands))

    def test_front_panel_change_during_transfer_is_rejected(self):
        class Changed:
            def __init__(self, replies):
                self.replies = iter(replies)
            def text(self, command):
                return next(self.replies)
        for replies in (("RESistance", "AUTO", "1.2", "VOLTage", "AUTO"),
                        ("VOLTage", "mV", "1.2", "VOLTage", "V")):
            with self.subTest(replies=replies), self.assertRaisesRegex(ValueError, "changed"):
                read_multimeter(Changed(replies), "test")


if __name__ == "__main__":
    unittest.main()
