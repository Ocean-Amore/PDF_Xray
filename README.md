# PDF X-Ray

**PDF X-Ray** is a standalone Python utility for inspecting the internal structure of PDF files. It reads a PDF at byte/object level, extracts indirect objects, attempts to decode supported streams, summarises cross-reference and trailer information, and produces an examiner-friendly HTML report.

The tool is intended for the anlysis of born-digital or scanned PDF files where the examiner needs to understand how the file is built, what objects it contains, and whether there are structural irregularities that warrant closer examination.

---

## 1. What PDF X-Ray Does

PDF X-Ray processes a PDF and produces an **x-ray style report** showing the internal objects and decoded stream content where possible.

At a high level, it:

1. Loads the PDF as raw bytes.
2. Extracts PDF indirect objects such as `1 0 obj`, `2 0 obj`, and so on.
3. Identifies and decodes supported stream filters.
4. Parses cross-reference and trailer information using lightweight heuristics.
5. Analyses object generation numbers and incremental-update indicators.
6. Classifies common stream types, including content streams, image streams, CMaps, embedded font programs, XRef streams, ICC profiles, and XMP metadata.
7. Generates an HTML report, and optionally a plain-text report.

The default output is an HTML file named:

```text
<original_file_name>_xray.html
```

If plain-text export is enabled in `parser_config.py`, the tool also creates:

```text
<original_file_name>_xray.txt
```
Note: images are not rendered in the .txt file. The .html output is used to visualise images in object streams.
---

## 2. What PDF X-Ray Is Used For

PDF X-Ray is used to assist with the forensic examination of PDF documents by making internal PDF structures easier to review.

Typical uses include:

- Reviewing PDF object structure.
- Inspecting raw and decoded PDF streams.
- Identifying image objects and previewing extracted image streams where supported.
- Reviewing page content streams containing text and drawing operators.
- Examining embedded font programs and subset font indicators.
- Reviewing ToUnicode CMaps and character mapping behaviour.
- Identifying XMP metadata streams.
- Reviewing ICC colour profile streams.
- Summarising cross-reference table and cross-reference stream layout.
- Highlighting approximate incremental-update indicators.
- Flagging non-zero or unusual generation number patterns.
- Assisting triage of possible editing, object replacement, object insertion, redaction, or reconstruction artefacts.

PDF X-Ray is an **inspection and triage aid**. It does not, by itself, prove that a PDF has been altered. Findings should be interpreted by a trained examiner and considered alongside the document history, metadata, visual examination, file hashes, source information, and any other forensic results.

---

## 3. Key Features

### Object-Level PDF Review

PDF X-Ray extracts indirect PDF objects and displays each object in the report. For each object, the report can show:

- object number;
- generation number;
- raw object text;
- decoded stream output, where available;
- stream type classification; and
- heuristic warnings.

### Stream Decoding

The current stream decoding logic supports or recognises the following PDF filter paths:

| Filter | Current behaviour |
|---|---|
| `/FlateDecode` | Decoded using Python `zlib`. |
| `/ASCIIHexDecode` | Basic ASCII hex decoding. |
| `/RunLengthDecode` | Implemented for PDF run-length encoded streams. |
| `/ASCII85Decode` | Present as a placeholder/pass-through in the current codebase. |
| `/LZWDecode` | Present as a placeholder/pass-through in the current codebase. |

The decoder can also resolve simple indirect filter references such as:

```text
/Filter 6 0 R
```

where the referenced object contains a filter array such as:

```text
[/FlateDecode /RunLengthDecode]
```

### HTML Report Output

The HTML report is designed for examiner review. It includes:

- a PDF X-Ray header with version information;
- a summary section;
- file header/pre-object bytes, where available;
- each parsed object;
- collapsible raw object sections;
- collapsible decoded stream sections;
- generation-number colour coding;
- warnings for selected unusual patterns;
- image previews where the image stream can be rendered; and
- highlighted text-like content in page content streams.

### TXT Report Output

Plain-text output can be enabled through `parser_config.py`.

This is useful where an examiner wants a simpler report for:

- Notepad++ review;
- searching text content;
- preserving a plain-text extraction record; or
- comparing decoded stream output between files.

### Batch Processing

PDF X-Ray can process a single PDF file or a folder containing multiple PDF files. In folder mode, it processes each `.pdf` file in the selected folder and logs errors without stopping the entire batch run.

### Tkinter File Selector

If the program is launched without a file or folder path, it opens a small Tkinter selector that allows the user to choose either:

- a single PDF file; or
- a folder of PDFs for batch processing.

---

## 4. Project Layout

The expected project structure is:

```text
pdf_xray_project/
│
├── main.py
├── parser_config.py
├── pdf_reader.py
├── stream_decoder.py
├── image_decoder.py
├── cmap_parser.py
├── font_decoder.py
├── xref_parser.py
├── object_analyser.py
├── reporting.py
│
├── html_renderer.py
├── txt_renderer.py
│
└── utils/
    ├── __init__.py
    ├── filters.py
    ├── ascii_hexdump.py
    ├── colour_maps.py
    └── warnings_engine.py
```

All files should remain in this structure so that module imports work correctly.

---

## 5. Python Version Required

### Recommended Python Version

PDF X-Ray is recommended for:

```
Python 3.13, 64-bit
```

### Minimum Python Version

The code uses modern Python syntax and type annotations. The practical minimum version is:

```
Python 3.10+
```

Python 3.13 is preferred for consistency with the current development environment.

---

## 6. Dependencies

PDF X-Ray is designed to run using the Python standard library only.

### Required Standard Library Modules

The project uses standard Python modules including:

- `argparse`
- `base64`
- `binascii`
- `datetime`
- `html`
- `os`
- `re`
- `struct`
- `tkinter`
- `typing`
- `zlib`

### Third-Party Python Packages

No third-party Python packages are required for the current standalone version.

That means there is currently no required `pip install` step.

### Optional System Requirements

- A modern web browser to open the generated HTML report.
- Tkinter support if using the graphical file/folder selector. Tkinter is normally included with the official Windows Python installer.

---

## 7. Installation

### Windows Installation

1. Install **Python 3.13 64-bit**.

   During installation, tick:

   Add python.exe to PATH

2. Create a folder for PDF X-Ray, for example:

   C:\pdf_xray
   

3. Copy the PDF X-Ray project files into that folder, preserving the `utils` subfolder.

4. Open Command Prompt or PowerShell in the project folder.

5. Confirm Python is available:

   ```powershell
   python --version
   ```

   or:

   ```powershell
   py -3.13 --version
   ```

6. Optional: create a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```

7. No external Python packages are required. The tool can be run directly.

---

## 8. How to Run PDF X-Ray

### Option A — Run Against a Single PDF

From the PDF X-Ray project folder:

```powershell
python main.py "C:\Path\To\Document.pdf"
```

or, using the Python launcher:

```powershell
py -3.13 main.py "C:\Path\To\Document.pdf"
```

The report will be written beside the original file, for example:

C:\Path\To\Document_xray.html

### Option B — Run Against a Folder of PDFs

```powershell
python main.py "C:\Path\To\Folder"
```

PDF X-Ray will process each `.pdf` file in that folder.

### Option C — Launch the File/Folder Selector

Run the tool without arguments:

```powershell
python main.py
```

A small PDF X-Ray window will open and allow selection of either a PDF file or a folder.

---

## 9. Configuration

Main runtime options are stored in:

```text
parser_config.py
```

Current configuration options include:

| Setting | Purpose |
|---|---|
| `app_version` | Application version shown in the HTML report and About dialog. |
| `generate_html` | Creates the HTML report when set to `True`. |
| `generate_txt` | Creates the TXT report when set to `True`. |
| `show_decoded_stream` | Shows decoded stream content in the HTML report. |
| `show_image_previews` | Shows image previews where supported. |
| `warn_strange_generations` | Enables generation-number warnings. |
| `color_code_generations` | Enables generation-number colour coding. |
| `summarise_incremental_updates` | Enables approximate incremental-update summary behaviour. |
| `collapse_objects_by_default` | Wraps object sections in collapsible HTML sections. |
| `details_default_open` | Controls whether collapsible sections open by default. |
| `max_stream_preview_bytes` | Limits decoded stream preview length; `None` means no truncation. |
| `html_suffix` | Suffix used for HTML report filenames. |
| `txt_suffix` | Suffix used for TXT report filenames. |

Example:

```python
CONFIG = {
    "app_version": "0.0.14",
    "generate_html": True,
    "generate_txt": False,
    "show_decoded_stream": True,
    "show_image_previews": True,
    "warn_strange_generations": True,
    "color_code_generations": True,
    "summarise_incremental_updates": True,
    "collapse_objects_by_default": True,
    "details_default_open": False,
    "max_stream_preview_bytes": None,
    "html_suffix": "_xray.html",
    "txt_suffix": "_xray.txt",
}
```

---

## 10. Output Interpretation

The PDF X-Ray report should be used as a technical inspection aid.

### Useful Indicators to Review

When reviewing an output report, consider checking:

- multiple xref sections;
- xref streams and classic xref tables;
- trailer dictionaries;
- `/Prev` references;
- non-zero generation numbers;
- unusually high generation numbers;
- object numbers appearing in multiple generations;
- page content streams containing unexpected text operations;
- image streams that visually differ from expected page content;
- embedded subset fonts;
- ToUnicode CMap mappings;
- metadata and XMP content;
- ICC profile information;
- object streams and compressed object structures;
- unusual filter chains; and
- mismatch between visible document content and embedded resources.

### Incremental Updates

PDF X-Ray provides an approximate incremental-update count based on xref/trailer layout. Incremental updates can occur during legitimate PDF editing, saving, signing, optimisation, annotation, or form-filling workflows. They are not automatically evidence of fraud.

### Generation Numbers

Non-zero or high generation numbers may indicate object reuse, incremental saving, or complex editing history. They should be reviewed in context rather than treated as a standalone conclusion.

### CMaps and Fonts

ToUnicode CMaps and embedded font subsets can be highly relevant in PDF examinations because they affect how encoded glyphs map to readable text. Differences between displayed glyphs, extracted text, and embedded mappings may warrant closer review.

---

## 11. Forensic Use Notes

When using PDF X-Ray in a forensic workflow:

1. Work from a forensic copy, not the original source file.
2. Record the original file hash before analysis.
3. Preserve the generated PDF X-Ray report with the case record where appropriate.
4. Treat PDF X-Ray findings as technical observations.
5. Correlate structural findings with visual examination, metadata review, file provenance, and other validated tools.
6. Document any limitations encountered during examination.
7. Validate the tool and its outputs before relying on it in formal casework.

Suggested record wording:

```text
PDF X-Ray was used as a technical inspection aid to review PDF object structure, decoded streams, cross-reference/trailer information, embedded resources, and heuristic indicators of incremental updates or unusual object generation patterns. The output was interpreted in conjunction with other examination results and was not treated as a standalone determination of authenticity.
```

---

## 12. Known Limitations

PDF X-Ray is intentionally lightweight and examiner-oriented. It is not a complete PDF specification validator.

Known limitations include:

- Object extraction is heuristic and may not fully parse every malformed, encrypted, hybrid-reference, or heavily optimised PDF.
- Some filters are not fully implemented in the current codebase.
- `/ASCII85Decode` and `/LZWDecode` currently exist as placeholder/pass-through functions.
- CMap interpretation is best-effort and may not reconstruct all text accurately.
- Image preview generation is best-effort and may not support every colour space, compression type, bit depth, or predictor combination.
- Font program parsing is lightweight and intended to summarise embedded font data, not fully validate font files.
- Incremental-update counts are approximate and based on structural heuristics.
- A clean report does not prove that a PDF is authentic.
- A flagged report does not prove that a PDF is fraudulent.

---

## 13. Troubleshooting

### No TXT report is created

Check `parser_config.py`:

```python
"generate_txt": False,
```

Change it to:

```python
"generate_txt": True,
```

### No GUI appears

The GUI uses Tkinter. Confirm Tkinter is available:

```powershell
python -m tkinter
```

If Tkinter is missing, reinstall Python using the official Windows installer and ensure Tcl/Tk support is included.

### The HTML report is very large

Large PDFs with many objects, images, or decoded streams can generate large HTML reports.

To limit preview size, set a byte limit in `parser_config.py`:

```python
"max_stream_preview_bytes": 8192,
```

### A batch run fails on one PDF

Folder processing is designed to log an error for a problematic PDF and continue processing the remaining PDFs. Review the console output for lines beginning with:

```text
[ERR]
```

---

## 14. Suggested Validation Before Casework

Before operational use, validate PDF X-Ray against a controlled set of test files, including:

- simple born-digital PDFs;
- scanned-image PDFs;
- PDFs with known metadata;
- PDFs with known incremental updates;
- PDFs edited in Adobe Acrobat;
- PDFs edited in non-Adobe tools;
- PDFs containing embedded fonts and ToUnicode CMaps;
- PDFs containing image masks;
- PDFs containing xref streams;
- malformed or deliberately unusual PDFs; and
- known negative controls.

For each test file, compare PDF X-Ray output against expected observations and at least one independent tool where practical.

---

## 15. Basic Command Summary

```powershell
# Run against one PDF
python main.py "C:\Path\To\Document.pdf"

# Run against a folder of PDFs
python main.py "C:\Path\To\Folder"

# Launch GUI selector
python main.py

# Show Python version
python --version

# Test Tkinter
python -m tkinter
```


```
