"""
html_renderer.py

Renders the analysis into an HTML x-ray report.

Version: 1.5 (content streams: dark-blue text, UTF-16 NUL cleanup)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import html
import base64
import re
from utils.colour_maps import gen_class_for_generation
from parser_config import CONFIG as DEFAULT_CONFIG
from cmap_parser import GLOBAL_CMAP_MAPPING



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _html_header(title: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html><head><meta charset='utf-8'>\n"
        f"<title>{html.escape(title)}</title>\n"
        "<style>"
        "body{font-family:Consolas,monospace;font-size:12px;}"
        ".obj{border:1px solid #ccc;margin:4px 0;padding:4px;}"
        ".hdr{background:#eee;padding:2px 4px;font-weight:bold;}"
        ".gen0{color:#000;}"
        ".gen_odd{color:#003399;}"
        ".gen_high{color:#990000;}"
        ".warning{color:#b30000;font-weight:bold;margin:2px 0;}"
        ".xref-summary{border:1px solid #ccc;padding:4px;margin:6px 0;background:#f9f9f9;}"
        ".xref-summary h3{margin:0 0 4px 0;font-size:13px;}"
        ".xref-summary ul{margin:0 0 0 16px;padding:0;}"
        ".xref-summary li{margin:0;}"
        ".image_preview{margin-top:4px;}"
        "details{margin-top:2px;margin-bottom:2px;}"
        "summary{cursor:pointer;}"
        "</style>\n"
        "</head><body>\n"
    )


def _html_footer() -> str:
    return "</body></html>\n"


def _stream_type_label(stream_info: Dict[str, Any]) -> str:
    stype = (stream_info or {}).get("stream_type") or "unknown"
    if stype in (
        "image",
        "cmap",
        "font_program",
        "xref",
        "generic",
        "icc_profile",
        "xmp",
        "content",   # <- page content / generic contents stream
    ):
        return stype
    return "unknown"


def _annotate_operators(decoded_text: str) -> str:
    """
    Kept for possible future use, but no longer rendered as a separate
    "Decoded Stream (with operator hints)" section.

    Left here so that if you ever want the operator-hints view back, you can
    re-enable it without having to re-port the logic.
    """
    # Placeholder: return text unchanged.
    return decoded_text


def _decode_hex_text_to_unicode(hex_payload: str) -> str:
    """
    Decode a PDF <...> hex string into readable text.

    Non-breaking strategy:

      * First try the existing 16-bit CID / ToUnicode-CMap path.
      * If that does not look plausible, also try a byte-oriented decode
        (two hex digits per byte) and prefer it when it clearly produces
        readable Latin text.

    This preserves multilingual CID/CMap decoding, while also handling
    simple single-byte hex strings such as:
        <57415245484F5553453A>  ->  WAREHOUSE:
    instead of misreading them as CJK code points.
    """
    if not hex_payload:
        return ""

    def _decode_as_cids(payload: str) -> str:
        chars: List[str] = []
        for i in range(0, len(payload), 4):
            chunk = payload[i:i+4]
            if len(chunk) < 4:
                continue
            try:
                cid = int(chunk, 16)
            except ValueError:
                chars.append("?")
                continue
            if cid == 0:
                continue
            mapped = GLOBAL_CMAP_MAPPING.get(cid)
            if mapped is not None:
                chars.append(mapped)
            else:
                if cid < 32:
                    continue
                try:
                    chars.append(chr(cid))
                except ValueError:
                    chars.append("?")
        return "".join(chars)

    def _decode_as_bytes(payload: str) -> str:
        if len(payload) % 2 == 1:
            payload = payload[:-1]
        try:
            data = bytes.fromhex(payload)
        except ValueError:
            return ""
        try:
            return data.decode("latin-1", errors="ignore")
        except Exception:
            return ""

    def _readability_score(s: str) -> tuple[int, int, int]:
        if not s:
            return (0, 0, 0)
        printable = sum(1 for ch in s if ch.isprintable() and ch not in "\x0b\x0c")
        asciiish = sum(1 for ch in s if 32 <= ord(ch) <= 126)
        weird = sum(1 for ch in s if ord(ch) > 0x024F and ch.isprintable())
        return (printable, asciiish, -weird)

    cid_text = _decode_as_cids(hex_payload)

    # Prefer CID/CMap path when it obviously engaged (e.g. UTF-16/CMap-style
    # strings usually contain 00xx code units or actual mapped values).
    if len(hex_payload) % 4 == 0 and ("00" in hex_payload[:8] or any(int(hex_payload[i:i+4],16) in GLOBAL_CMAP_MAPPING for i in range(0, len(hex_payload)-3, 4))):
        return cid_text

    byte_text = _decode_as_bytes(hex_payload)

    # Choose the more readable decoding. This keeps multilingual CID decoding
    # intact, but lets plain ASCII/WinAnsi byte strings render as English.
    if _readability_score(byte_text) > _readability_score(cid_text):
        return byte_text

    return cid_text or byte_text


def _decode_pdf_literal_bytes(inner: str) -> bytes:
    """
    Decode the *contents* of a PDF literal string into raw bytes.

    Handles the standard PDF literal-string escape rules:

      \\  \\(  \\)   -> literal byte
      \n \r \t \b \f -> control bytes
      \\ddd          -> octal byte
      backslash + line break -> line continuation (ignored)

    The input `inner` is expected to be a latin-1-decoded view of the raw PDF
    bytes between the outer parentheses.
    """
    out = bytearray()
    i = 0
    n = len(inner)

    while i < n:
        ch = inner[i]
        if ch != "\\":
            out.append(ord(ch) & 0xFF)
            i += 1
            continue

        i += 1
        if i >= n:
            out.append(0x5C)
            break

        esc = inner[i]

        # Line continuation
        if esc == "\r":
            i += 1
            if i < n and inner[i] == "\n":
                i += 1
            continue
        if esc == "\n":
            i += 1
            continue

        mapping = {
            "n": 0x0A,
            "r": 0x0D,
            "t": 0x09,
            "b": 0x08,
            "f": 0x0C,
            "(": 0x28,
            ")": 0x29,
            "\\": 0x5C,
        }
        if esc in mapping:
            out.append(mapping[esc])
            i += 1
            continue

        # Octal escape: up to 3 octal digits
        if esc in "01234567":
            digits = [esc]
            i += 1
            for _ in range(2):
                if i < n and inner[i] in "01234567":
                    digits.append(inner[i])
                    i += 1
                else:
                    break
            out.append(int("".join(digits), 8) & 0xFF)
            continue

        # Unknown escape -> keep escaped character literally
        out.append(ord(esc) & 0xFF)
        i += 1

    return bytes(out)


def _decode_cid_bytes_literal(inner: str) -> str:
    """
    Decode the contents of a PDF literal string into examiner-friendly text.

    Key fix:
      decode PDF literal-string escape syntax *before* interpreting the bytes
      as UTF-16BE/CIDs. This prevents escaped bytes such as ``\\)``, ``\\(``,
      ``\\\\`` and octal escapes from being misread as extra text.

    That was the root cause of artefacts such as:
      * doubled trailing "y"
      * chunks like ``y)HHV...`` embedded in otherwise-correct text
    """
    from cmap_parser import GLOBAL_CMAP_MAPPING  # local import keeps behaviour stable

    raw = _decode_pdf_literal_bytes(inner)
    if not raw:
        return ""

    looks_utf16ish = (
        b"\x00" in raw
        or (
            len(raw) >= 4
            and len(raw) % 2 == 0
            and any(
                int.from_bytes(raw[i:i+2], "big") in GLOBAL_CMAP_MAPPING
                for i in range(0, len(raw) - 1, 2)
            )
        )
    )

    if not looks_utf16ish:
        return raw.decode("latin-1", errors="ignore")

    chars: List[str] = []
    i = 0
    n = len(raw)

    while i < n:
        if i + 1 < n:
            cid = int.from_bytes(raw[i:i+2], "big")
            i += 2
        else:
            cid = raw[i]
            i += 1

        if cid == 0:
            continue

        mapped = GLOBAL_CMAP_MAPPING.get(cid)
        if mapped is not None:
            chars.append(mapped)
            continue

        if cid < 32:
            continue
        try:
            chars.append(chr(cid))
        except ValueError:
            chars.append("?")

    return "".join(chars)


def _rewrite_utf16ish_literals_with_cmap(s: str) -> str:
    """
    Rewrite literal strings (...) inside a content stream so that any
    UTF-16-ish byte sequences are mapped via the ToUnicode CMap.

    Only the inner text of (...) is transformed; all operators and
    other syntax remain unchanged.
    """
    out: List[str] = []
    buf: List[str] = []
    in_paren = False

    for ch in s:
        if ch == "(" and not in_paren:
            in_paren = True
            out.append(ch)
            buf = []
            continue

        if ch == ")" and in_paren:
            inner = "".join(buf)
            out.append(_decode_cid_bytes_literal(inner))
            out.append(")")
            in_paren = False
            buf = []
            continue

        if in_paren:
            buf.append(ch)
        else:
            out.append(ch)

    # Unterminated literal – just append as-is.
    if in_paren and buf:
        out.append("".join(buf))

    return "".join(out)



def _rewrite_pdf_literal_strings(s: str) -> str:
    """
    Rewrite PDF literal strings (...) in-place, decoding PDF escape syntax
    robustly while preserving the surrounding content-stream operators.
    """
    out: List[str] = []
    buf: List[str] = []
    in_paren = False
    i = 0
    n = len(s)

    while i < n:
        ch = s[i]

        if not in_paren:
            if ch == "(":
                in_paren = True
                buf = []
                out.append(ch)
            else:
                out.append(ch)
            i += 1
            continue

        # Inside a literal string: keep escape pairs together in the buffer so
        # the decoder sees the original PDF literal syntax.
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(s[i + 1])
            i += 2
            continue

        if ch == ")":
            out.append(_decode_cid_bytes_literal("".join(buf)))
            out.append(")")
            in_paren = False
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    if in_paren and buf:
        out.append("".join(buf))

    return "".join(out)


def _format_decoded_stream_for_html(decoded_txt: str, stream_type: str) -> str:
    """Post-process decoded stream text for HTML.

    For page-content streams we try to make the text more examiner-friendly by:

      * Stripping embedded NUL characters that typically appear when UTF-16BE
        text is shown via a latin-1 decode (e.g. "\x00T\x00h\x00a...").
      * Highlighting literal-text payloads inside "(...)" (the strings that
        Tj / TJ operators show) in a dark blue colour so the actual words pop
        out from the drawing operators.
      * Additionally, detecting hex-encoded text strings of the form
        "<0024004F...>" used with Tj/TJ and rendering the decoded characters
        in red, so altered/coloured text stands out (e.g. 53 0 obj).

    We detect "content-like" streams either from the explicit stream_type
    ("content") or heuristically by looking for BT/ET text objects together
    with Tj/TJ operators in the decoded text.

    For all other streams we simply HTML-escape the decoded text.
    """

    s = decoded_txt or ""

    # Heuristic: treat as page content if either explicitly labelled, or if the
    # decoded text clearly contains text objects and text-show operators.
    is_content_like = (
        stream_type == "content"
        or (
            "BT" in s
            and "ET" in s
            and ("Tj" in s or "TJ" in s)
        )
    )


    if not is_content_like:
        # Non-content-like streams: just escape verbatim.
        return html.escape(s)

    # ------------------------------------------------------------------
    # Content-like streams
    # ------------------------------------------------------------------

    # If there are NUL bytes, try to interpret UTF-16-ish literal strings
    # via the ToUnicode CMap first, then strip any leftover NULs.
    s = _rewrite_pdf_literal_strings(s)
    if "\x00" in s:
        s = s.replace("\x00", "")

    # Decode PDF literal-string escapes (\), \(, \), octal escapes, etc.
    # after any UTF-16/CMap rewrite so escaped delimiters are not misread as
    # visible text.
    s = _rewrite_pdf_literal_strings(s)


    # Escape for HTML first; after this, "<" becomes "&lt;", etc.
    escaped = html.escape(s)

    # 1) Highlight literal text in parentheses in dark blue (existing behaviour)
    def _paren_repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.strip() == "":
            return "(" + inner + ")"
        return (
            "(<span style='color:#003399;font-weight:bold'>"
            + inner +
            "</span>)"
        )

    highlighted = re.sub(r"\(([^()\n]{1,400})\)", _paren_repl, escaped)

    # 2) Decode hex text of the form &lt;0024004F...&gt; Tj/TJ and render it in red.
    #
    # NOTE: by this point, the original "<" and ">" are "&lt;" and "&gt;".
    # We capture the hex payload and the trailing operator (" Tj" or " TJ").
    # IMPORTANT FIX: allow both Tj and TJ (case for 'j' was the bug).
    hex_pattern = re.compile(r"&lt;([0-9A-Fa-f]{4,})&gt;(\s*T[jJ]\b)")

    def _hex_repl(m: re.Match[str]) -> str:
        hex_payload = m.group(1)
        op_suffix = m.group(2)  # e.g. " Tj" or " TJ"

        decoded_chars = _decode_hex_text_to_unicode(hex_payload)
        if not decoded_chars:
            # If we fail to decode, leave the original intact.
            return m.group(0)

        # Show original hex plus decoded, with the decoded text in red.
        return (
            "&lt;" + hex_payload + "&gt; "
            "<span style='color:#c00000;font-weight:bold'>"
            + html.escape(decoded_chars) +
            "</span>"
            + op_suffix
        )

    highlighted = hex_pattern.sub(_hex_repl, highlighted)

    return highlighted



def _extract_text_stream(decoded_txt: str, stream_type: str) -> str:
    """Build a human-readable "Text Stream" view for content-like streams.

    This keeps the *layout feel* (newlines driven by Td/T*/TD) while decoding
    text operands so they're examiner-friendly.

    It supports:
      - hex strings <...> via GLOBAL_CMAP_MAPPING (ToUnicode mapping)
      - literal strings (...) via the same CID->Unicode helper used elsewhere

    It is intentionally lightweight (not full PDF text reflow).
    """
    s = decoded_txt or ""

    # Content-like heuristic (matches _format_decoded_stream_for_html intent)
    is_content_like = (
        stream_type == "content"
        or ("BT" in s and "ET" in s and ("Tj" in s or "TJ" in s))
    )
    if not is_content_like:
        return ""

    # Cleanup UTF-16-ish NULs similarly to the decoded-stream formatter
    if "\x00" in s:
        s = _rewrite_utf16ish_literals_with_cmap(s)
        if "\x00" in s:
            s = s.replace("\x00", "")

    out: list[str] = []

    td_re = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+Td\b")
    tstar_re = re.compile(r"\bT\*\b")
    td_cap_re = re.compile(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+TD\b")

    def _decode_hex_payload(hex_s: str) -> str:
        try:
            return _decode_hex_text_to_unicode(hex_s)
        except Exception:
            return "?"

    def _decode_lit_payload(lit: str) -> str:
        try:
            return _decode_cid_bytes_literal(lit)
        except Exception:
            return lit

    def _extract_from_tj_array(arr: str) -> str:
        parts: list[str] = []

        # literal (...) chunks
        for m in re.finditer(r"\(((?:\\.|[^\\)])*)\)", arr):
            parts.append(_decode_lit_payload(m.group(1)))

        # hex <...> chunks
        for m in re.finditer(r"<([0-9A-Fa-f]+)>", arr):
            parts.append(_decode_hex_payload(m.group(1)))

        return "".join(parts)

    for line in s.splitlines():
        # Hard-break on T*
        if tstar_re.search(line):
            out.append("")

        # Hard-break when moving downwards (dy negative) via Td/TD
        m_td = td_re.search(line)
        if m_td:
            try:
                dy = float(m_td.group(2))
                if dy < 0:
                    out.append("")
            except Exception:
                pass

        m_td2 = td_cap_re.search(line)
        if m_td2:
            try:
                dy = float(m_td2.group(2))
                if dy < 0:
                    out.append("")
            except Exception:
                pass

        # TJ: [ ... ] TJ
        m_tj = re.search(r"\[(.*)\]\s*TJ\b", line)
        if m_tj:
            txt = _extract_from_tj_array(m_tj.group(1))
            if txt.strip():
                out.append(txt)
            continue

        # Tj: (...) Tj
        m_tj_lit = re.search(r"\(((?:\\.|[^\\)])*)\)\s*Tj\b", line)
        if m_tj_lit:
            txt = _decode_lit_payload(m_tj_lit.group(1))
            if txt.strip():
                out.append(txt)
            continue

        # Tj: <...> Tj
        m_tj_hex = re.search(r"<([0-9A-Fa-f]+)>\s*Tj\b", line)
        if m_tj_hex:
            txt = _decode_hex_payload(m_tj_hex.group(1))
            if txt.strip():
                out.append(txt)
            continue

        # ' and " operators: show then newline
        m_quote = re.search(r"\((.*?)\)\s*'\b", line)
        if m_quote:
            out.append(_decode_lit_payload(m_quote.group(1)))
            out.append("")
            continue

        m_dquote = re.search(r"\(((?:\\.|[^\\)])*)\)\s*\"\b", line)
        if m_dquote:
            out.append(_decode_lit_payload(m_dquote.group(1)))
            out.append("")
            continue

    text = "\n".join(out)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text


def _summarise(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("<h2>Summary</h2>")
    lines.append("<ul>")
    if summary.get("object_count") is not None:
        lines.append(f"<li>Total objects: {summary['object_count']}</li>")
    if summary.get("image_stream_count") is not None:
        lines.append(f"<li>Image streams: {summary['image_stream_count']}</li>")
    if summary.get("incremental_update_count") is not None:
        lines.append(
            f"<li>Incremental updates (approx): "
            f"{summary['incremental_update_count']}</li>"
        )

    # Optional xref / trailer summary
    xref_summary = summary.get("xref_summary") or {}
    if xref_summary:
        lines.append("<li>Cross-reference / trailer overview:</li>")
        lines.append("<ul>")
        for k, v in xref_summary.items():
            lines.append(f"<li>{html.escape(str(k))}: {html.escape(str(v))}</li>")
        lines.append("</ul>")

    lines.append("</ul>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_html(
    analysis: List[Dict[str, Any]],
    summary: Dict[str, Any],
    pdf_path: str,
    out_path: str,
    config: Optional[Dict[str, Any]] = None,
) -> None:

    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    collapse_objects = cfg.get("collapse_objects_by_default", True)
    show_decoded = cfg.get("show_decoded_stream", True)
    # NOTE: We keep the config value but no longer render the operator-hints section.
    show_annotated = cfg.get("show_operator_annotated_stream", False)
    show_image_previews = cfg.get("show_image_previews", True)
    max_preview = int(cfg.get("max_stream_preview_bytes", 8192) or 0)

    title = f"PDF x-ray: {pdf_path}"

    lines: List[str] = []
    lines.append(_html_header(title))

    # --- NEW: PDF X-Ray header block (matches older xray style) ---
    app_version = cfg.get("app_version") or DEFAULT_CONFIG.get("app_version") or "unknown"
    lines.append("<h1>PDF X-Ray</h1>")
    lines.append(f"<p><b>Version:</b> {html.escape(str(app_version))}</p>")
    lines.append(f"<p><b>File:</b> {html.escape(pdf_path)}</p>")
    # ---------------------------------------------------------------

    # Summary
    lines.append(_summarise(summary))


    # File header / pre-object bytes (if any)
    prefix_text = summary.get("prefix_text")
    if prefix_text:
        lines.append("<h2>File header / pre-object bytes</h2>")
        if collapse_objects:
            lines.append("<details open>")
            lines.append("<summary>Bytes before first indirect object</summary>")
        lines.append("<pre class='stream'>")
        lines.append(html.escape(str(prefix_text)))
        lines.append("</pre>")
        if collapse_objects:
            lines.append("</details>")

    file_tail_text = summary.get("file_tail_text") or ""

    # ----------------------------------------------------------------------
    # Objects
    # ----------------------------------------------------------------------
    for rec_index, rec in enumerate(analysis):
        obj_num = rec.get("obj_num")
        gen_num = rec.get("gen_num")
        raw_obj = rec.get("raw_object_text") or ""
        stream_info = rec.get("stream_info") or {}
        warnings = rec.get("warnings") or []

        type_label = _stream_type_label(stream_info)
        gen_class = gen_class_for_generation(gen_num)

        lines.append("<div class='obj'>")

        # Header
        hdr = f"{obj_num} {gen_num} obj" if obj_num is not None else "Object"
        lines.append(
            f"<div class='hdr {gen_class}'>" +
            html.escape(hdr) +
            f" &nbsp;[type: {html.escape(type_label)}]</div>"
        )

        # Warnings
        for w in warnings:
            lines.append(
                "<div class='warning'>Warning: " +
                html.escape(str(w)) +
                "</div>"
            )

        # Raw object
        if collapse_objects:
            lines.append("<details open>")
            lines.append("<summary>Raw Object</summary>")
        lines.append("<pre class='stream'>")
        raw_to_show = raw_obj
        if file_tail_text and rec_index == (len(analysis) - 1):
            raw_to_show = (raw_obj.rstrip() + "\n\n" + str(file_tail_text).lstrip("\r\n"))
        lines.append(html.escape(raw_to_show))
        lines.append("</pre>")
        if collapse_objects:
            lines.append("</details>")

        # Decoded stream
        decoded_bytes = stream_info.get("decoded_bytes")
        if show_decoded and decoded_bytes is not None:
            display = decoded_bytes
            truncated = False
            if max_preview > 0 and len(display) > max_preview:
                display = display[:max_preview]
                truncated = True

            try:
                decoded_txt = display.decode("latin-1", errors="replace")
            except Exception:
                decoded_txt = "<binary data>"

            # For page-content streams, post-process the decoded text so that
            # Unicode text becomes more readable (strip UTF-16 NULs) and the
            # literal strings used by Tj/TJ operators stand out in dark blue.
            stream_type = (stream_info or {}).get("stream_type") or ""
            formatted_txt = _format_decoded_stream_for_html(decoded_txt, stream_type)

            if collapse_objects:
                lines.append("<details open>")
                lines.append("<summary>Decoded Stream</summary>")

            lines.append("<pre class='stream'>")
            lines.append(formatted_txt)
            if truncated:
                lines.append(
                    html.escape(
                        f"\n\n[truncated at {max_preview} bytes for HTML preview]"
                    )
                )
            lines.append("</pre>")

            if collapse_objects:
                lines.append("</details>")

            # --------------------------------------------------------------
            # NEW: Text Stream (human-readable extraction for text content)
            # --------------------------------------------------------------
            show_text_stream = cfg.get("show_text_stream", True)
            if show_text_stream:
                extracted = _extract_text_stream(decoded_txt, stream_type)
                if extracted:
                    if collapse_objects:
                        lines.append("<details open>")
                        lines.append("<summary>Text Stream</summary>")

                    lines.append("<pre class='stream'>")
                    lines.append(html.escape(extracted))
                    lines.append("</pre>")

                    if collapse_objects:
                        lines.append("</details>")

            # NOTE:
            # The former "Decoded Stream (with operator hints)" section has been
            # removed from the HTML output by request. If you ever want it back,
            # you can reintroduce it here using _annotate_operators(decoded_txt).

        # IMAGE PREVIEW
        if show_image_previews and stream_info.get("stream_type") == "image":
            aux = stream_info.get("aux_info") or {}
            img = aux.get("image") or {}

            # Prefer PNG preview bytes
            png_bytes = img.get("png_bytes")
            mime = "image/png"
            raw_bytes = png_bytes

            # Fallback: JPEG or other
            if raw_bytes is None:
                raw_bytes = img.get("image_bytes")
                mime = img.get("image_mime") or "image/jpeg"

            if raw_bytes:
                b64 = base64.b64encode(raw_bytes).decode("ascii", errors="ignore")
                lines.append("<details class='image_preview' open>")
                lines.append("<summary><b>Image Preview</b></summary>")
                lines.append(
                    f"<img src='data:{mime};base64,{b64}' "
                    f"alt='Image preview for {obj_num} {gen_num} obj' "
                    "style='max-width:600px;max-height:600px;border:1px solid #aaa;margin-top:4px;' />"
                )
                lines.append("</details>")

        lines.append("</div>")  # end .obj


    # ------------------------------------------------------------------
    # Parsed trailer dictionaries
    # ------------------------------------------------------------------
    trailers = summary.get("parsed_trailers") or []
    if trailers:
        lines.append("<h2>Parsed trailer dictionaries</h2>")
        for i, t in enumerate(trailers, start=1):
            if collapse_objects:
                lines.append("<details open>")
                lines.append(f"<summary>Trailer #{i}</summary>")
            else:
                lines.append(f"<h4>Trailer #{i}</h4>")

            lines.append("<pre class='stream'>")
            lines.append(html.escape(str(t)))
            lines.append("</pre>")

            if collapse_objects:
                lines.append("</details>")

    # End of document
    lines.append(_html_footer())

    html_text = "\n".join(lines)

    with open(out_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(html_text)
