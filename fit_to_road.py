#!/usr/bin/env python3
"""Change cycling activity metadata to Road without re-encoding sensor data.

Python 3.9+; install dependency: python -m pip install fitdecode==0.11.0

Examples:
    python fit_to_road.py test.fit
    python fit_to_road.py "*.fit" --output-dir road_files
    python fit_to_road.py test.fit --inspect

Only existing fields in cycling session/lap/sport/split/segment messages
are edited. Missing or invalid sport values in ancillary messages inherit
cycling from the validated sessions. Explicit other sports are left alone.
Sensor samples, coordinates, timestamps, embedded workouts,
developer fields and unknown messages are preserved byte for byte.
This changes file metadata, not the nature of the recorded ride. Platform
classification after import still needs to be verified separately.
"""

import argparse
from collections import Counter
import glob
import io
from pathlib import Path
import struct
import sys

try:
    import fitdecode
except ImportError:
    raise SystemExit(
        "Missing dependency. Run: python -m pip install fitdecode==0.11.0"
    )


# Global message number: (name, sport field, sub_sport field, profile-name field).
# Field numbers are from Garmin's FIT Profile. Do not confuse field numbers
# with their values: the desired values are sport=2 and sub_sport=7.
SCHEMAS = {
    12: ("sport", 0, 1, 3),
    18: ("session", 5, 6, 110),
    19: ("lap", 25, 39, None),
    142: ("segment_lap", 23, 32, None),
    312: ("split", 11, 12, None),
    313: ("split_summary", 1, 2, None),
}
SUBSPORT_NAMES = {0: "generic", 5: "spin", 6: "indoor_cycling", 7: "road", 58: "virtual_activity"}


def scan(data):
    """Return patchable field offsets after a full, strict CRC-checked decode."""
    if len(data) < 14 or data[8:12] != b".FIT":
        raise ValueError("Not a FIT file.")
    header_size = data[0]
    if header_size not in (12, 14):
        raise ValueError("Only standard 12-byte and 14-byte FIT headers are supported.")
    body_size = struct.unpack_from("<I", data, 4)[0]
    if header_size + body_size + 2 != len(data):
        raise ValueError("Truncated, concatenated or nonstandard FIT file; conversion stopped.")

    result = []
    file_types = []
    with fitdecode.FitReader(
        io.BytesIO(data), processor=None, keep_raw_chunks=True,
        check_crc=fitdecode.CrcCheck.RAISE,
        error_handling=fitdecode.ErrorHandling.RAISE,
    ) as reader:
        for frame in reader:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue
            if frame.global_mesg_num == 0:
                file_types.append(frame.get_raw_value("type", fallback=None))
            if frame.global_mesg_num not in SCHEMAS:
                continue

            fields = {}
            offset = frame.chunk.offset + 1  # The data-message header is one byte.
            for definition in frame.def_mesg.field_defs:
                fields[definition.def_num] = (offset, definition.size)
                offset += definition.size
            offset += sum(d.size for d in frame.def_mesg.dev_field_defs)
            if offset != frame.chunk.offset + len(frame.chunk.bytes):
                raise ValueError("Unexpected message layout; conversion stopped.")

            name, sport_id, sub_id, label_id = SCHEMAS[frame.global_mesg_num]

            def byte_value(field_id):
                if field_id not in fields:
                    return None
                field_offset, size = fields[field_id]
                if size != 1:
                    raise ValueError("Unexpected sport-field size in " + name)
                return data[field_offset]

            result.append({
                "name": name, "fields": fields,
                "sport": byte_value(sport_id), "sub_sport": byte_value(sub_id),
                "sport_id": sport_id, "sub_id": sub_id, "label_id": label_id,
            })

    if file_types != [4]:
        raise ValueError("Expected one FIT Activity file (file_id.type=4).")
    sessions = [m for m in result if m["name"] == "session"]
    if not sessions or any(m["sport"] != 2 for m in sessions):
        raise ValueError("Only cycling-only activity files are supported.")
    if any(m["sub_sport"] is None for m in sessions):
        raise ValueError("A session has no sub_sport field; this script cannot add missing fields.")
    return result


def is_cycling_message(message):
    # scan() has already required every session to have sport=cycling.
    # Ancillary messages may omit sport or use FIT's invalid enum (0xff).
    return message["sport"] in (None, 2, 0xff)


def convert(data):
    messages = scan(data)
    edited = bytearray(data)
    changes = Counter()
    for message in messages:
        if not is_cycling_message(message):
            continue
        name, fields = message["name"], message["fields"]
        sub_id = message["sub_id"]
        if sub_id in fields:
            offset, _ = fields[sub_id]
            if edited[offset] != 7:
                edited[offset] = 7
                changes[name + ".sub_sport"] += 1

        label_id = message["label_id"]
        if label_id in fields:
            offset, size = fields[label_id]
            if size >= 5:
                label = b"ROAD" + bytes(size - 4)
                if edited[offset:offset + size] != label:
                    edited[offset:offset + size] = label
                    changes[name + ".profile_name"] += 1

    if not changes:
        return data, changes
    edited[-2:] = struct.pack("<H", fitdecode.utils.compute_crc(edited[:-2]))
    output = bytes(edited)
    verified = scan(output)
    if len(output) != len(data):
        raise ValueError("Unexpected file-size change.")
    if any(is_cycling_message(m) and m["sub_sport"] not in (None, 7) for m in verified):
        raise ValueError("Output sport-field validation failed.")
    return output, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="FIT filenames or quoted wildcards such as '*.fit'")
    parser.add_argument("--output-dir", type=Path,
                        help="Write every result here, including unchanged Road files; default: beside each input file")
    parser.add_argument("--inspect", action="store_true", help="Read and validate only; do not write any files")
    args = parser.parse_args()
    inputs = []
    for pattern in args.files:
        matches = sorted(glob.glob(pattern)) if glob.has_magic(pattern) else [pattern]
        if not matches:
            parser.error("No files matched: " + pattern)
        for match in matches:
            path = Path(match).resolve()
            if path not in inputs:
                inputs.append(path)

    failures = 0
    for source in inputs:
        try:
            data = source.read_bytes()
            if args.inspect:
                counts = Counter((m["name"], m["sport"], m["sub_sport"]) for m in scan(data))
                print(str(source) + " [CRC OK]")
                for (name, sport, sub), count in counts.items():
                    print("  {}: sport={}, sub_sport={} ({}) x{}".format(
                        name, sport, sub, SUBSPORT_NAMES.get(sub, "other/missing"), count))
                continue

            output, changes = convert(data)
            if not changes and args.output_dir is None:
                print("Already Road: " + str(source))
                continue
            folder = args.output_dir.resolve() if args.output_dir else source.parent
            destination = folder / (source.stem + "_road.fit")
            if destination.exists():
                raise FileExistsError("Output already exists: " + str(destination))
            folder.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(output)
            if changes:
                print("Saved: {} [CRC OK; {} fields changed]".format(destination, sum(changes.values())))
            else:
                print("Copied unchanged: {} [CRC OK]".format(destination))
            for name, count in changes.items():
                print("  {}: {}".format(name, count))
        except Exception as error:
            failures += 1
            print("ERROR {}: {}".format(source, error), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
