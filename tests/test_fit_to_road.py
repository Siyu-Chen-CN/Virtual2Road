"""Exercise binary preservation and CLI behavior with synthetic FIT fixtures."""

import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import fitdecode

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import fit_to_road


def message(number, fields, endian="<", local=0, developer_fields=(), time_offset=None):
    """Build a definition and its data without using the converter."""
    flags = 0x40 | local | (0x20 if developer_fields else 0)
    definition = bytes([flags, 0, int(endian == ">")])
    definition += struct.pack(endian + "H", number) + bytes([len(fields)])
    definition += b"".join(bytes([field, len(value), base]) for field, base, value in fields)
    if developer_fields:
        definition += bytes([len(developer_fields)])
        definition += b"".join(bytes([field, len(value), index])
                               for field, index, value in developer_fields)
    header = local if time_offset is None else 0x80 | (local << 5) | time_offset
    payload = bytes([header]) + b"".join(value for _, _, value in fields)
    payload += b"".join(value for _, _, value in developer_fields)
    return definition + payload


def container(body, header_size=14, header_crc=True):
    header = struct.pack("<BBHI4s", header_size, 0x20, 21800, len(body), b".FIT")
    if header_size == 14:
        crc = fitdecode.utils.compute_crc(header) if header_crc else 0
        header += struct.pack("<H", crc)
    data = header + body
    return data + struct.pack("<H", fitdecode.utils.compute_crc(data))


def activity(extra=b"", session_fields=None, endian="<", header_size=14,
             developer=False, header_crc=True):
    body = message(0, [(0, 0, b"\x04")], endian)
    if developer:
        body += message(207, [(1, 13, bytes(range(16))), (3, 2, b"\x00")], endian)
        body += message(206, [(0, 2, b"\x00"), (1, 2, b"\x00"),
                              (2, 2, b"\x84"), (3, 7, b"custom\x00"),
                              (8, 7, b"units\x00")], endian)
    if session_fields is None:
        session_fields = [(5, 0, b"\x02"), (6, 0, b"\x06"),
                          (110, 7, b"INDOOR\x00\x00")]
    dev_fields = [(0, 0, struct.pack(endian + "H", 1234))] if developer else ()
    body += message(18, session_fields, endian, local=1, developer_fields=dev_fields)
    record = [(253, 0x86, struct.pack(endian + "I", 1000000)),
              (0, 0x85, struct.pack(endian + "i", 620000000)),
              (1, 0x85, struct.pack(endian + "i", 59000000)),
              (3, 2, b"\x96"), (4, 2, b"\x5a"),
              (5, 0x86, struct.pack(endian + "I", 123456)),
              (7, 0x84, struct.pack(endian + "H", 250))]
    body += message(20, record, endian, local=2, developer_fields=dev_fields)
    body += message(20, [(3, 2, b"\x97"), (7, 0x84, struct.pack(endian + "H", 260))],
                    endian, local=2, time_offset=3)
    body += message(26, [(4, 0, b"\x02"), (11, 0, b"\x06")], endian)
    body += message(60000, [(1, 13, b"\xaa\xbb\xcc")], endian)
    return container(body + extra, header_size, header_crc)


def frames(data):
    with fitdecode.FitReader(io.BytesIO(data), processor=None, keep_raw_chunks=True,
                             check_crc=fitdecode.CrcCheck.RAISE) as reader:
        return list(reader)


class ConversionTests(unittest.TestCase):
    def test_all_supported_messages(self):
        extra = b""
        for number, (_, sport, sub, label) in fit_to_road.SCHEMAS.items():
            fields = [(sport, 0, b"\x02"), (sub, 0, b"\x3a")]
            if label is not None:
                fields.append((label, 7, b"VIRTUAL\x00"))
            extra += message(number, fields)
        output, changes = fit_to_road.convert(activity(extra))
        self.assertTrue(changes)
        for item in fit_to_road.scan(output):
            self.assertEqual(item["sub_sport"], 7)

    def test_ancillary_missing_and_invalid_sport_inherit_cycling(self):
        for number, (name, sport, sub, label) in fit_to_road.SCHEMAS.items():
            if name == "session":
                continue
            for value in (None, b"\xff"):
                with self.subTest(message=name, sport=value):
                    fields = [(sub, 0, b"\x06")]
                    if value is not None:
                        fields.insert(0, (sport, 0, value))
                    if label is not None:
                        fields.append((label, 7, b"INDOOR\x00"))
                    output, changes = fit_to_road.convert(activity(message(number, fields)))
                    converted = fit_to_road.scan(output)[-1]
                    self.assertEqual(converted["sub_sport"], 7)
                    self.assertEqual(converted["sport"], None if value is None else 255)
                    self.assertIn(name + ".sub_sport", changes)
                    if label is not None:
                        offset, size = converted["fields"][label]
                        self.assertEqual(output[offset:offset + size], b"ROAD\x00\x00\x00")

    def test_explicit_other_sport_is_untouched(self):
        extra = message(19, [(25, 0, b"\x01"), (39, 0, b"\x01")])
        original = activity(extra)
        output, _ = fit_to_road.convert(original)
        self.assertEqual(fit_to_road.scan(output)[-1]["sub_sport"], 1)
        self.assertEqual(output[-2-len(extra):-2], extra)

    def test_missing_ancillary_sub_sport_is_not_added(self):
        original = activity(message(19, [(25, 0, b"\x02")]))
        output, _ = fit_to_road.convert(original)
        self.assertIsNone(fit_to_road.scan(output)[-1]["sub_sport"])
        self.assertEqual(len(original), len(output))

    def test_headers_endianness_and_byte_preservation(self):
        for header in (12, 14):
            for endian in ("<", ">"):
                with self.subTest(header=header, endian=endian):
                    original = activity(header_size=header, endian=endian, developer=True)
                    output, _ = fit_to_road.convert(original)
                    allowed = {len(original)-2, len(original)-1}
                    for item in fit_to_road.scan(original):
                        for field in (item["sub_id"], item["label_id"]):
                            if field in item["fields"]:
                                offset, size = item["fields"][field]
                                allowed.update(range(offset, offset + size))
                    self.assertEqual(len(original), len(output))
                    differences = {i for i, (a, b) in enumerate(zip(original, output)) if a != b}
                    self.assertTrue(differences <= allowed)
                    before = [f.chunk.bytes for f in frames(original)
                              if f.frame_type == fitdecode.FIT_FRAME_DATA and
                              f.global_mesg_num != 18]
                    after = [f.chunk.bytes for f in frames(output)
                             if f.frame_type == fitdecode.FIT_FRAME_DATA and
                             f.global_mesg_num != 18]
                    self.assertEqual(before, after)

    def test_optional_zero_header_crc(self):
        output, _ = fit_to_road.convert(activity(header_crc=False))
        self.assertEqual(output[12:14], b"\x00\x00")
        frames(output)

    def test_idempotent(self):
        output, _ = fit_to_road.convert(activity())
        again, changes = fit_to_road.convert(output)
        self.assertEqual(again, output)
        self.assertFalse(changes)

    def test_short_profile_name_is_preserved(self):
        original = activity(session_fields=[(5, 0, b"\x02"), (6, 0, b"\x06"),
                                            (110, 7, b"IN\x00")])
        output, changes = fit_to_road.convert(original)
        item = fit_to_road.scan(output)[0]
        offset, size = item["fields"][110]
        self.assertEqual(output[offset:offset + size], b"IN\x00")
        self.assertEqual(dict(changes), {"session.sub_sport": 1})

    def test_missing_or_noncycling_session_sport_is_rejected(self):
        for sport in (None, b"\x01", b"\xff"):
            with self.subTest(sport=sport):
                fields = [(6, 0, b"\x06")]
                if sport is not None:
                    fields.append((5, 0, sport))
                with self.assertRaisesRegex(ValueError, "cycling-only"):
                    fit_to_road.convert(activity(session_fields=fields))

    def test_missing_session_sub_sport_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no sub_sport"):
            fit_to_road.convert(activity(session_fields=[(5, 0, b"\x02")]))

    def test_invalid_existing_session_sub_sport_can_be_replaced(self):
        output, _ = fit_to_road.convert(activity(session_fields=[(5, 0, b"\x02"),
                                                               (6, 0, b"\xff")]))
        self.assertEqual(fit_to_road.scan(output)[0]["sub_sport"], 7)

    def test_multisport_is_rejected(self):
        extra = message(18, [(5, 0, b"\x01"), (6, 0, b"\x00")])
        with self.assertRaisesRegex(ValueError, "cycling-only"):
            fit_to_road.convert(activity(extra))

    def test_nonactivity_file_is_rejected(self):
        body = message(0, [(0, 0, b"\x05")])
        body += message(18, [(5, 0, b"\x02"), (6, 0, b"\x06")])
        with self.assertRaisesRegex(ValueError, "Activity"):
            fit_to_road.convert(container(body))

    def test_bad_crc_is_rejected(self):
        for position in (12, -1):
            with self.subTest(position=position):
                data = bytearray(activity())
                data[position] ^= 1
                with self.assertRaises(fitdecode.FitCRCError):
                    fit_to_road.convert(bytes(data))

    def test_invalid_container_is_rejected(self):
        valid = activity()
        for data in (b"not FIT", valid[:-3], valid + valid):
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    fit_to_road.convert(data)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "ride.fit"
        self.source.write_bytes(activity())

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(PROJECT / "fit_to_road.py"),
                               *map(str, args)], cwd=self.folder, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=20)

    def test_default_output_and_original_preservation(self):
        original = self.source.read_bytes()
        result = self.run_cli(self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual((self.folder / "ride_road.fit").read_bytes(),
                         fit_to_road.convert(original)[0])

    def test_already_road_without_output_dir_skips(self):
        self.source.write_bytes(fit_to_road.convert(activity())[0])
        result = self.run_cli(self.source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already Road", result.stdout)
        self.assertFalse((self.folder / "ride_road.fit").exists())

    def test_already_road_with_output_dir_is_copied(self):
        original = fit_to_road.convert(activity())[0]
        self.source.write_bytes(original)
        result = self.run_cli(self.source, "--output-dir", "out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.folder / "out/ride_road.fit").read_bytes(), original)
        self.assertIn("Copied unchanged", result.stdout)

    def test_existing_destination_is_not_overwritten(self):
        destination = self.folder / "ride_road.fit"
        destination.write_bytes(b"keep existing file")
        result = self.run_cli(self.source)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(destination.read_bytes(), b"keep existing file")

    def test_inspect_does_not_write(self):
        original = self.source.read_bytes()
        result = self.run_cli(self.source, "--inspect", "--output-dir", "out")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("indoor_cycling", result.stdout)
        self.assertEqual(list(self.folder.iterdir()), [self.source])
        self.assertEqual(self.source.read_bytes(), original)

    def test_glob_deduplication_and_batch_errors(self):
        (self.folder / "bad.fit").write_bytes(b"not FIT")
        result = self.run_cli("*.fit", self.source, "--output-dir", "out")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("Saved:"), 1)
        self.assertIn("bad.fit", result.stderr)
        self.assertEqual([f.name for f in (self.folder / "out").iterdir()], ["ride_road.fit"])

    def test_unmatched_glob_is_an_error(self):
        result = self.run_cli("missing-*.fit")
        self.assertEqual(result.returncode, 2)
        self.assertIn("No files matched", result.stderr)

    def test_help(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--inspect", result.stdout)


if __name__ == "__main__":
    unittest.main()
