#!/usr/bin/env python3
"""Tkinter desktop interface for the draw.io XML to Mermaid converter."""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Sequence

from xml_to_mermaid import (
    MERMAID_CURVES,
    VALID_DIRECTIONS,
    ConversionError,
    diagram_to_mermaid,
    parse_drawio_xml,
)


PAGE_LAYOUT_LABELS = {
    "A4 tự động": "adaptive",
    "A4 dọc": "portrait",
    "A4 ngang": "landscape",
    "Flowchart tự do": "flowchart",
}


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class GeneratedMermaid:
    source: Path
    diagram_name: str
    output: Path
    code: str
    warnings: tuple[str, ...] = ()


def source_stem(path: Path) -> str:
    """Return a friendly output stem, removing compound draw.io suffixes."""
    name = path.name
    lowered = name.lower()
    for suffix in (".drawio.xml", ".drawio", ".xml"):
        if lowered.endswith(suffix):
            return name[: -len(suffix)] or "diagram"
    return path.stem or "diagram"


def safe_filename(value: str) -> str:
    """Sanitize a diagram/page name for Windows and other common filesystems."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = "diagram"
    if value.upper() in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"
    return value[:120].rstrip(" .") or "diagram"


def available_output_path(path: Path, reserved: set[Path], overwrite: bool) -> Path:
    """Avoid accidentally replacing an existing or same-batch output file."""
    normalized_reserved = {item.resolve() for item in reserved}
    if overwrite and path.resolve() not in normalized_reserved:
        return path
    if not path.exists() and path.resolve() not in normalized_reserved:
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists() and candidate.resolve() not in normalized_reserved:
            return candidate
        index += 1


def convert_inputs(
    input_paths: Sequence[Path],
    output_dir: Path,
    *,
    direction: str = "auto",
    curve: str = "rounded",
    node_spacing: int = 60,
    rank_spacing: int = 80,
    infer_distance: float = 60.0,
    overwrite: bool = False,
    page_layout: str = "adaptive",
) -> tuple[list[GeneratedMermaid], list[str]]:
    """Convert all selected inputs and write one .mmd file per diagram page."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[GeneratedMermaid] = []
    errors: list[str] = []
    reserved: set[Path] = set()

    for input_path in input_paths:
        try:
            diagrams = parse_drawio_xml(input_path)
            for diagram in diagrams:
                code = diagram_to_mermaid(
                    diagram,
                    direction=direction,
                    infer_distance=infer_distance,
                    infer_text_labels=True,
                    curve=curve,
                    node_spacing=node_spacing,
                    rank_spacing=rank_spacing,
                    page_layout=page_layout,
                )
                stem = safe_filename(source_stem(input_path))
                if len(diagrams) > 1:
                    stem += "__" + safe_filename(diagram.name)
                destination = available_output_path(output_dir / f"{stem}.mmd", reserved, overwrite)
                destination.write_text(code, encoding="utf-8")
                reserved.add(destination)
                generated.append(
                    GeneratedMermaid(
                        source=input_path,
                        diagram_name=diagram.name,
                        output=destination,
                        code=code,
                        warnings=tuple(diagram.warnings),
                    )
                )
        except (ConversionError, OSError, ValueError) as exc:
            errors.append(f"{input_path.name}: {exc}")
    return generated, errors


class XmlToMermaidApp(tk.Tk):
    POLL_MS = 100

    def __init__(self) -> None:
        super().__init__()
        self.title("XML draw.io → Mermaid")
        self.geometry("1120x760")
        self.minsize(860, 620)

        self.input_paths: list[Path] = []
        self.results: list[GeneratedMermaid] = []
        self.worker_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.output_dir_var = tk.StringVar()
        self.direction_var = tk.StringVar(value="auto")
        self.page_layout_var = tk.StringVar(value="A4 tự động")
        self.curve_var = tk.StringVar(value="rounded")
        self.node_spacing_var = tk.IntVar(value=60)
        self.rank_spacing_var = tk.IntVar(value=80)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Chọn một hoặc nhiều file XML để bắt đầu.")

        self._configure_style()
        self._build_ui()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#52606d")
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8))

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Chuyển XML draw.io sang Mermaid", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            container,
            text="Chọn nhiều file, xem kết quả, copy code và lưu tự động thành file .mmd.",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 12))

        source_frame = ttk.LabelFrame(container, text="1. File đầu vào", padding=10)
        source_frame.pack(fill=tk.X)
        list_area = ttk.Frame(source_frame)
        list_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_list = tk.Listbox(list_area, height=5, selectmode=tk.EXTENDED, activestyle="none")
        file_scroll = ttk.Scrollbar(list_area, orient=tk.VERTICAL, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=file_scroll.set)
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        file_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        source_buttons = ttk.Frame(source_frame)
        source_buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        ttk.Button(source_buttons, text="Chọn file...", command=self.choose_files).pack(fill=tk.X)
        ttk.Button(source_buttons, text="Xóa đã chọn", command=self.remove_selected_files).pack(
            fill=tk.X, pady=6
        )
        ttk.Button(source_buttons, text="Xóa tất cả", command=self.clear_files).pack(fill=tk.X)

        settings = ttk.LabelFrame(container, text="2. Thư mục và tùy chọn", padding=10)
        settings.pack(fill=tk.X, pady=10)
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="Thư mục đầu ra:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        ttk.Entry(settings, textvariable=self.output_dir_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Button(settings, text="Chọn thư mục...", command=self.choose_output_dir).grid(
            row=0, column=2, padx=(8, 0)
        )

        option_row = ttk.Frame(settings)
        option_row.grid(row=1, column=0, columnspan=3, sticky=tk.EW, pady=(10, 0))
        ttk.Label(option_row, text="Trang:").pack(side=tk.LEFT)
        self.page_layout_combo = ttk.Combobox(
            option_row,
            textvariable=self.page_layout_var,
            values=tuple(PAGE_LAYOUT_LABELS),
            state="readonly",
            width=11,
        )
        self.page_layout_combo.pack(side=tk.LEFT, padx=(5, 14))
        self.page_layout_combo.bind("<<ComboboxSelected>>", self._sync_layout_controls)
        ttk.Label(option_row, text="Hướng:").pack(side=tk.LEFT)
        self.direction_combo = ttk.Combobox(
            option_row,
            textvariable=self.direction_var,
            values=VALID_DIRECTIONS,
            state="readonly",
            width=7,
        )
        self.direction_combo.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Label(option_row, text="Đường nối:").pack(side=tk.LEFT)
        self.curve_combo = ttk.Combobox(
            option_row,
            textvariable=self.curve_var,
            values=MERMAID_CURVES,
            state="readonly",
            width=13,
        )
        self.curve_combo.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Label(option_row, text="Cách node:").pack(side=tk.LEFT)
        self.node_spacing_spin = ttk.Spinbox(
            option_row, from_=0, to=500, textvariable=self.node_spacing_var, width=6
        )
        self.node_spacing_spin.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Label(option_row, text="Cách cấp:").pack(side=tk.LEFT)
        self.rank_spacing_spin = ttk.Spinbox(
            option_row, from_=0, to=500, textvariable=self.rank_spacing_var, width=6
        )
        self.rank_spacing_spin.pack(side=tk.LEFT, padx=(5, 14))
        ttk.Checkbutton(option_row, text="Ghi đè file trùng tên", variable=self.overwrite_var).pack(
            side=tk.LEFT
        )

        action_row = ttk.Frame(container)
        action_row.pack(fill=tk.X, pady=(0, 10))
        self.convert_button = ttk.Button(
            action_row, text="Chuyển sang Mermaid", style="Action.TButton", command=self.start_conversion
        )
        self.convert_button.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(action_row, mode="indeterminate", length=180)
        self.progress.pack(side=tk.LEFT, padx=12)
        ttk.Label(action_row, textvariable=self.status_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        result_frame = ttk.LabelFrame(container, text="3. Kết quả", padding=8)
        result_frame.pack(fill=tk.BOTH, expand=True)
        pane = ttk.Panedwindow(result_frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        result_list_frame = ttk.Frame(pane)
        self.result_tree = ttk.Treeview(
            result_list_frame,
            columns=("page", "file"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.result_tree.heading("page", text="Diagram")
        self.result_tree.heading("file", text="File .mmd")
        self.result_tree.column("page", width=130, minwidth=90)
        self.result_tree.column("file", width=220, minwidth=140)
        result_scroll = ttk.Scrollbar(result_list_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=result_scroll.set)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_tree.bind("<<TreeviewSelect>>", self.show_selected_result)

        code_frame = ttk.Frame(pane)
        self.code_text = scrolledtext.ScrolledText(
            code_frame,
            wrap=tk.NONE,
            undo=False,
            font=("Consolas", 10),
            padx=8,
            pady=8,
        )
        self.code_text.pack(fill=tk.BOTH, expand=True)
        self.code_text.configure(state=tk.DISABLED)
        copy_row = ttk.Frame(code_frame)
        copy_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(copy_row, text="Copy code đang xem", command=self.copy_current).pack(side=tk.LEFT)
        ttk.Button(copy_row, text="Copy tất cả", command=self.copy_all).pack(side=tk.LEFT, padx=8)

        pane.add(result_list_frame, weight=1)
        pane.add(code_frame, weight=3)
        self._sync_layout_controls()

    def _sync_layout_controls(self, _event: tk.Event | None = None) -> None:
        is_flowchart = PAGE_LAYOUT_LABELS[self.page_layout_var.get()] == "flowchart"
        self.direction_combo.configure(state="readonly" if is_flowchart else tk.DISABLED)
        self.curve_combo.configure(state="readonly" if is_flowchart else tk.DISABLED)
        spin_state = tk.NORMAL if is_flowchart else tk.DISABLED
        self.node_spacing_spin.configure(state=spin_state)
        self.rank_spacing_spin.configure(state=spin_state)

    def choose_files(self) -> None:
        filenames = filedialog.askopenfilenames(
            parent=self,
            title="Chọn file XML draw.io",
            filetypes=(
                ("Draw.io XML", "*.xml *.drawio"),
                ("XML", "*.xml"),
                ("Draw.io", "*.drawio"),
                ("Tất cả file", "*.*"),
            ),
        )
        if not filenames:
            return
        existing = {path.resolve() for path in self.input_paths}
        for filename in filenames:
            path = Path(filename)
            if path.resolve() not in existing:
                self.input_paths.append(path)
                existing.add(path.resolve())
        if self.input_paths and not self.output_dir_var.get():
            self.output_dir_var.set(str(self.input_paths[0].parent / "mermaid_output"))
        self._refresh_file_list()
        self.status_var.set(f"Đã chọn {len(self.input_paths)} file.")

    def remove_selected_files(self) -> None:
        selected = set(self.file_list.curselection())
        self.input_paths = [path for index, path in enumerate(self.input_paths) if index not in selected]
        self._refresh_file_list()
        self.status_var.set(f"Còn {len(self.input_paths)} file được chọn.")

    def clear_files(self) -> None:
        self.input_paths.clear()
        self._refresh_file_list()
        self.status_var.set("Đã xóa danh sách file đầu vào.")

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, tk.END)
        for path in self.input_paths:
            self.file_list.insert(tk.END, str(path))

    def choose_output_dir(self) -> None:
        initial = self.output_dir_var.get() or (str(self.input_paths[0].parent) if self.input_paths else None)
        directory = filedialog.askdirectory(parent=self, title="Chọn thư mục lưu Mermaid", initialdir=initial)
        if directory:
            self.output_dir_var.set(directory)

    def start_conversion(self) -> None:
        if not self.input_paths:
            messagebox.showwarning("Thiếu file", "Hãy chọn ít nhất một file XML hoặc draw.io.", parent=self)
            return
        if not self.output_dir_var.get().strip():
            messagebox.showwarning("Thiếu thư mục", "Hãy chọn thư mục đầu ra.", parent=self)
            return
        try:
            node_spacing = int(self.node_spacing_var.get())
            rank_spacing = int(self.rank_spacing_var.get())
            if node_spacing < 0 or rank_spacing < 0:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Giá trị không hợp lệ", "Khoảng cách phải là số nguyên không âm.", parent=self)
            return

        self.convert_button.configure(state=tk.DISABLED)
        self.progress.start(12)
        self.status_var.set("Đang chuyển đổi...")
        settings = {
            "direction": self.direction_var.get(),
            "curve": self.curve_var.get(),
            "node_spacing": node_spacing,
            "rank_spacing": rank_spacing,
            "overwrite": self.overwrite_var.get(),
            "page_layout": PAGE_LAYOUT_LABELS[self.page_layout_var.get()],
        }
        thread = threading.Thread(
            target=self._conversion_worker,
            args=(tuple(self.input_paths), Path(self.output_dir_var.get().strip()), settings),
            daemon=True,
        )
        thread.start()
        self.after(self.POLL_MS, self._poll_worker)

    def _conversion_worker(self, inputs: tuple[Path, ...], output: Path, settings: dict[str, object]) -> None:
        try:
            result = convert_inputs(inputs, output, **settings)
            self.worker_queue.put(("done", result))
        except Exception as exc:  # Last-resort boundary: keep the GUI alive.
            self.worker_queue.put(("fatal", str(exc)))

    def _poll_worker(self) -> None:
        try:
            kind, payload = self.worker_queue.get_nowait()
        except queue.Empty:
            self.after(self.POLL_MS, self._poll_worker)
            return
        self.progress.stop()
        self.convert_button.configure(state=tk.NORMAL)
        if kind == "fatal":
            self.status_var.set("Chuyển đổi thất bại.")
            messagebox.showerror("Lỗi", str(payload), parent=self)
            return
        generated, errors = payload  # type: ignore[misc]
        self._show_results(generated)
        conversion_warnings = [
            f"{result.output.name}: {warning}"
            for result in generated
            for warning in result.warnings
        ]
        if errors or conversion_warnings:
            self.status_var.set(
                f"Đã tạo {len(generated)} file, có {len(errors) + len(conversion_warnings)} cảnh báo."
            )
            messagebox.showwarning(
                "Hoàn thành với cảnh báo",
                f"Đã tạo {len(generated)} file.\n\n"
                + "\n".join(errors + conversion_warnings),
                parent=self,
            )
        elif generated:
            self.status_var.set(f"Hoàn thành: đã tạo {len(generated)} file Mermaid.")
        else:
            self.status_var.set("Không có kết quả được tạo.")

    def _show_results(self, results: list[GeneratedMermaid]) -> None:
        self.results = results
        self.result_tree.delete(*self.result_tree.get_children())
        self._set_code("")
        for index, result in enumerate(results):
            self.result_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(result.diagram_name, result.output.name),
            )
        if results:
            self.result_tree.selection_set("0")
            self.result_tree.focus("0")
            self.result_tree.see("0")
            self._set_code(results[0].code)

    def show_selected_result(self, _event: tk.Event | None = None) -> None:
        selection = self.result_tree.selection()
        if selection:
            self._set_code(self.results[int(selection[0])].code)

    def _set_code(self, value: str) -> None:
        self.code_text.configure(state=tk.NORMAL)
        self.code_text.delete("1.0", tk.END)
        self.code_text.insert("1.0", value)
        self.code_text.configure(state=tk.DISABLED)

    def copy_current(self) -> None:
        selection = self.result_tree.selection()
        if not selection:
            messagebox.showinfo("Chưa có kết quả", "Chưa có code Mermaid để copy.", parent=self)
            return
        self._copy_to_clipboard(self.results[int(selection[0])].code)
        self.status_var.set("Đã copy code đang xem vào clipboard.")

    def copy_all(self) -> None:
        if not self.results:
            messagebox.showinfo("Chưa có kết quả", "Chưa có code Mermaid để copy.", parent=self)
            return
        combined = "\n\n".join(
            f"%% File: {result.output.name}\n{result.code.rstrip()}" for result in self.results
        ) + "\n"
        self._copy_to_clipboard(combined)
        self.status_var.set(f"Đã copy toàn bộ {len(self.results)} kết quả vào clipboard.")

    def _copy_to_clipboard(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update_idletasks()


def main() -> None:
    app = XmlToMermaidApp()
    app.mainloop()


if __name__ == "__main__":
    main()
