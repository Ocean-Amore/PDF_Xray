# main.py
# Entry point for the PDF analysis and rendering application.
# Handles loading a PDF, analysis, HTML/TXT rendering, GUI selection and batch mode.
#
# Version: 1.5.0-renderhtml-hard-safe
# Patch purpose:
#   * preserve existing public behaviour
#   * remove the default Windows worker-subprocess path that can leave a
#     pre-written placeholder HTML when the child process stalls before flushing
#   * purge stale __pycache__ before importing project modules
#   * write stage progress into the placeholder HTML file so future failures
#     identify the exact stage rather than leaving only "Processing started"
#   * keep optional batch subprocess mode available by environment variable

from __future__ import annotations

import argparse
import html
import os
import shutil
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Optional

MAIN_PATCH_VERSION = "1.5.0-renderhtml-hard-safe"

# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------
# On Windows, repeated drop-in replacements can leave stale bytecode beside the
# modules.  Purge the local __pycache__ before importing project modules so the
# current .py files are used.  This is intentionally early in the file.
sys.dont_write_bytecode = True
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if _MODULE_DIR and _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)
try:
    _pycache = os.path.join(_MODULE_DIR, "__pycache__")
    if os.path.isdir(_pycache):
        shutil.rmtree(_pycache, ignore_errors=True)
except Exception:
    pass

from parser_config import CONFIG
from pdf_reader import load_pdf_bytes, extract_objects
from xref_parser import parse_xref_and_trailers
from object_analyser import analyse_objects
import html_renderer as _html_renderer_module
from html_renderer import render_html
from txt_renderer import render_txt
from reporting import build_summary


def _log(message: str) -> None:
    print(message, flush=True)


def _html_output_path_for(pdf_path: str) -> str:
    base, _ext = os.path.splitext(pdf_path)
    return base + CONFIG.get("html_suffix", "_xray.html")


def _txt_output_path_for(pdf_path: str) -> str:
    base, _ext = os.path.splitext(pdf_path)
    return base + CONFIG.get("txt_suffix", "_xray.txt")


def _append_html_status(html_path: Optional[str], status: str) -> None:
    """Append a stage marker to the diagnostic placeholder HTML.

    The renderer will later replace the whole file with the final report.  If a
    future stall occurs, this status trail remains in the placeholder and shows
    exactly which stage stopped.
    """
    if not html_path:
        return
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        # Insert before </body> if present; otherwise append safely.
        line = f"<p><b>{html.escape(stamp)}:</b> {html.escape(status)}</p>\n"
        if os.path.exists(html_path):
            try:
                with open(html_path, "r", encoding="utf-8", errors="replace") as f:
                    existing = f.read()
                marker = "</body>"
                if marker in existing:
                    existing = existing.replace(marker, line + marker, 1)
                    with open(html_path, "w", encoding="utf-8", errors="replace") as f:
                        f.write(existing)
                    return
            except Exception:
                pass
        with open(html_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(line)
    except Exception:
        pass


def _prewrite_started_html(pdf_path: str, html_path: str) -> None:
    """Create the expected HTML file before analysis begins."""
    try:
        parent = os.path.dirname(os.path.abspath(html_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(html_path, "w", encoding="utf-8", errors="replace") as f:
            f.write("<!DOCTYPE html>\n<html><head><meta charset='utf-8'>")
            f.write("<title>PDF X-Ray processing started</title></head><body>\n")
            f.write("<h1>PDF X-Ray</h1>\n")
            f.write("<h2>Processing started</h2>\n")
            f.write(f"<p><b>File:</b> {html.escape(str(pdf_path))}</p>\n")
            f.write(f"<p><b>Main patch:</b> {html.escape(MAIN_PATCH_VERSION)}</p>\n")
            f.write(f"<p><b>Started:</b> {html.escape(now)}</p>\n")
            f.write("<p>If this message remains, processing stopped before the final HTML render completed.</p>\n")
            f.write("</body></html>\n")
    except Exception as e:
        _log(f"[WARN] Could not pre-create diagnostic HTML file: {e!r}")


def _stage(label: str, func: Callable[[], Any], html_path: Optional[str] = None) -> Any:
    started = time.perf_counter()
    _log(f"[INFO]   {label} - started")
    _append_html_status(html_path, f"{label} - started")
    try:
        result = func()
    except Exception:
        elapsed = time.perf_counter() - started
        _log(f"[ERR]   {label} - failed after {elapsed:.2f}s")
        _append_html_status(html_path, f"{label} - failed after {elapsed:.2f}s")
        raise
    elapsed = time.perf_counter() - started
    _log(f"[INFO]   {label} - completed in {elapsed:.2f}s")
    _append_html_status(html_path, f"{label} - completed in {elapsed:.2f}s")
    return result


def run(pdf_path: str) -> None:
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    html_path: Optional[str] = None
    if CONFIG.get("generate_html", True):
        html_path = _html_output_path_for(pdf_path)
        _prewrite_started_html(pdf_path, html_path)

    _log(f"[INFO] Running PDF X-Ray main patch: {MAIN_PATCH_VERSION}")
    try:
        _renderer_version = getattr(_html_renderer_module, "PDFXRAY_HTML_RENDERER_PATCH_VERSION", "unknown")
    except Exception:
        _renderer_version = "unknown"
    _log(f"[INFO] HTML renderer patch: {_renderer_version}")
    _log(f"[INFO] Input PDF: {pdf_path}")
    _append_html_status(html_path, f"run() entered using main patch {MAIN_PATCH_VERSION}")

    raw_bytes = _stage("load_pdf_bytes", lambda: load_pdf_bytes(pdf_path), html_path)
    objects = _stage("extract_objects", lambda: extract_objects(raw_bytes), html_path)
    xref_info = _stage("parse_xref_and_trailers", lambda: parse_xref_and_trailers(raw_bytes, objects), html_path)
    analysis = _stage("analyse_objects", lambda: analyse_objects(objects, xref_info, CONFIG), html_path)
    summary = _stage("build_summary", lambda: build_summary(analysis, xref_info, CONFIG), html_path)

    if CONFIG.get("generate_html", True):
        assert html_path is not None
        _stage("render_html", lambda: render_html(analysis, summary, pdf_path, html_path, CONFIG), html_path)
        _log(f"[OK] HTML x-ray written to: {html_path}")

    if CONFIG.get("generate_txt", True):
        txt_path = _txt_output_path_for(pdf_path)
        _stage("render_txt", lambda: render_txt(analysis, summary, pdf_path, txt_path, CONFIG), html_path)
        _log(f"[OK] TXT x-ray written to: {txt_path}")


def _run_pdf_subprocess(pdf_path: str, timeout_seconds: int) -> int:
    """Run one PDF in a worker subprocess for optional batch isolation."""
    cmd = [sys.executable, "-B", "-u", os.path.abspath(__file__), "--_pdfxray_worker", pdf_path]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(cmd, timeout=timeout_seconds, cwd=_MODULE_DIR or None, env=env)
        return int(completed.returncode or 0)
    except subprocess.TimeoutExpired:
        _log(f"[ERR] Timed out after {timeout_seconds}s while processing {pdf_path}")
        return 124
    except Exception as e:
        _log(f"[ERR] Could not start worker for {pdf_path}: {e!r}")
        return 125


# ---------------------------------------------------------------------------
# Helper to process all PDFs in a folder (batch mode)
# ---------------------------------------------------------------------------
def run_folder(folder_path: str) -> None:
    if not os.path.isdir(folder_path):
        raise NotADirectoryError(f"Not a directory: {folder_path}")

    _log(f"[INFO] Batch processing folder: {folder_path}")

    # IMPORTANT CHANGE IN 1.4.0:
    # Default to in-process batch execution.  The earlier default worker
    # subprocess could leave only the pre-written placeholder HTML on some
    # Windows/VS Code runs.  Optional subprocess isolation remains available by
    # setting PDFXRAY_USE_BATCH_SUBPROCESS=1.
    use_subprocess = os.environ.get("PDFXRAY_USE_BATCH_SUBPROCESS", "0") == "1"
    try:
        timeout_seconds = int(os.environ.get("PDFXRAY_PDF_TIMEOUT_SECONDS", "180"))
    except Exception:
        timeout_seconds = 180

    for name in sorted(os.listdir(folder_path)):
        full = os.path.join(folder_path, name)
        if not os.path.isfile(full):
            continue
        if not name.lower().endswith(".pdf"):
            continue

        _log(f"[INFO] Processing PDF: {full}")
        try:
            if use_subprocess:
                rc = _run_pdf_subprocess(full, timeout_seconds)
                if rc != 0:
                    _log(f"[ERR] Failed on {full}: worker exit code {rc}")
            else:
                run(full)
        except Exception as e:  # pragma: no cover (runtime safety)
            _log(f"[ERR] Failed on {full}: {e!r}")
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Tkinter selector – choose single file or folder for batch
# ---------------------------------------------------------------------------
def _run_tkinter_selector() -> None:
    """Open a small Tkinter dialog to choose single file or folder."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("PDF X-Ray - Select input")
    root.geometry("420x140")
    root.resizable(False, False)

    menubar = tk.Menu(root)

    def show_about() -> None:
        app_version = CONFIG.get("app_version", "unspecified")
        msg = (
            "PDF X-Ray\n"
            f"Version: {app_version}\n"
            f"Main patch: {MAIN_PATCH_VERSION}\n"
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
        folder_path = filedialog.askdirectory(title="Select folder containing PDF files")
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
    parser = argparse.ArgumentParser(description="PDF x-ray parser (standalone project skeleton).")
    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="Path to a PDF file or a folder containing PDFs. If omitted, a Tkinter dialog will open.",
    )
    parser.add_argument(
        "--_pdfxray_worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


if __name__ == "__main__":
    arg_parser = _build_arg_parser()
    args = arg_parser.parse_args()

    if args._pdfxray_worker:
        if not args.pdf_path:
            raise SystemExit("Worker mode requires a PDF path.")
        run(args.pdf_path)
    elif args.pdf_path:
        if os.path.isdir(args.pdf_path):
            run_folder(args.pdf_path)
        else:
            run(args.pdf_path)
    else:
        _run_tkinter_selector()
