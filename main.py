# main.py
# Entry point for the PDF analysis and rendering application.
# Imports necessary modules and orchestrates the workflow.
# Handles:
# 1. loading a PDF
# 2. calling all submodules
# 3. generating HTML and/or TXT
# 4. handling toggles (image previews, txt export, etc.)

#Version: 1.2

"""
main.py

Entry point for the PDF x-ray parser project.
This is intentionally standalone so you can evolve it independently of your
existing PDF Stream Parser Vxx series.
"""

import argparse
import os

from parser_config import CONFIG
from pdf_reader import load_pdf_bytes, extract_objects
from xref_parser import parse_xref_and_trailers
from object_analyser import analyse_objects
from html_renderer import render_html
from txt_renderer import render_txt
from reporting import build_summary


def run(pdf_path: str) -> None:
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    raw_bytes = load_pdf_bytes(pdf_path)
    objects = extract_objects(raw_bytes)
    # OLD:
    # xref_info = parse_xref_and_trailers(raw_bytes)
    # NEW:
    xref_info = parse_xref_and_trailers(raw_bytes, objects)

    analysis = analyse_objects(objects, xref_info, CONFIG)
    summary = build_summary(analysis, xref_info, CONFIG)
    
    base, ext = os.path.splitext(pdf_path)
    if CONFIG.get("generate_html", True):
        html_path = base + CONFIG.get("html_suffix", "_xray.html")
        render_html(analysis, summary, pdf_path, html_path, CONFIG)
        print(f"[OK] HTML x-ray written to: {html_path}")

    if CONFIG.get("generate_txt", True):
        txt_path = base + CONFIG.get("txt_suffix", "_xray.txt")
        render_txt(analysis, summary, pdf_path, txt_path, CONFIG)
        print(f"[OK] TXT x-ray written to: {txt_path}")


# ---------------------------------------------------------------------------
# NEW: helper to process all PDFs in a folder (batch mode)
# ---------------------------------------------------------------------------
def run_folder(folder_path: str) -> None:
    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"Not a directory: {folder_path}")

    print(f"[INFO] Batch processing folder: {folder_path}")
    for name in sorted(os.listdir(folder_path)):
        full = os.path.join(folder_path, name)
        if not os.path.isfile(full):
            continue
        if not name.lower().endswith(".pdf"):
            continue

        print(f"[INFO] Processing PDF: {full}")
        try:
            run(full)
        except Exception as e:  # pragma: no cover (runtime safety)
            print(f"[ERR] Failed on {full}: {e!r}")


# ---------------------------------------------------------------------------
# NEW: Tkinter selector – choose single file or folder for batch
# ---------------------------------------------------------------------------
def _run_tkinter_selector() -> None:
    """Open a small Tkinter dialog to choose single file or folder."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("PDF X-Ray - Select input")
    root.geometry("420x140")
    root.resizable(False, False)

    # NEW: About menu with version, creator and date
    menubar = tk.Menu(root)

    def show_about() -> None:
        app_version = CONFIG.get("app_version", "unspecified")
        msg = (
            "PDF X-Ray\n"
            f"Version: {app_version}\n"
            "Creator: Benjamin Kriss\n"
            "Date: 7th December 2025"
        )
        messagebox.showinfo("About PDF X-Ray", msg)

    about_menu = tk.Menu(menubar, tearoff=0)
    about_menu.add_command(label="About PDF X-Ray", command=show_about)
    menubar.add_cascade(label="About", menu=about_menu)
    root.config(menu=menubar)


    label = tk.Label(
        root,
        text="Select a single PDF file or a folder of PDFs for batch processing:",
        wraplength=380,
        justify="left",
    )
    label.pack(padx=10, pady=10)

    def choose_file() -> None:
        pdf_path = filedialog.askopenfilename(
            title="Select PDF file",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if pdf_path:
            root.destroy()
            run(pdf_path)

    def choose_folder() -> None:
        folder_path = filedialog.askdirectory(
            title="Select folder containing PDF files"
        )
        if folder_path:
            root.destroy()
            run_folder(folder_path)

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=5)

    file_btn = tk.Button(btn_frame, text="Select PDF file", width=18, command=choose_file)
    file_btn.pack(side="left", padx=5)

    folder_btn = tk.Button(btn_frame, text="Select folder", width=18, command=choose_folder)
    folder_btn.pack(side="left", padx=5)

    quit_btn = tk.Button(root, text="Quit", width=10, command=root.destroy)
    quit_btn.pack(pady=5)

    root.mainloop()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PDF x-ray parser (standalone project skeleton)."
    )
    # CHANGED: make pdf_path optional and allow folder as well.
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="Path to a PDF file or a folder containing PDFs. "
             "If omitted, a Tkinter dialog will open.",
    )
    return parser


if __name__ == "__main__":
    arg_parser = _build_arg_parser()
    args = arg_parser.parse_args()

    # If a path is provided, use CLI mode; otherwise show Tkinter selector.
    if args.pdf_path:
        if os.path.isdir(args.pdf_path):
            run_folder(args.pdf_path)
        else:
            run(args.pdf_path)
    else:
        _run_tkinter_selector()
