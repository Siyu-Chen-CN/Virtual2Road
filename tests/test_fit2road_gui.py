"""Exercise GUI file handling and the actual Tk / worker event loop."""

from pathlib import Path
import queue
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from test_fit2road import activity
import fit2road
from fit2road_gui import Fit2RoadApp, convert_batch, convert_file


class FileConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "骑行.FIT"
        self.original = activity()
        self.source.write_bytes(self.original)

    def test_existing_outputs_and_sources_are_preserved(self):
        first = convert_file(self.source)
        second = convert_file(self.source)
        self.assertEqual(first.destination.name, "骑行_road.fit")
        self.assertEqual(second.destination.name, "骑行_road (2).fit")
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(first.destination.read_bytes(), second.destination.read_bytes())
        self.assertTrue(first.changed_fields)
        self.assertEqual(fit2road.scan(first.destination.read_bytes())[0]["sub_sport"], 7)

    def test_same_names_in_different_folders_share_output_safely(self):
        other = self.folder / "other" / self.source.name
        other.parent.mkdir()
        other.write_bytes(self.original)
        output = self.folder / "out"
        first = convert_file(self.source, output)
        second = convert_file(other, output)
        self.assertNotEqual(first.destination, second.destination)
        self.assertEqual(first.destination.parent, output)
        self.assertEqual(first.destination.read_bytes(), second.destination.read_bytes())

    def test_road_file_is_copied_to_custom_folder(self):
        original = fit2road.convert(self.original)[0]
        self.source.write_bytes(original)
        result = convert_file(self.source, self.folder / "out")
        self.assertEqual(result.changed_fields, 0)
        self.assertEqual(result.destination.read_bytes(), original)

    def test_bad_file_creates_no_output_and_batch_continues(self):
        bad = self.folder / "bad.fit"
        bad.write_bytes(b"not FIT")
        events = queue.Queue()
        output = self.folder / "out"
        convert_batch([("bad", bad), ("good", self.source)], output, events)
        messages = []
        while not events.empty():
            messages.append(events.get_nowait())
        self.assertEqual([event[0] for event in messages],
                         ["processing", "error", "processing", "success", "done"])
        self.assertEqual([path.name for path in output.iterdir()], ["骑行_road.fit"])

    def test_write_failure_removes_partial_output(self):
        real_open = Path.open

        class BrokenWriter:
            def __init__(self, path):
                self.handle = real_open(path, "xb")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def write(self, data):
                self.handle.write(data[:10])
                raise OSError("disk full")

        def failing_open(path, mode="r", *args, **kwargs):
            return BrokenWriter(path) if mode == "xb" else real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", failing_open):
            with self.assertRaisesRegex(OSError, "disk full"):
                convert_file(self.source)
        self.assertEqual(list(self.folder.iterdir()), [self.source])


class InterfaceTests(unittest.TestCase):
    def test_file_dialog_to_conversion_and_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "骑行.fit"
            source.write_bytes(activity())
            bad = folder / "bad.fit"
            bad.write_bytes(b"not FIT")
            root = tk.Tk()
            root.withdraw()
            try:
                app = Fit2RoadApp(root)
                with patch("fit2road_gui.filedialog.askopenfilenames",
                           return_value=(str(source), str(bad), str(source))):
                    app.add_button.invoke()
                self.assertEqual(len(app.files), 2)
                with patch("fit2road_gui.filedialog.askdirectory", return_value=str(folder / "out")):
                    app.folder_button.invoke()

                for attempt in range(2):
                    app.convert_button.invoke()
                    self.assertTrue(app.busy)
                    self.assertTrue(app.add_button.instate(["disabled"]))
                    app.add_files([folder / "should_not_be_added.fit"])
                    app.close()
                    self.assertTrue(root.winfo_exists())
                    deadline = time.monotonic() + 10
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy, "Background conversion did not finish")
                    self.assertEqual((app.successes, app.failures), (1, 1))
                    self.assertEqual(len(app.files), 2)
                    self.assertTrue(app.convert_button.instate(["!disabled"]))
                    self.assertEqual(len(list((folder / "out").glob("*.fit"))), attempt + 1)
                good_item, bad_item = app.files
                app.tree.selection_set(bad_item)
                app.show_detail()
                self.assertIn("Not a FIT file", app.detail.get())
                app.tree.selection_set(good_item)
                with patch("fit2road_gui.os.startfile") as open_folder:
                    app.open_button.invoke()
                    open_folder.assert_called_once_with(str(folder / "out"))
                app.clear_button.invoke()
                self.assertFalse(app.files)
                self.assertTrue(app.convert_button.instate(["disabled"]))
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
