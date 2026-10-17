"""Desktop interface for converting cycling FIT files to Road."""

from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from fit2road import convert


def enable_high_dpi():
    """Opt source launches into the same DPI mode as the packaged manifest.

    Tk 8.6 uses a single display scale. System awareness keeps its fonts and
    pixel layout consistent; call this before constructing any Tk windows.
    An exe's manifest already sets the mode, so access-denied is expected.
    """
    if os.name != "nt":
        return
    try:
        set_awareness = ctypes.windll.shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        set_awareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


@dataclass(frozen=True)
class ConversionResult:
    destination: Path
    changed_fields: int


def convert_file(source, output_dir=None):
    """Convert one file, creating a new output even when it is already Road.

    Exclusive creation protects both inputs and existing outputs. Numbered
    names also allow different input folders to contain the same filename.
    """
    source = Path(source).resolve()
    output, changes = convert(source.read_bytes())
    folder = Path(output_dir).resolve() if output_dir is not None else source.parent
    folder.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        suffix = "" if number == 1 else " ({})".format(number)
        destination = folder / (source.stem + "_road" + suffix + ".fit")
        try:
            handle = destination.open("xb")
        except FileExistsError:
            number += 1
            continue
        try:
            with handle:
                handle.write(output)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return ConversionResult(destination, sum(changes.values()))


def convert_batch(entries, output_dir, events):
    """Send progress to a queue; the worker never calls Tk."""
    for item, source in entries:
        events.put(("processing", item, None))
        try:
            result = convert_file(source, output_dir)
        except Exception as error:
            events.put(("error", item, str(error)))
        else:
            events.put(("success", item, result))
    events.put(("done", None, None))


class Fit2RoadApp:
    def __init__(self, root):
        self.root = root
        self.files = {}
        self.outputs = {}
        self.details = {}
        self.events = queue.Queue()
        self.busy = False
        self.successes = 0
        self.failures = 0
        self.output_dir = tk.StringVar(root)
        self.status = tk.StringVar(root, "请先添加需要转换的 FIT 文件。")
        self.detail = tk.StringVar(root, "选择文件行可查看完整路径与处理结果。")

        dpi = round(root.winfo_fpixels("1i"))
        self.scale = dpi / 96
        root.tk.call("tk", "scaling", dpi / 72)
        px = lambda value: round(value * self.scale)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name, root=root).configure(family="Microsoft YaHei UI", size=11)
        tkfont.nametofont("TkHeadingFont", root=root).configure(weight="bold")

        root.title("fit2road · 骑行文件转换")
        width = min(px(1080), root.winfo_screenwidth() - px(64))
        height = min(px(720), root.winfo_screenheight() - px(96))
        root.geometry("{}x{}".format(width, height))
        root.minsize(min(px(820), width), min(px(560), height))
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure(".", font="TkDefaultFont")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Treeview", rowheight=px(34), font="TkDefaultFont")
        style.configure("Treeview.Heading", font="TkHeadingFont")
        style.configure("TButton", padding=(px(10), px(5)))

        main = ttk.Frame(root, padding=px(24))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        ttk.Label(main, text="FIT → ROAD", style="Title.TLabel").grid(sticky="w")
        ttk.Label(main, text="将室内 / 虚拟骑行分类转换为公路骑行，保留原始传感器数据。",
                  padding=(0, px(8), 0, px(18))).grid(row=1, sticky="w")

        toolbar = ttk.Frame(main)
        toolbar.grid(row=2, sticky="ew", pady=(0, px(10)))
        self.add_button = ttk.Button(toolbar, text="添加 FIT 文件…", command=self.choose_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="移除选中", command=self.remove_selected)
        self.remove_button.pack(side="left", padx=px(8))
        self.clear_button = ttk.Button(toolbar, text="清空列表", command=self.clear)
        self.clear_button.pack(side="left")

        table = ttk.Frame(main)
        table.grid(row=3, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("name", "folder", "status"),
                                 show="headings", selectmode="extended")
        for column, title, width in (("name", "文件名", 260), ("folder", "所在目录", 460),
                                     ("status", "转换结果", 240)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=px(width), minwidth=px(120))
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.tag_configure("error", foreground="#b42318")
        self.tree.tag_configure("success", foreground="#167348")
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)

        detail_label = ttk.Label(main, textvariable=self.detail, wraplength=px(980))
        detail_label.grid(row=4, sticky="ew", pady=(px(8), px(14)))
        main.bind("<Configure>", lambda event: detail_label.configure(
            wraplength=max(px(300), event.width - px(48))))

        output = ttk.LabelFrame(main, text="保存位置", padding=px(10))
        output.grid(row=5, sticky="ew")
        output.columnconfigure(0, weight=1)
        self.output_label = tk.StringVar(root, "默认：保存在各原文件旁")
        ttk.Entry(output, textvariable=self.output_label, state="readonly").grid(
            row=0, column=0, sticky="ew", padx=(0, px(8)))
        self.folder_button = ttk.Button(output, text="选择目录…", command=self.choose_folder)
        self.folder_button.grid(row=0, column=1)
        self.reset_button = ttk.Button(output, text="恢复默认", command=self.reset_folder)
        self.reset_button.grid(row=0, column=2, padx=(px(8), 0))
        ttk.Label(main, text="输出名：原文件名_road.fit；重名时自动编号。原文件保留。",
                  padding=(0, px(8), 0, px(12))).grid(row=6, sticky="w")

        actions = ttk.Frame(main)
        actions.grid(row=7, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(actions, mode="determinate", length=px(160))
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, px(16)))
        self.open_button = ttk.Button(actions, text="打开输出目录", command=self.open_folder,
                                      state="disabled")
        self.open_button.grid(row=0, column=1, padx=(0, px(8)))
        self.convert_button = ttk.Button(actions, text="一键转换", command=self.start,
                                         state="disabled")
        self.convert_button.grid(row=0, column=2)
        ttk.Label(main, textvariable=self.status, padding=(0, px(10), 0, 0)).grid(row=8, sticky="w")
        self.edit_buttons = (self.add_button, self.remove_button, self.clear_button,
                             self.folder_button, self.reset_button)

    def choose_files(self):
        paths = filedialog.askopenfilenames(parent=self.root, title="选择 FIT 骑行文件（支持多选）",
                                           filetypes=[("FIT 文件", "*.fit *.FIT")])
        self.add_files(paths)

    def add_files(self, paths):
        if self.busy:
            return
        existing = set(self.files.values())
        for path in paths:
            source = Path(path).resolve()
            if source in existing:
                continue
            item = self.tree.insert("", "end", values=(source.name, str(source.parent), "待转换"))
            self.files[item] = source
            self.details[item] = str(source)
            existing.add(source)
        self.refresh()

    def remove_selected(self):
        if self.busy:
            return
        for item in self.tree.selection():
            self.tree.delete(item)
            self.files.pop(item, None)
            self.outputs.pop(item, None)
            self.details.pop(item, None)
        self.refresh()

    def clear(self):
        if self.busy:
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.files.clear()
        self.outputs.clear()
        self.details.clear()
        self.progress["value"] = 0
        self.refresh()

    def refresh(self):
        self.status.set("已添加 {} 个文件，点击「一键转换」开始。".format(len(self.files))
                        if self.files else "请先添加需要转换的 FIT 文件。")
        self.convert_button.configure(state="normal" if self.files else "disabled")
        self.open_button.configure(state="normal" if self.outputs else "disabled")
        self.show_detail()

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self.root, title="选择输出目录")
        if folder:
            self.output_dir.set(folder)
            self.output_label.set(folder)

    def reset_folder(self):
        self.output_dir.set("")
        self.output_label.set("默认：保存在各原文件旁")

    def show_detail(self, event=None):
        selected = self.tree.selection()
        self.detail.set(self.details.get(selected[0], "") if selected
                        else "选择文件行可查看完整路径与处理结果。")

    def start(self):
        if self.busy or not self.files:
            return
        self.busy = True
        self.successes = self.failures = 0
        self.outputs.clear()
        for item, source in self.files.items():
            self.tree.set(item, "status", "等待中")
            self.tree.item(item, tags=())
            self.details[item] = str(source)
        for button in (*self.edit_buttons, self.convert_button, self.open_button):
            button.configure(state="disabled")
        self.progress.configure(maximum=len(self.files), value=0)
        self.status.set("正在转换 0 / {}，请稍候…".format(len(self.files)))
        self.show_detail()
        threading.Thread(target=convert_batch,
                         args=(list(self.files.items()), self.output_dir.get() or None, self.events),
                         daemon=True).start()
        self.root.after(60, self.poll)

    def poll(self):
        while True:
            try:
                kind, item, result = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self.busy = False
                for button in (*self.edit_buttons, self.convert_button):
                    button.configure(state="normal")
                self.open_button.configure(state="normal" if self.outputs else "disabled")
                self.status.set("处理完成：成功 {} 个，失败 {} 个。{}".format(
                    self.successes, self.failures,
                    "选中失败行可查看原因。" if self.failures else "可打开输出目录查看文件。"))
                return
            if kind == "processing":
                self.tree.set(item, "status", "转换中…")
                continue
            if kind == "success":
                self.successes += 1
                self.outputs[item] = result.destination
                self.tree.set(item, "status", "转换成功" if result.changed_fields else "已是 Road，已复制")
                self.tree.item(item, tags=("success",))
                self.details[item] = "输出：{}".format(result.destination)
            else:
                self.failures += 1
                self.tree.set(item, "status", "失败（选中查看原因）")
                self.tree.item(item, tags=("error",))
                self.details[item] = "{}\n失败原因：{}".format(self.files[item], result)
            completed = self.successes + self.failures
            self.progress["value"] = completed
            self.status.set("正在转换 {} / {}…".format(completed, len(self.files)))
            self.show_detail()
        if self.busy:
            self.root.after(60, self.poll)

    def open_folder(self):
        selected = self.tree.selection()
        destination = self.outputs.get(selected[0]) if selected else None
        if destination is None:
            destination = next(iter(self.outputs.values()), None)
        if destination is not None:
            try:
                os.startfile(str(destination.parent))
            except OSError as error:
                messagebox.showerror("无法打开目录", str(error), parent=self.root)

    def close(self):
        if self.busy:
            self.status.set("转换正在进行，请等待处理完成后关闭窗口。")
            return
        self.root.destroy()


def main():
    enable_high_dpi()
    root = tk.Tk()
    Fit2RoadApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
