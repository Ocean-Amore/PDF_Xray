"""
html_renderer.py

Renders the analysis into an HTML x-ray report.

Version: 1.7.1 (safe tokenizer + fail-safe Text Stream guard)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
import html
import base64
import re
try:
    # Preferred package layout
    from utils.colour_maps import gen_class_for_generation
except Exception:  # pragma: no cover - flat module fallback for direct/drop-in use
    try:
        from colour_maps import gen_class_for_generation
    except Exception:  # pragma: no cover - last-resort fallback
        def gen_class_for_generation(gen_num):
            try:
                g = int(gen_num)
            except Exception:
                return 'gen0'
            if g == 0:
                return 'gen0'
            if g >= 10:
                return 'gen_high'
            return 'gen_odd'
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
        ".text_stream{background:#f8fbff;}"
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
    Decode a PDF <...> hex string into a Unicode string.

    Strategy (non-breaking):

      * Walk the payload in 4-hex-digit units (CID-sized).
      * For each CID:
          - If a ToUnicode CMap mapping exists in GLOBAL_CMAP_MAPPING,
            use that (preferred).
          - Otherwise, fall back to chr(codepoint) as before.
      * NULs and other control-like values with no mapping are skipped.

    This ensures that strings like <0024004F00570048005500480047> are
    decoded using the Calibri ToUnicode CMap as "Altered" instead of
    the raw UTF-16BE interpretation "$OWHUHG".
    """
    if not hex_payload:
        return ""

    chars: List[str] = []

    # Walk in 4-hex-digit chunks; ignore incomplete tail bytes.
    for i in range(0, len(hex_payload), 4):
        chunk = hex_payload[i:i+4]
        if len(chunk) < 2:
            # Too short to be meaningful; skip quietly.
            continue

        try:
            cid = int(chunk, 16)
        except ValueError:
            # If any chunk is bad, represent it visibly.
            chars.append("?")
            continue

        # Drop explicit NULs.
        if cid == 0:
            continue

        # Prefer ToUnicode CMap mapping if we have one.
        mapped = GLOBAL_CMAP_MAPPING.get(cid)

        if mapped is not None:
            # Mapped value can be one or more Unicode characters.
            chars.append(mapped)
        else:
            # No ToUnicode entry:
            #   * skip obvious control characters (non-printable)
            #   * otherwise fall back to the original "chr(cid)" behaviour.
            if cid < 32:
                # These are control-like and not useful visually.
                continue
            try:
                chars.append(chr(cid))
            except ValueError:
                chars.append("?")

    return "".join(chars)

def _decode_cid_bytes_literal(inner: str) -> str:
    """
    Decode the raw byte-like contents of a literal string (inner part of (...))
    into Unicode using the global ToUnicode CMap mapping where available.

    This is aimed at UTF-16-ish sequences such as:
        "\\x007\\x00X\\x00H..."  (decoded via latin-1)

    We reconstruct 16-bit big-endian CIDs from pairs (0x00, code)
    and then:

      * If GLOBAL_CMAP_MAPPING[cid] exists, use that (preferred).
      * Else, fall back to chr(cid) for printable values.

    If there are no NUL bytes, we return the inner text unchanged.
    """
    from cmap_parser import GLOBAL_CMAP_MAPPING  # already imported at top

    if "\x00" not in inner:
        # No UTF-16-style pattern; leave as-is.
        return inner

    bytes_vals = [ord(c) for c in inner]
    chars: List[str] = []
    i = 0

    while i < len(bytes_vals):
        b = bytes_vals[i]

        # Heuristic: 0x00 followed by another byte -> 16-bit big-endian CID
        if i + 1 < len(bytes_vals) and b == 0:
            cid = (bytes_vals[i] << 8) | bytes_vals[i + 1]
            i += 2
        else:
            cid = bytes_vals[i]
            i += 1

        if cid == 0:
            continue

        mapped = GLOBAL_CMAP_MAPPING.get(cid)

        if mapped is not None:
            # ToUnicode mapping wins (may be 1+ characters).
            chars.append(mapped)
        else:
            # Fallback: keep printable characters roughly as before.
            if cid < 32:
                continue  # skip control-like codes
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



# ---------------------------------------------------------------------------
# Text Stream extraction helpers
# ---------------------------------------------------------------------------
# The HTML "Decoded Stream" section intentionally preserves the PDF drawing
# operators.  The separate "Text Stream" section below is different: it extracts
# only text-showing operands and decodes the glyph codes through the active font's
# /ToUnicode CMap so the examiner sees text resembling the rendered PDF page.

_PDF_TEXT_SHOW_OPS = {"Tj", "TJ", "'", '"'}


def _extract_indirect_ref(text: str, key: str) -> Optional[Tuple[int, int]]:
    """Return the first indirect reference following a dictionary key."""
    m = re.search(r"/" + re.escape(key) + r"\s+(\d+)\s+(\d+)\s+R\b", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _iter_font_resource_blocks(raw: str) -> List[str]:
    """Return /Font resource dictionary bodies from a raw object string.

    This is intentionally conservative and lightweight.  It is not a full PDF
    dictionary parser, but it handles the common resource form:

        /Font << /TT2 7 0 R /TT4 9 0 R >>

    which is what the affected document uses.  Multiple resource dictionaries
    across the file are merged by alias below.
    """
    blocks: List[str] = []
    for m in re.finditer(r"/Font\s*<<", raw or ""):
        start = m.end()
        depth = 1
        i = start
        while i < len(raw) - 1:
            pair = raw[i:i + 2]
            if pair == "<<":
                depth += 1
                i += 2
                continue
            if pair == ">>":
                depth -= 1
                if depth == 0:
                    blocks.append(raw[start:i])
                    break
                i += 2
                continue
            i += 1
    return blocks


def _build_font_cmap_lookup(analysis: List[Dict[str, Any]]) -> Dict[str, Dict[int, str]]:
    """Build a map of page resource font alias -> ToUnicode mapping.

    Why this exists:
      PDF subset fonts commonly reuse the same byte/glyph codes for different
      characters.  A merged/global CMap can therefore be wrong.  For example,
      in the supplied invoice PDF, /TT2, /TT4 and /TT6 all use overlapping codes
      such as 0x21, but each code maps to a different character depending on the
      active font.  This resolver keeps those maps separate.
    """
    cmap_by_obj: Dict[int, Dict[int, str]] = {}
    font_obj_to_cmap_obj: Dict[int, int] = {}
    alias_to_font_obj: Dict[str, int] = {}

    for rec in analysis or []:
        obj_num = rec.get("obj_num")
        raw = rec.get("raw_object_text") or ""
        stream_info = rec.get("stream_info") or {}
        aux = stream_info.get("aux_info") or {}

        # CMap objects already parsed by stream_decoder/cmap_parser.
        cmap_info = aux.get("cmap") or {}
        mappings = cmap_info.get("mappings")
        if isinstance(obj_num, int) and isinstance(mappings, dict) and mappings:
            cmap_by_obj[obj_num] = mappings

        # Font dictionary -> /ToUnicode N 0 R.
        if isinstance(obj_num, int) and "/Type /Font" in raw and "/ToUnicode" in raw:
            ref = _extract_indirect_ref(raw, "ToUnicode")
            if ref:
                font_obj_to_cmap_obj[obj_num] = ref[0]

        # Resource dictionary aliases: /TT2 7 0 R, /F1 12 0 R, etc.
        for block in _iter_font_resource_blocks(raw):
            for am in re.finditer(r"/([A-Za-z0-9_.-]+)\s+(\d+)\s+(\d+)\s+R\b", block):
                alias_to_font_obj[am.group(1)] = int(am.group(2))

    alias_to_cmap: Dict[str, Dict[int, str]] = {}
    for alias, font_obj in alias_to_font_obj.items():
        cmap_obj = font_obj_to_cmap_obj.get(font_obj)
        cmap = cmap_by_obj.get(cmap_obj) if cmap_obj is not None else None
        if cmap:
            alias_to_cmap[alias] = cmap

    return alias_to_cmap


def _parse_pdf_literal_token(data: bytes, i: int) -> Tuple[Tuple[str, bytes], int]:
    """Parse a PDF literal string from data[i] == '(' into raw bytes."""
    i += 1
    out = bytearray()
    depth = 1

    while i < len(data) and depth:
        c = data[i]
        i += 1

        if c == 0x5C:  # backslash escape
            if i >= len(data):
                out.append(0x5C)
                break
            esc = data[i]
            i += 1

            escape_map = {
                ord("n"): 0x0A,
                ord("r"): 0x0D,
                ord("t"): 0x09,
                ord("b"): 0x08,
                ord("f"): 0x0C,
            }
            if esc in escape_map:
                out.append(escape_map[esc])
            elif esc in (0x28, 0x29, 0x5C):  # \( \) \\
                out.append(esc)
            elif esc == 0x0D:  # line continuation
                if i < len(data) and data[i] == 0x0A:
                    i += 1
            elif esc == 0x0A:  # line continuation
                pass
            elif 0x30 <= esc <= 0x37:  # octal escape
                digits = [esc]
                for _ in range(2):
                    if i < len(data) and 0x30 <= data[i] <= 0x37:
                        digits.append(data[i])
                        i += 1
                    else:
                        break
                out.append(int(bytes(digits), 8) & 0xFF)
            else:
                out.append(esc)
            continue

        if c == 0x28:  # nested '('
            depth += 1
            out.append(c)
            continue

        if c == 0x29:  # ')'
            depth -= 1
            if depth:
                out.append(c)
            continue

        out.append(c)

    return ("str", bytes(out)), i


def _parse_pdf_hex_token(data: bytes, i: int) -> Tuple[Tuple[str, bytes], int]:
    """Parse a PDF hex string from data[i] == '<' into raw bytes."""
    i += 1
    hex_chars: List[str] = []

    while i < len(data) and data[i] != 0x3E:  # '>'
        ch = chr(data[i])
        if not ch.isspace():
            hex_chars.append(ch)
        i += 1

    if len(hex_chars) % 2:
        hex_chars.append("0")

    try:
        raw = bytes.fromhex("".join(hex_chars))
    except ValueError:
        raw = b""

    if i < len(data) and data[i] == 0x3E:
        i += 1
    return ("hex", raw), i


def _tokenise_pdf_content(data: bytes):
    """Yield small PDF content-stream tokens needed for text extraction.

    Safety note:
        This function is deliberately conservative. Older versions could hang
        when a stream contained PDF delimiters such as ``<<`` or ``>>`` because
        the generic-token branch returned an empty token without advancing the
        byte pointer. That stopped HTML generation before the output file was
        created. This tokenizer always advances, treats unsupported delimiters
        as ignorable tokens, and places a hard ceiling on token count so a
        malformed stream cannot freeze batch processing.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return

    data = bytes(data)
    i = 0
    n = len(data)
    whitespace = b"\x00\x09\x0A\x0C\x0D\x20"
    delimiters = b"\x00\x09\x0A\x0C\x0D\x20()<>[]{}/%"
    max_tokens = max(10000, min(250000, n * 8 + 1000))
    token_count = 0

    while i < n:
        if token_count >= max_tokens:
            yield ("op", "__PDFXRAY_TOKEN_LIMIT__")
            return

        start_i = i
        c = data[i]

        if c in whitespace:
            i += 1
            continue

        if c == 0x25:  # % comment
            while i < n and data[i] not in (0x0A, 0x0D):
                i += 1
            continue

        if c == 0x28:  # literal string: (...)
            tok, i = _parse_pdf_literal_token(data, i)
            token_count += 1
            yield tok
            if i <= start_i:
                i = start_i + 1
            continue

        # Dictionaries are common in CMaps/Form/XObject resources and may occur
        # in streams that are not real page content. Text extraction does not
        # need dictionary delimiters, so consume them safely and move on.
        if c == 0x3C and i + 1 < n and data[i + 1] == 0x3C:  # <<
            i += 2
            token_count += 1
            yield ("dict_start", "<<")
            continue

        if c == 0x3E and i + 1 < n and data[i + 1] == 0x3E:  # >>
            i += 2
            token_count += 1
            yield ("dict_end", ">>")
            continue

        if c == 0x3C:  # hex string: <...>
            tok, i = _parse_pdf_hex_token(data, i)
            token_count += 1
            yield tok
            if i <= start_i:
                i = start_i + 1
            continue

        if c == 0x3E:  # stray >
            i += 1
            token_count += 1
            yield ("delimiter", ">")
            continue

        if c == 0x5B:  # [
            i += 1
            token_count += 1
            yield ("array_start", "[")
            continue

        if c == 0x5D:  # ]
            i += 1
            token_count += 1
            yield ("array_end", "]")
            continue

        if c in (0x7B, 0x7D):  # { }
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue

        if c == 0x2F:  # /name
            j = i + 1
            while j < n and data[j] not in delimiters:
                j += 1
            # Bare slash or delimiter after slash: consume slash to avoid stalling.
            if j == i + 1:
                i += 1
                token_count += 1
                yield ("delimiter", "/")
                continue
            token_count += 1
            yield ("name", data[i + 1:j].decode("latin-1", errors="replace"))
            i = j
            continue

        # Any other single delimiter that reached here is unsupported for text
        # extraction. Consume it safely instead of trying to parse a zero-length
        # operator.
        if c in delimiters:
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue

        j = i
        while j < n and data[j] not in delimiters:
            j += 1

        if j <= i:
            # Absolute last-resort progress guard.
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue

        raw = data[i:j].decode("latin-1", errors="replace")
        if raw:
            if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)", raw):
                value: Any = float(raw) if "." in raw else int(raw)
                token_count += 1
                yield ("num", value)
            else:
                token_count += 1
                yield ("op", raw)
        i = j


def _push_text_operand(stack: List[Tuple[str, Any]], tok: Tuple[str, Any]) -> None:
    """Push a token, collapsing [...] arrays into a single operand."""
    if tok[0] == "array_start":
        stack.append(("array_marker", None))
        return

    if tok[0] == "array_end":
        items: List[Tuple[str, Any]] = []
        while stack:
            item = stack.pop()
            if item[0] == "array_marker":
                break
            items.append(item)
        items.reverse()
        stack.append(("array", items))
        return

    stack.append(tok)


def _fallback_decode_text_bytes(raw: bytes) -> str:
    """Decode text bytes when no active /ToUnicode CMap is available."""
    if not raw:
        return ""

    if raw.startswith((b"\xfe\xff", b"\xff\xfe")):
        for enc in ("utf-16", "utf-16-be", "utf-16-le"):
            try:
                return raw.decode(enc, errors="replace").replace("\x00", "")
            except Exception:
                pass

    # UTF-16BE without BOM commonly appears as NUL-prefixed ASCII.
    if raw.count(b"\x00") >= max(1, len(raw) // 4) and len(raw) % 2 == 0:
        try:
            return raw.decode("utf-16-be", errors="replace").replace("\x00", "")
        except Exception:
            pass

    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc, errors="replace").replace("\x00", "")
        except Exception:
            pass
    return ""


def _decode_text_bytes_with_cmap(raw: bytes, cmap: Optional[Dict[int, str]]) -> str:
    """Decode PDF text bytes using the active font CMap where possible."""
    if not raw:
        return ""

    if cmap:
        # CID/UTF-16BE style: <00240025> or literal bytes containing NULs.
        if b"\x00" in raw and len(raw) % 2 == 0:
            chars: List[str] = []
            hits = 0
            for i in range(0, len(raw), 2):
                code = (raw[i] << 8) | raw[i + 1]
                mapped = cmap.get(code)
                if mapped is not None:
                    chars.append(mapped)
                    hits += 1
                elif code == 0:
                    continue
                elif 32 <= code <= 0x10FFFF:
                    try:
                        chars.append(chr(code))
                    except ValueError:
                        chars.append("?")
                else:
                    chars.append("?")
            if hits:
                return "".join(chars)

        # Single-byte subset font style.  This is the important fix for the
        # supplied HOME_AFFAIRS_AUTHENTICATE_PERPETUAL_004.xls.pdf file.
        chars = []
        hits = 0
        total = 0
        for byte in raw:
            total += 1
            mapped = cmap.get(byte)
            if mapped is not None:
                chars.append(mapped)
                hits += 1
            elif byte in (0x09, 0x0A, 0x0D):
                chars.append(chr(byte))
            elif 32 <= byte <= 126:
                chars.append(chr(byte))
            else:
                chars.append("?")

        # Use the single-byte CMap result when any meaningful amount mapped.
        # This avoids choosing a misleading UTF-16-looking interpretation such
        # as U+3B28 for bytes that are really subset-font glyph codes.
        if hits and hits / max(total, 1) >= 0.25:
            return "".join(chars)

        # Last CMap attempt: two-byte CIDs without NULs.
        if len(raw) % 2 == 0:
            chars = []
            hits = 0
            for i in range(0, len(raw), 2):
                code = (raw[i] << 8) | raw[i + 1]
                mapped = cmap.get(code)
                if mapped is not None:
                    chars.append(mapped)
                    hits += 1
                elif code == 0:
                    continue
                elif 32 <= code <= 0x10FFFF:
                    try:
                        chars.append(chr(code))
                    except ValueError:
                        chars.append("?")
                else:
                    chars.append("?")
            if hits:
                return "".join(chars)

    return _fallback_decode_text_bytes(raw)


def _decode_text_operand(tok: Tuple[str, Any], cmap: Optional[Dict[int, str]]) -> str:
    """Decode a Tj/TJ operand into readable text."""
    kind, value = tok
    if kind in ("str", "hex"):
        return _decode_text_bytes_with_cmap(value, cmap)
    if kind == "array":
        return "".join(
            _decode_text_operand(item, cmap)
            for item in value
            if item[0] in ("str", "hex", "array")
        )
    return ""


def _clean_extracted_text_piece(s: str) -> str:
    """Normalise one extracted text-showing result for the Text Stream view."""
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()



def _is_text_stream_candidate(stream_info: Dict[str, Any], decoded_txt: str) -> bool:
    """Return True only for streams worth attempting Text Stream extraction on.

    The Text Stream view is a convenience layer; it must never be allowed to
    decide whether the HTML report is produced. CMaps, font programs, images,
    ICC profiles, XMP metadata, xref streams, and object streams can contain
    text fragments such as BT/ET but are not page content streams. Parsing them
    as content is both misleading and was the source of the no-output hang.
    """
    stype = ((stream_info or {}).get("stream_type") or "").lower()
    if stype in {"cmap", "font_program", "image", "icc_profile", "xmp", "xref", "object_stream", "objstm"}:
        return False
    if stype == "content":
        return True

    # For generic/unknown streams, require a real text object and at least one
    # text-showing operator. This keeps backwards compatibility for content
    # streams not classified by object_analyser, but avoids CMap-style false hits.
    if not decoded_txt:
        return False
    if not re.search(r"(?<![A-Za-z0-9])BT(?![A-Za-z0-9]).*?(?<![A-Za-z0-9])ET(?![A-Za-z0-9])", decoded_txt, re.S):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])(?:Tj|TJ|'|\")(?=\s|\]|\)|$)", decoded_txt))

def _extract_text_stream_from_content(
    decoded_bytes: bytes,
    font_alias_to_cmap: Dict[str, Dict[int, str]],
) -> str:
    """Extract readable text from a decoded page content stream.

    The parser tracks the active font selected by /Fxx ... Tf, decodes Tj/TJ
    operands through that font's own /ToUnicode mapping, records the current
    text matrix position, and groups text fragments into rough rendered lines.
    """
    if not isinstance(decoded_bytes, (bytes, bytearray)) or not decoded_bytes:
        return ""

    segments: List[Tuple[float, float, str, str]] = []
    stack: List[Tuple[str, Any]] = []
    in_text = False
    current_font: Optional[str] = None
    x = 0.0
    y = 0.0

    for tok in _tokenise_pdf_content(bytes(decoded_bytes)):
        kind, value = tok

        if kind != "op":
            # Delimiters/dictionaries are not operands for text showing.
            if in_text and kind not in ("delimiter", "dict_start", "dict_end"):
                _push_text_operand(stack, tok)
            continue

        op = str(value)
        if op == "__PDFXRAY_TOKEN_LIMIT__":
            break

        if op == "BT":
            in_text = True
            stack = []
            x = 0.0
            y = 0.0
            continue

        if op == "ET":
            in_text = False
            stack = []
            continue

        if not in_text:
            stack = []
            continue

        if op == "Tf":
            if len(stack) >= 2 and stack[-2][0] == "name":
                current_font = str(stack[-2][1])
            stack = []
            continue

        if op == "Tm":
            nums = [item[1] for item in stack[-6:] if item[0] == "num"]
            if len(nums) == 6:
                x = float(nums[4])
                y = float(nums[5])
            stack = []
            continue

        if op in ("Td", "TD"):
            nums = [item[1] for item in stack[-2:] if item[0] == "num"]
            if len(nums) == 2:
                x += float(nums[0])
                y += float(nums[1])
            stack = []
            continue

        if op == "T*":
            # Approximate a new line when no explicit Tm is provided.
            y -= 1.0
            stack = []
            continue

        if op in _PDF_TEXT_SHOW_OPS:
            operand = next(
                (item for item in reversed(stack) if item[0] in ("str", "hex", "array")),
                None,
            )
            if operand is not None:
                cmap = font_alias_to_cmap.get(current_font or "")
                piece = _clean_extracted_text_piece(_decode_text_operand(operand, cmap))
                if piece:
                    segments.append((round(y, 3), round(x, 3), current_font or "", piece))
            stack = []
            continue

        # Other text-state/graphics operators are not text-showing operators.
        stack = []

    if not segments:
        return ""

    # Group by approximate baseline Y, sort top-to-bottom then left-to-right.
    # A small tolerance makes the output stable across floating point formatting.
    sorted_segments = sorted(segments, key=lambda item: (-item[0], item[1]))
    grouped: List[List[Tuple[float, float, str, str]]] = []
    tolerance = 1.5

    for seg in sorted_segments:
        if not grouped or abs(grouped[-1][0][0] - seg[0]) > tolerance:
            grouped.append([seg])
        else:
            grouped[-1].append(seg)

    lines: List[str] = []
    for group in grouped:
        group.sort(key=lambda item: item[1])
        pieces = [item[3] for item in group if item[3]]
        if pieces:
            lines.append("\t".join(pieces).rstrip())

    return "\n".join(lines).strip()


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
    if "\x00" in s:
        s = _rewrite_utf16ish_literals_with_cmap(s)
        if "\x00" in s:
            s = s.replace("\x00", "")


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

    highlighted = re.sub(r"\(([^()\n]{1,200})\)", _paren_repl, escaped)

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

    # ----------------------------------------------------------------------
    # Objects
    # ----------------------------------------------------------------------
    # Build once so each content stream can decode glyph codes using the
    # active font's own /ToUnicode CMap, not a merged/global CMap.
    font_alias_to_cmap = _build_font_cmap_lookup(analysis)

    for rec in analysis:
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
        lines.append(html.escape(raw_obj))
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

            # Text Stream: readable text extracted from Tj/TJ operands using
            # the active font's own /ToUnicode CMap.  This is intentionally
            # separate from Decoded Stream, which preserves the raw operators.
            try:
                text_stream = ""
                if _is_text_stream_candidate(stream_info, decoded_txt):
                    text_stream = _extract_text_stream_from_content(
                        decoded_bytes,
                        font_alias_to_cmap,
                    )
                if text_stream:
                    if collapse_objects:
                        lines.append("<details open>")
                        lines.append("<summary>Text Stream</summary>")
                    else:
                        lines.append("<h4>Text Stream</h4>")
                    lines.append("<pre class='stream text_stream'>")
                    lines.append(html.escape(text_stream))
                    lines.append("</pre>")
                    if collapse_objects:
                        lines.append("</details>")
            except Exception as e:
                # Defensive: never let the convenience Text Stream view break
                # the primary x-ray report.  The Decoded Stream remains above.
                lines.append("<div class='warning'>Warning: Text Stream extraction failed: " + html.escape(repr(e)) + "</div>")

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
                raw_bytes = img.get("raw_bytes")
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
    # XRef / trailer information from file tail (classic xref + trailer)
    # ------------------------------------------------------------------
    xref_section = summary.get("xref_trailer_text")
    if xref_section:
        lines.append("<h2>File tail: classic xref / trailer</h2>")
        if collapse_objects:
            lines.append("<details open>")
            lines.append("<summary>XRef / trailer section</summary>")
        lines.append("<pre class='stream'>")
        lines.append(html.escape(str(xref_section)))
        lines.append("</pre>")
        if collapse_objects:
            lines.append("</details>")

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


# ---------------------------------------------------------------------------
# PDF X-Ray stable override patch
# ---------------------------------------------------------------------------
# This block intentionally appears at the end of the module so the public
# render_html() name below overrides earlier versions while preserving the older
# helper functions above for compatibility.

PDFXRAY_HTML_RENDERER_PATCH_VERSION = "2.4.0-stable-progressive-safe-text"

import os as _pdfxray_os
import time as _pdfxray_time


def _pdfxray_write(fp, text: str) -> None:
    """Write and flush small chunks so a report exists even during long runs."""
    fp.write(text)
    if not text.endswith("\n"):
        fp.write("\n")
    try:
        fp.flush()
    except Exception:
        pass


def _pdfxray_details_tag(default_open: bool) -> str:
    return "<details open>" if default_open else "<details>"


def _pdfxray_to_latin1_text(value: Any, limit: Optional[int] = None) -> Tuple[str, bool]:
    """Convert bytes/str-ish values to display text with optional truncation."""
    truncated = False
    if value is None:
        return "", False
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if isinstance(value, bytes):
        data = value
        if limit and limit > 0 and len(data) > limit:
            data = data[:limit]
            truncated = True
        return data.decode("latin-1", errors="replace"), truncated
    text = str(value)
    if limit and limit > 0 and len(text) > limit:
        text = text[:limit]
        truncated = True
    return text, truncated


def _pdfxray_stream_type(stream_info: Dict[str, Any]) -> str:
    return str((stream_info or {}).get("stream_type") or "unknown").lower()


def _pdfxray_has_content_text_ops(decoded: str) -> bool:
    """Fast non-regex check for likely page-content text operations."""
    if not decoded:
        return False
    # Avoid expensive regex over large streams. These operator checks are only
    # candidates; the safe tokenizer confirms the actual operands.
    return ("BT" in decoded and "ET" in decoded and ("Tj" in decoded or "TJ" in decoded or "'" in decoded or '"' in decoded))


def _pdfxray_is_text_stream_candidate(stream_info: Dict[str, Any], decoded_txt: str) -> bool:
    stype = _pdfxray_stream_type(stream_info)
    if stype in {"cmap", "font_program", "image", "icc_profile", "xmp", "xref", "object_stream", "objstm"}:
        return False
    if stype == "content":
        return True
    return _pdfxray_has_content_text_ops(decoded_txt)


def _pdfxray_tokenise_content(data: bytes, max_tokens: int = 200000):
    """Bounded tokenizer for the subset of PDF content syntax needed for text.

    It always advances at least one byte per loop. Dictionary delimiters,
    braces, stray '<'/'>' and malformed names are consumed as delimiter tokens so
    malformed or non-content streams cannot trap the renderer.
    """
    if isinstance(data, memoryview):
        data = data.tobytes()
    if isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        return

    n = len(data)
    i = 0
    token_count = 0
    whitespace = b"\x00\x09\x0A\x0C\x0D\x20"
    delimiters = b"\x00\x09\x0A\x0C\x0D\x20()<>[]{}/%"

    def parse_literal(pos: int):
        pos += 1
        depth = 1
        out = bytearray()
        while pos < n and depth:
            c = data[pos]
            pos += 1
            if c == 0x5C:  # backslash escape
                if pos >= n:
                    break
                esc = data[pos]
                pos += 1
                if esc == ord("n"):
                    out.append(0x0A)
                elif esc == ord("r"):
                    out.append(0x0D)
                elif esc == ord("t"):
                    out.append(0x09)
                elif esc == ord("b"):
                    out.append(0x08)
                elif esc == ord("f"):
                    out.append(0x0C)
                elif esc in (0x28, 0x29, 0x5C):
                    out.append(esc)
                elif esc in (0x0A, 0x0D):
                    if esc == 0x0D and pos < n and data[pos] == 0x0A:
                        pos += 1
                elif 0x30 <= esc <= 0x37:
                    digs = [esc]
                    for _ in range(2):
                        if pos < n and 0x30 <= data[pos] <= 0x37:
                            digs.append(data[pos])
                            pos += 1
                        else:
                            break
                    try:
                        out.append(int(bytes(digs), 8) & 0xFF)
                    except Exception:
                        pass
                else:
                    out.append(esc)
                continue
            if c == 0x28:
                depth += 1
                out.append(c)
                continue
            if c == 0x29:
                depth -= 1
                if depth:
                    out.append(c)
                continue
            out.append(c)
        return ("str", bytes(out)), pos

    def parse_hex(pos: int):
        pos += 1
        chars: List[str] = []
        while pos < n and data[pos] != 0x3E:
            ch = chr(data[pos])
            if ch in "0123456789abcdefABCDEF":
                chars.append(ch)
            pos += 1
        if pos < n and data[pos] == 0x3E:
            pos += 1
        if len(chars) % 2:
            chars.append("0")
        try:
            return ("hex", bytes.fromhex("".join(chars))), pos
        except Exception:
            return ("hex", b""), pos

    while i < n and token_count < max_tokens:
        start = i
        c = data[i]

        if c in whitespace:
            i += 1
            continue

        if c == 0x25:  # comment
            while i < n and data[i] not in (0x0A, 0x0D):
                i += 1
            continue

        if c == 0x28:
            tok, i = parse_literal(i)
            token_count += 1
            yield tok
            if i <= start:
                i = start + 1
            continue

        if c == 0x3C and i + 1 < n and data[i + 1] == 0x3C:
            i += 2
            token_count += 1
            yield ("dict_start", "<<")
            continue
        if c == 0x3E and i + 1 < n and data[i + 1] == 0x3E:
            i += 2
            token_count += 1
            yield ("dict_end", ">>")
            continue
        if c == 0x3C:
            tok, i = parse_hex(i)
            token_count += 1
            yield tok
            if i <= start:
                i = start + 1
            continue
        if c == 0x3E:
            i += 1
            token_count += 1
            yield ("delimiter", ">")
            continue
        if c == 0x5B:
            i += 1
            token_count += 1
            yield ("array_start", "[")
            continue
        if c == 0x5D:
            i += 1
            token_count += 1
            yield ("array_end", "]")
            continue
        if c in (0x7B, 0x7D):
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue
        if c == 0x2F:  # name
            j = i + 1
            while j < n and data[j] not in delimiters:
                j += 1
            if j == i + 1:
                i += 1
                token_count += 1
                yield ("delimiter", "/")
            else:
                name = data[i + 1:j].decode("latin-1", errors="replace")
                i = j
                token_count += 1
                yield ("name", name)
            continue

        if c in delimiters:
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue

        j = i
        while j < n and data[j] not in delimiters:
            j += 1
        if j <= i:
            i += 1
            token_count += 1
            yield ("delimiter", chr(c))
            continue
        raw = data[i:j].decode("latin-1", errors="replace")
        i = j
        token_count += 1
        try:
            if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)", raw):
                yield ("num", float(raw) if "." in raw else int(raw))
            else:
                yield ("op", raw)
        except Exception:
            yield ("op", raw)

    if token_count >= max_tokens:
        yield ("op", "__PDFXRAY_TOKEN_LIMIT__")


def _pdfxray_push_operand(stack: List[Tuple[str, Any]], tok: Tuple[str, Any]) -> None:
    if tok[0] == "array_start":
        stack.append(("array_marker", None))
        return
    if tok[0] == "array_end":
        items: List[Tuple[str, Any]] = []
        while stack:
            item = stack.pop()
            if item[0] == "array_marker":
                break
            items.append(item)
        items.reverse()
        stack.append(("array", items))
        return
    stack.append(tok)


def _pdfxray_fallback_decode(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.startswith((b"\xfe\xff", b"\xff\xfe")):
        try:
            return raw.decode("utf-16", errors="replace").replace("\x00", "")
        except Exception:
            pass
    if b"\x00" in raw and len(raw) % 2 == 0:
        try:
            return raw.decode("utf-16-be", errors="replace").replace("\x00", "")
        except Exception:
            pass
    try:
        return raw.decode("utf-8", errors="replace").replace("\x00", "")
    except Exception:
        return raw.decode("latin-1", errors="replace").replace("\x00", "")


def _pdfxray_decode_with_cmap(raw: bytes, cmap: Optional[Dict[int, str]]) -> str:
    if not raw:
        return ""
    if cmap:
        # single-byte subset fonts first; this fixes the HOME_AFFAIRS invoice.
        chars: List[str] = []
        hits = 0
        total = 0
        for b in raw:
            total += 1
            mapped = cmap.get(b)
            if mapped is not None:
                chars.append(mapped)
                hits += 1
            elif b in (9, 10, 13):
                chars.append(chr(b))
            elif 32 <= b <= 126:
                chars.append(chr(b))
            else:
                chars.append("?")
        if hits and hits / max(total, 1) >= 0.20:
            return "".join(chars)

        if len(raw) % 2 == 0:
            chars = []
            hits = 0
            for i in range(0, len(raw), 2):
                code = (raw[i] << 8) | raw[i + 1]
                mapped = cmap.get(code)
                if mapped is not None:
                    chars.append(mapped)
                    hits += 1
                elif code == 0:
                    continue
                elif 32 <= code <= 0x10FFFF:
                    try:
                        chars.append(chr(code))
                    except Exception:
                        chars.append("?")
            if hits:
                return "".join(chars)
    return _pdfxray_fallback_decode(raw)


def _pdfxray_decode_operand(tok: Tuple[str, Any], cmap: Optional[Dict[int, str]]) -> str:
    kind, val = tok
    if kind in {"str", "hex"}:
        return _pdfxray_decode_with_cmap(val, cmap)
    if kind == "array":
        return "".join(_pdfxray_decode_operand(x, cmap) for x in val if x[0] in {"str", "hex", "array"})
    return ""


def _pdfxray_clean_piece(text: str) -> str:
    text = (text or "").replace("\xa0", " ").replace("\x00", "")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _pdfxray_extract_text_stream(decoded_bytes: bytes, font_alias_to_cmap: Dict[str, Dict[int, str]], max_tokens: int) -> Tuple[str, Optional[str]]:
    """Return extracted text and optional warning string."""
    if isinstance(decoded_bytes, memoryview):
        decoded_bytes = decoded_bytes.tobytes()
    if isinstance(decoded_bytes, bytearray):
        decoded_bytes = bytes(decoded_bytes)
    if not isinstance(decoded_bytes, bytes) or not decoded_bytes:
        return "", None

    segments: List[Tuple[float, float, str, str]] = []
    stack: List[Tuple[str, Any]] = []
    in_text = False
    current_font: Optional[str] = None
    x = 0.0
    y = 0.0
    limit_hit = False

    for tok in _pdfxray_tokenise_content(decoded_bytes, max_tokens=max_tokens):
        kind, value = tok
        if kind != "op":
            if in_text and kind not in {"delimiter", "dict_start", "dict_end"}:
                _pdfxray_push_operand(stack, tok)
            continue

        op = str(value)
        if op == "__PDFXRAY_TOKEN_LIMIT__":
            limit_hit = True
            break

        if op == "BT":
            in_text = True
            stack = []
            x = 0.0
            y = 0.0
            continue
        if op == "ET":
            in_text = False
            stack = []
            continue
        if not in_text:
            stack = []
            continue

        if op == "Tf":
            if len(stack) >= 2 and stack[-2][0] == "name":
                current_font = str(stack[-2][1])
            stack = []
            continue
        if op == "Tm":
            nums = [item[1] for item in stack[-6:] if item[0] == "num"]
            if len(nums) == 6:
                try:
                    x = float(nums[4])
                    y = float(nums[5])
                except Exception:
                    pass
            stack = []
            continue
        if op in {"Td", "TD"}:
            nums = [item[1] for item in stack[-2:] if item[0] == "num"]
            if len(nums) == 2:
                try:
                    x += float(nums[0])
                    y += float(nums[1])
                except Exception:
                    pass
            stack = []
            continue
        if op == "T*":
            y -= 1.0
            stack = []
            continue
        if op in _PDF_TEXT_SHOW_OPS:
            operand = next((item for item in reversed(stack) if item[0] in {"str", "hex", "array"}), None)
            if operand is not None:
                cmap = font_alias_to_cmap.get(current_font or "")
                piece = _pdfxray_clean_piece(_pdfxray_decode_operand(operand, cmap))
                if piece:
                    segments.append((round(y, 3), round(x, 3), current_font or "", piece))
            stack = []
            continue
        stack = []

    if not segments:
        return "", "Text Stream token limit reached before extractable text was found." if limit_hit else None

    sorted_segments = sorted(segments, key=lambda item: (-item[0], item[1]))
    grouped: List[List[Tuple[float, float, str, str]]] = []
    tolerance = 1.5
    for seg in sorted_segments:
        if not grouped or abs(grouped[-1][0][0] - seg[0]) > tolerance:
            grouped.append([seg])
        else:
            grouped[-1].append(seg)

    lines: List[str] = []
    for group in grouped:
        group.sort(key=lambda item: item[1])
        pieces = [item[3] for item in group if item[3]]
        if pieces:
            lines.append("\t".join(pieces).rstrip())
    warning = "Text Stream token limit reached; output may be partial." if limit_hit else None
    return "\n".join(lines).strip(), warning


def _pdfxray_format_decoded_stream(decoded_txt: str, stream_type: str) -> str:
    """Safe decoded-stream formatter that avoids catastrophic regex paths."""
    s = decoded_txt or ""
    if _pdfxray_stream_type({"stream_type": stream_type}) != "content" and not _pdfxray_has_content_text_ops(s[:250000]):
        return html.escape(s)
    # For content streams, strip NULs for readability, then escape. Keep this
    # intentionally simple; Text Stream provides the richer font-aware output.
    if "\x00" in s:
        s = s.replace("\x00", "")
    return html.escape(s)


def _pdfxray_write_start_html(out_path: str, pdf_path: str, config: Optional[Dict[str, Any]] = None) -> None:
    """Create a small diagnostic report immediately. It will be replaced/extended by render_html."""
    try:
        parent = _pdfxray_os.path.dirname(_pdfxray_os.path.abspath(out_path))
        if parent:
            _pdfxray_os.makedirs(parent, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG)
        if config:
            cfg.update(config)
        with open(out_path, "w", encoding="utf-8", errors="replace") as fp:
            _pdfxray_write(fp, _html_header(f"PDF x-ray: {pdf_path}"))
            _pdfxray_write(fp, "<h1>PDF X-Ray</h1>")
            _pdfxray_write(fp, f"<p><b>File:</b> {html.escape(str(pdf_path))}</p>")
            _pdfxray_write(fp, f"<p><b>Renderer patch:</b> {html.escape(PDFXRAY_HTML_RENDERER_PATCH_VERSION)}</p>")
            _pdfxray_write(fp, "<p><b>Status:</b> Report generation started. If this message remains, processing stopped before final HTML rendering completed.</p>")
            _pdfxray_write(fp, _html_footer())
    except Exception:
        pass


def render_html(
    analysis: List[Dict[str, Any]],
    summary: Dict[str, Any],
    pdf_path: str,
    out_path: str,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Progressive, fail-safe HTML writer.

    This override preserves the existing public function signature and output
    sections, but writes the final file progressively and isolates every object
    rendering step. A malformed stream can now produce a warning in that object,
    but it cannot prevent the HTML file being created for the PDF or block batch
    processing.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    _pdfxray_write_start_html(out_path, pdf_path, cfg)

    collapse_objects = bool(cfg.get("collapse_objects_by_default", True))
    details_open = bool(cfg.get("details_default_open", False))
    show_decoded = bool(cfg.get("show_decoded_stream", True))
    show_image_previews = bool(cfg.get("show_image_previews", True))
    show_text_stream = bool(cfg.get("show_text_stream", True))

    raw_limit_value = cfg.get("max_stream_preview_bytes", None)
    try:
        max_preview = int(raw_limit_value) if raw_limit_value not in (None, "", 0) else 0
    except Exception:
        max_preview = 0

    # Independent Text Stream safety limits. These are deliberately separate
    # from max_stream_preview_bytes so full decoded streams can still be shown
    # while the convenience extraction remains bounded.
    try:
        text_stream_max_bytes = int(cfg.get("text_stream_max_bytes", 250000) or 250000)
    except Exception:
        text_stream_max_bytes = 250000
    try:
        text_stream_max_tokens = int(cfg.get("text_stream_max_tokens", 200000) or 200000)
    except Exception:
        text_stream_max_tokens = 200000

    title = f"PDF x-ray: {pdf_path}"
    parent = _pdfxray_os.path.dirname(_pdfxray_os.path.abspath(out_path))
    if parent:
        _pdfxray_os.makedirs(parent, exist_ok=True)

    try:
        font_alias_to_cmap = _build_font_cmap_lookup(analysis)
    except Exception as e:
        font_alias_to_cmap = {}
        font_lookup_warning = f"Font/CMap lookup failed: {e!r}"
    else:
        font_lookup_warning = ""

    started = _pdfxray_time.strftime("%Y-%m-%d %H:%M:%S")

    with open(out_path, "w", encoding="utf-8", errors="replace") as fp:
        _pdfxray_write(fp, _html_header(title))
        _pdfxray_write(fp, "<h1>PDF X-Ray</h1>")
        app_version = cfg.get("app_version") or DEFAULT_CONFIG.get("app_version") or "unknown"
        _pdfxray_write(fp, f"<p><b>Version:</b> {html.escape(str(app_version))}</p>")
        _pdfxray_write(fp, f"<p><b>HTML renderer patch:</b> {html.escape(PDFXRAY_HTML_RENDERER_PATCH_VERSION)}</p>")
        _pdfxray_write(fp, f"<p><b>File:</b> {html.escape(str(pdf_path))}</p>")
        _pdfxray_write(fp, f"<p><b>Render started:</b> {html.escape(started)}</p>")
        if font_lookup_warning:
            _pdfxray_write(fp, "<div class='warning'>Warning: " + html.escape(font_lookup_warning) + "</div>")

        try:
            _pdfxray_write(fp, _summarise(summary or {}))
        except Exception as e:
            _pdfxray_write(fp, "<h2>Summary</h2><div class='warning'>Summary rendering failed: " + html.escape(repr(e)) + "</div>")

        try:
            prefix_text = (summary or {}).get("prefix_text")
            if prefix_text:
                _pdfxray_write(fp, "<h2>File header / pre-object bytes</h2>")
                if collapse_objects:
                    _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                    _pdfxray_write(fp, "<summary>Bytes before first indirect object</summary>")
                _pdfxray_write(fp, "<pre class='stream'>" + html.escape(str(prefix_text)) + "</pre>")
                if collapse_objects:
                    _pdfxray_write(fp, "</details>")
        except Exception as e:
            _pdfxray_write(fp, "<div class='warning'>Warning: Prefix rendering failed: " + html.escape(repr(e)) + "</div>")

        _pdfxray_write(fp, "<h2>Objects</h2>")
        total = len(analysis or [])
        for index, rec in enumerate(analysis or [], start=1):
            try:
                obj_num = rec.get("obj_num")
                gen_num = rec.get("gen_num")
                raw_obj = rec.get("raw_object_text") or ""
                stream_info = rec.get("stream_info") or {}
                warnings = rec.get("warnings") or []
                type_label = _stream_type_label(stream_info)
                try:
                    gen_class = gen_class_for_generation(gen_num)
                except Exception:
                    gen_class = "gen0"

                _pdfxray_write(fp, "<div class='obj'>")
                hdr = f"{obj_num} {gen_num} obj" if obj_num is not None else "Object"
                _pdfxray_write(fp, f"<div class='hdr {html.escape(str(gen_class))}'>" + html.escape(hdr) + f" &nbsp;[type: {html.escape(type_label)}] &nbsp;[{index}/{total}]</div>")

                for w in warnings:
                    _pdfxray_write(fp, "<div class='warning'>Warning: " + html.escape(str(w)) + "</div>")
                for err in (stream_info.get("errors") or []):
                    _pdfxray_write(fp, "<div class='warning'>Stream decode note: " + html.escape(str(err)) + "</div>")

                # Raw object
                try:
                    if collapse_objects:
                        _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                        _pdfxray_write(fp, "<summary>Raw Object</summary>")
                    _pdfxray_write(fp, "<pre class='stream'>" + html.escape(str(raw_obj)) + "</pre>")
                    if collapse_objects:
                        _pdfxray_write(fp, "</details>")
                except Exception as e:
                    _pdfxray_write(fp, "<div class='warning'>Raw object rendering failed: " + html.escape(repr(e)) + "</div>")

                decoded_bytes = stream_info.get("decoded_bytes")
                decoded_txt = ""
                if show_decoded and decoded_bytes is not None:
                    try:
                        decoded_txt, truncated = _pdfxray_to_latin1_text(decoded_bytes, max_preview if max_preview > 0 else None)
                        stream_type = (stream_info or {}).get("stream_type") or ""
                        formatted_txt = _pdfxray_format_decoded_stream(decoded_txt, stream_type)
                        if collapse_objects:
                            _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                            _pdfxray_write(fp, "<summary>Decoded Stream</summary>")
                        _pdfxray_write(fp, "<pre class='stream'>")
                        _pdfxray_write(fp, formatted_txt)
                        if truncated:
                            _pdfxray_write(fp, html.escape(f"\n\n[truncated at {max_preview} bytes for HTML preview]"))
                        _pdfxray_write(fp, "</pre>")
                        if collapse_objects:
                            _pdfxray_write(fp, "</details>")
                    except Exception as e:
                        _pdfxray_write(fp, "<div class='warning'>Decoded Stream rendering failed: " + html.escape(repr(e)) + "</div>")

                    # Text Stream - bounded and isolated.
                    if show_text_stream:
                        try:
                            # Use a small preview string for candidate detection if the full
                            # decoded text was not generated due to config limit.
                            if not decoded_txt:
                                decoded_txt, _ = _pdfxray_to_latin1_text(decoded_bytes, 250000)
                            raw_len = len(decoded_bytes) if isinstance(decoded_bytes, (bytes, bytearray, memoryview)) else len(str(decoded_bytes))
                            if _pdfxray_is_text_stream_candidate(stream_info, decoded_txt):
                                if raw_len > text_stream_max_bytes:
                                    _pdfxray_write(fp, "<div class='warning'>Text Stream skipped for this object: decoded stream length " + html.escape(str(raw_len)) + " bytes exceeds safety limit " + html.escape(str(text_stream_max_bytes)) + " bytes.</div>")
                                else:
                                    text_stream, ts_warning = _pdfxray_extract_text_stream(decoded_bytes, font_alias_to_cmap, text_stream_max_tokens)
                                    if ts_warning:
                                        _pdfxray_write(fp, "<div class='warning'>Warning: " + html.escape(ts_warning) + "</div>")
                                    if text_stream:
                                        if collapse_objects:
                                            _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                                            _pdfxray_write(fp, "<summary>Text Stream</summary>")
                                        else:
                                            _pdfxray_write(fp, "<h4>Text Stream</h4>")
                                        _pdfxray_write(fp, "<pre class='stream text_stream'>" + html.escape(text_stream) + "</pre>")
                                        if collapse_objects:
                                            _pdfxray_write(fp, "</details>")
                        except Exception as e:
                            _pdfxray_write(fp, "<div class='warning'>Warning: Text Stream extraction failed: " + html.escape(repr(e)) + "</div>")

                # Image preview remains preserved, but isolated.
                if show_image_previews and stream_info.get("stream_type") == "image":
                    try:
                        aux = stream_info.get("aux_info") or {}
                        img = aux.get("image") or {}
                        raw_bytes = img.get("png_bytes")
                        mime = "image/png"
                        if raw_bytes is None:
                            raw_bytes = img.get("raw_bytes")
                            mime = img.get("image_mime") or "image/jpeg"
                        if raw_bytes:
                            b64 = base64.b64encode(raw_bytes).decode("ascii", errors="ignore")
                            _pdfxray_write(fp, "<details class='image_preview' open>")
                            _pdfxray_write(fp, "<summary><b>Image Preview</b></summary>")
                            _pdfxray_write(fp, f"<img src='data:{html.escape(str(mime))};base64,{b64}' alt='Image preview for {html.escape(str(obj_num))} {html.escape(str(gen_num))} obj' style='max-width:600px;max-height:600px;border:1px solid #aaa;margin-top:4px;' />")
                            _pdfxray_write(fp, "</details>")
                    except Exception as e:
                        _pdfxray_write(fp, "<div class='warning'>Image preview failed: " + html.escape(repr(e)) + "</div>")

                _pdfxray_write(fp, "</div>")
            except Exception as e:
                _pdfxray_write(fp, "<div class='obj'><div class='hdr'>Object rendering failed</div><div class='warning'>" + html.escape(repr(e)) + "</div></div>")

        try:
            xref_section = (summary or {}).get("xref_trailer_text")
            if xref_section:
                _pdfxray_write(fp, "<h2>File tail: classic xref / trailer</h2>")
                if collapse_objects:
                    _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                    _pdfxray_write(fp, "<summary>XRef / trailer section</summary>")
                _pdfxray_write(fp, "<pre class='stream'>" + html.escape(str(xref_section)) + "</pre>")
                if collapse_objects:
                    _pdfxray_write(fp, "</details>")
        except Exception as e:
            _pdfxray_write(fp, "<div class='warning'>XRef/trailer rendering failed: " + html.escape(repr(e)) + "</div>")

        try:
            trailers = (summary or {}).get("parsed_trailers") or []
            if trailers:
                _pdfxray_write(fp, "<h2>Parsed trailer dictionaries</h2>")
                for i, t in enumerate(trailers, start=1):
                    if collapse_objects:
                        _pdfxray_write(fp, _pdfxray_details_tag(details_open))
                        _pdfxray_write(fp, f"<summary>Trailer #{i}</summary>")
                    else:
                        _pdfxray_write(fp, f"<h4>Trailer #{i}</h4>")
                    _pdfxray_write(fp, "<pre class='stream'>" + html.escape(str(t)) + "</pre>")
                    if collapse_objects:
                        _pdfxray_write(fp, "</details>")
        except Exception as e:
            _pdfxray_write(fp, "<div class='warning'>Parsed trailer rendering failed: " + html.escape(repr(e)) + "</div>")

        ended = _pdfxray_time.strftime("%Y-%m-%d %H:%M:%S")
        _pdfxray_write(fp, f"<p><b>Render completed:</b> {html.escape(ended)}</p>")
        _pdfxray_write(fp, _html_footer())

# ---------------------------------------------------------------------------
# PDF X-Ray Windows no-hang guard patch
# ---------------------------------------------------------------------------
# This final override deliberately comes after all earlier definitions.  It keeps
# the existing render_html() implementation above, but tightens Text Stream
# candidate selection so only streams already identified by stream_decoder as
# page-content streams are tokenised.  This prevents CMaps, images, ICC data,
# font programs, object streams, or unusual generic streams from ever entering
# the Text Stream tokenizer on Windows batch runs.
PDFXRAY_HTML_RENDERER_PATCH_VERSION = "2.5.0-windows-nohang-content-only-textstream"


def _pdfxray_is_text_stream_candidate(stream_info: Dict[str, Any], decoded_txt: str) -> bool:  # type: ignore[override]
    """Final safe Text Stream gate.

    Earlier versions allowed heuristic parsing of unknown/generic streams if the
    decoded bytes happened to contain BT/ET/Tj/TJ.  That was useful, but unsafe:
    CMaps and other stream programs can legitimately contain those byte patterns
    without being page content.  The safe rule is now:

        Text Stream extraction only runs when stream_decoder classified the
        stream as `content`.

    This preserves the intended Text Stream output for real page content objects
    such as HOME_AFFAIRS object 3, while preventing all non-content streams from
    blocking HTML creation.
    """
    try:
        return _pdfxray_stream_type(stream_info) == "content"
    except Exception:
        return False
# ---------------------------------------------------------------------------
# PDF X-Ray HARD SAFE renderer override v2.6.0
# ---------------------------------------------------------------------------
# Final drop-in override.  This deliberately avoids calling any of the older
# render_html/Text Stream helper chain above.  The previous public helpers remain
# in the module for compatibility, but the public render_html name is rebound
# below to a compact, bounded, progressive writer.

PDFXRAY_HTML_RENDERER_PATCH_VERSION = "2.6.0-hard-safe-renderer-nohang"


def _v260_now() -> str:
    try:
        return _pdfxray_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        import time as _t
        return _t.strftime("%Y-%m-%d %H:%M:%S")


def _v260_stream_type(stream_info: Dict[str, Any]) -> str:
    try:
        return str((stream_info or {}).get("stream_type") or "unknown").lower()
    except Exception:
        return "unknown"


def _v260_write(fp, text: str = "") -> None:
    try:
        fp.write(str(text))
        if not str(text).endswith("\n"):
            fp.write("\n")
        fp.flush()
    except Exception:
        # Nothing in the renderer should raise from a diagnostic write.
        pass


def _v260_escape(value: Any) -> str:
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    # Keep the HTML file text-safe. Some PDF streams/font programs contain raw
    # NUL/control bytes; writing them directly can make editors/grep treat the
    # report as binary and can upset Windows preview tools.
    text = text.replace("\x00", "")
    text = "".join(ch if (ch == "\n" or ch == "\r" or ch == "\t" or ord(ch) >= 32) else "·" for ch in text)
    return html.escape(text, quote=False)


def _v260_safe_len(value: Any) -> int:
    try:
        return len(value)
    except Exception:
        try:
            return len(str(value))
        except Exception:
            return 0


def _v260_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("latin-1", errors="replace")
    try:
        return bytes(value)
    except Exception:
        return str(value).encode("latin-1", errors="replace")


def _v260_text_from_bytes(value: Any, limit: Optional[int] = None) -> Tuple[str, bool]:
    data = _v260_bytes(value)
    truncated = False
    if limit and limit > 0 and len(data) > limit:
        data = data[:limit]
        truncated = True
    try:
        return data.decode("latin-1", errors="replace"), truncated
    except Exception:
        return str(value), truncated


def _v260_details(open_by_default: bool) -> str:
    return "<details open>" if open_by_default else "<details>"


def _v260_summary_html(summary: Dict[str, Any]) -> str:
    s = summary or {}
    lines = ["<h2>Summary</h2>", "<ul>"]
    preferred = [
        ("pdf_version", "PDF version"),
        ("object_count", "Objects analysed"),
        ("stream_count", "Streams"),
        ("image_count", "Images"),
        ("font_count", "Fonts"),
        ("incremental_update_count", "Incremental updates (approx)"),
    ]
    used = set()
    for key, label in preferred:
        if key in s:
            used.add(key)
            lines.append(f"<li>{_v260_escape(label)}: {_v260_escape(s.get(key))}</li>")
    # Keep any extra scalar summary fields visible without dumping very large blobs.
    for key, val in list(s.items())[:80]:
        if key in used or key in {"prefix_text", "xref_trailer_text", "parsed_trailers"}:
            continue
        if isinstance(val, (str, int, float, bool)) or val is None:
            txt = str(val)
            if len(txt) > 500:
                txt = txt[:500] + " ... [truncated]"
            lines.append(f"<li>{_v260_escape(key)}: {_v260_escape(txt)}</li>")
    lines.append("</ul>")
    return "\n".join(lines)


def _v260_find_font_block(raw: str, start: int) -> Tuple[str, int]:
    """Return body of /Font <<...>> from raw using a bounded manual scan."""
    n = len(raw)
    i = raw.find("<<", start)
    if i < 0:
        return "", start + 5
    i += 2
    body_start = i
    depth = 1
    max_i = min(n, i + 20000)
    while i < max_i - 1:
        pair = raw[i:i+2]
        if pair == "<<":
            depth += 1
            i += 2
            continue
        if pair == ">>":
            depth -= 1
            if depth <= 0:
                return raw[body_start:i], i + 2
            i += 2
            continue
        i += 1
    return raw[body_start:max_i], max_i


def _v260_build_font_cmap_lookup(analysis: List[Dict[str, Any]]) -> Dict[str, Dict[int, str]]:
    """Build /Font alias -> ToUnicode CMap mapping without using old helpers."""
    cmap_by_obj: Dict[int, Dict[int, str]] = {}
    font_to_cmap_obj: Dict[int, int] = {}
    alias_to_font_obj: Dict[str, int] = {}

    for rec in analysis or []:
        try:
            obj_num = rec.get("obj_num")
            raw = str(rec.get("raw_object_text") or "")
            if len(raw) > 250000:
                raw_scan = raw[:250000]
            else:
                raw_scan = raw
            si = rec.get("stream_info") or {}
            aux = si.get("aux_info") or {}
            cmap_info = aux.get("cmap") or {}
            mappings = cmap_info.get("mappings")
            if isinstance(obj_num, int) and isinstance(mappings, dict) and mappings:
                # Normalise keys/values.
                norm: Dict[int, str] = {}
                for k, v in list(mappings.items())[:200000]:
                    try:
                        norm[int(k)] = str(v)
                    except Exception:
                        pass
                if norm:
                    cmap_by_obj[obj_num] = norm

            if isinstance(obj_num, int) and "/ToUnicode" in raw_scan:
                m = re.search(r"/ToUnicode\s+(\d+)\s+(\d+)\s+R\b", raw_scan)
                if m:
                    font_to_cmap_obj[obj_num] = int(m.group(1))

            pos = 0
            for _ in range(50):
                pos = raw_scan.find("/Font", pos)
                if pos < 0:
                    break
                body, new_pos = _v260_find_font_block(raw_scan, pos)
                if body:
                    for am in re.finditer(r"/([A-Za-z0-9_.-]+)\s+(\d+)\s+(\d+)\s+R\b", body):
                        try:
                            alias_to_font_obj[am.group(1)] = int(am.group(2))
                        except Exception:
                            pass
                pos = max(new_pos, pos + 5)
        except Exception:
            continue

    out: Dict[str, Dict[int, str]] = {}
    for alias, font_obj in alias_to_font_obj.items():
        cmap_obj = font_to_cmap_obj.get(font_obj)
        cmap = cmap_by_obj.get(cmap_obj) if cmap_obj is not None else None
        if cmap:
            out[alias] = cmap
    return out


def _v260_parse_literal(data: bytes, pos: int, end: int) -> Tuple[Tuple[str, bytes], int]:
    pos += 1
    depth = 1
    out = bytearray()
    while pos < end and depth:
        c = data[pos]
        pos += 1
        if c == 0x5C:  # backslash
            if pos >= end:
                break
            esc = data[pos]
            pos += 1
            if esc == ord("n"):
                out.append(10)
            elif esc == ord("r"):
                out.append(13)
            elif esc == ord("t"):
                out.append(9)
            elif esc == ord("b"):
                out.append(8)
            elif esc == ord("f"):
                out.append(12)
            elif esc in (ord("("), ord(")"), ord("\\")):
                out.append(esc)
            elif esc in (10, 13):
                if esc == 13 and pos < end and data[pos] == 10:
                    pos += 1
            elif 48 <= esc <= 55:
                digs = [esc]
                for _ in range(2):
                    if pos < end and 48 <= data[pos] <= 55:
                        digs.append(data[pos])
                        pos += 1
                    else:
                        break
                try:
                    out.append(int(bytes(digs), 8) & 0xFF)
                except Exception:
                    pass
            else:
                out.append(esc)
            continue
        if c == 0x28:
            depth += 1
            out.append(c)
            continue
        if c == 0x29:
            depth -= 1
            if depth:
                out.append(c)
            continue
        out.append(c)
    return ("str", bytes(out)), pos


def _v260_parse_hex(data: bytes, pos: int, end: int) -> Tuple[Tuple[str, bytes], int]:
    pos += 1
    chars: List[str] = []
    while pos < end and data[pos] != 0x3E:
        c = data[pos]
        if (48 <= c <= 57) or (65 <= c <= 70) or (97 <= c <= 102):
            chars.append(chr(c))
        pos += 1
    if pos < end and data[pos] == 0x3E:
        pos += 1
    if len(chars) % 2:
        chars.append("0")
    try:
        return ("hex", bytes.fromhex("".join(chars))), pos
    except Exception:
        return ("hex", b""), pos


def _v260_tokens(data: bytes, max_bytes: int, max_tokens: int):
    data = _v260_bytes(data)
    end = min(len(data), max_bytes)
    i = 0
    count = 0
    whitespace = b"\x00\x09\x0A\x0C\x0D\x20"
    delimiters = b"\x00\x09\x0A\x0C\x0D\x20()<>[]{}/%"
    while i < end and count < max_tokens:
        start = i
        c = data[i]
        if c in whitespace:
            i += 1
            continue
        if c == 0x25:  # comment
            while i < end and data[i] not in (10, 13):
                i += 1
            continue
        if c == 0x28:
            tok, i = _v260_parse_literal(data, i, end)
            count += 1
            yield tok
        elif c == 0x3C and i + 1 < end and data[i+1] == 0x3C:
            i += 2; count += 1; yield ("dict_start", "<<")
        elif c == 0x3E and i + 1 < end and data[i+1] == 0x3E:
            i += 2; count += 1; yield ("dict_end", ">>")
        elif c == 0x3C:
            tok, i = _v260_parse_hex(data, i, end)
            count += 1
            yield tok
        elif c == 0x5B:
            i += 1; count += 1; yield ("array_start", "[")
        elif c == 0x5D:
            i += 1; count += 1; yield ("array_end", "]")
        elif c == 0x2F:
            j = i + 1
            while j < end and data[j] not in delimiters:
                j += 1
            if j > i + 1:
                yield ("name", data[i+1:j].decode("latin-1", errors="replace"))
                i = j
            else:
                i += 1
                yield ("delimiter", "/")
            count += 1
        elif c in delimiters or c in (0x7B, 0x7D):
            i += 1; count += 1; yield ("delimiter", chr(c))
        else:
            j = i
            while j < end and data[j] not in delimiters:
                j += 1
            if j <= i:
                i += 1; count += 1; yield ("delimiter", chr(c))
            else:
                raw = data[i:j].decode("latin-1", errors="replace")
                i = j; count += 1
                try:
                    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)", raw):
                        yield ("num", float(raw) if "." in raw else int(raw))
                    else:
                        yield ("op", raw)
                except Exception:
                    yield ("op", raw)
        if i <= start:
            i = start + 1
    if count >= max_tokens:
        yield ("op", "__PDFXRAY_TOKEN_LIMIT__")


def _v260_decode_bytes(raw: bytes, cmap: Optional[Dict[int, str]]) -> str:
    raw = _v260_bytes(raw)
    if not raw:
        return ""
    if cmap:
        chars: List[str] = []
        hits = 0
        for b in raw:
            m = cmap.get(b)
            if m is not None:
                chars.append(m); hits += 1
            elif b in (9, 10, 13):
                chars.append(chr(b))
            elif 32 <= b <= 126:
                chars.append(chr(b))
        if hits:
            return "".join(chars)
        if len(raw) % 2 == 0:
            chars = []
            for i in range(0, len(raw), 2):
                code = (raw[i] << 8) | raw[i+1]
                m = cmap.get(code)
                if m is not None:
                    chars.append(m); hits += 1
                elif code and 32 <= code <= 0x10FFFF:
                    try: chars.append(chr(code))
                    except Exception: pass
            if hits:
                return "".join(chars)
    # Fallbacks.
    for enc in ("utf-16-be", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc, errors="replace")
            if text:
                return text.replace("\x00", "")
        except Exception:
            pass
    return ""


def _v260_push(stack: List[Tuple[str, Any]], tok: Tuple[str, Any]) -> None:
    if tok[0] == "array_start":
        stack.append(("array_marker", None)); return
    if tok[0] == "array_end":
        arr: List[Tuple[str, Any]] = []
        while stack:
            item = stack.pop()
            if item[0] == "array_marker":
                break
            arr.append(item)
        arr.reverse()
        stack.append(("array", arr)); return
    stack.append(tok)


def _v260_operand_text(tok: Tuple[str, Any], cmap: Optional[Dict[int, str]]) -> str:
    kind, val = tok
    if kind in {"str", "hex"}:
        return _v260_decode_bytes(val, cmap)
    if kind == "array":
        parts: List[str] = []
        for x in val:
            if x[0] in {"str", "hex", "array"}:
                parts.append(_v260_operand_text(x, cmap))
        return "".join(parts)
    return ""


def _v260_clean_text_piece(text: str) -> str:
    text = (text or "").replace("\x00", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _v260_extract_text_stream(decoded_bytes: Any, font_cmaps: Dict[str, Dict[int, str]], max_bytes: int = 250000, max_tokens: int = 80000) -> Tuple[str, Optional[str]]:
    data = _v260_bytes(decoded_bytes)
    if not data:
        return "", None
    stack: List[Tuple[str, Any]] = []
    in_text = False
    font: Optional[str] = None
    x = 0.0
    y = 0.0
    segs: List[Tuple[float, float, str]] = []
    limit_hit = False
    for kind, value in _v260_tokens(data, max_bytes=max_bytes, max_tokens=max_tokens):
        if kind != "op":
            if in_text and kind not in {"delimiter", "dict_start", "dict_end"}:
                _v260_push(stack, (kind, value))
            continue
        op = str(value)
        if op == "__PDFXRAY_TOKEN_LIMIT__":
            limit_hit = True
            break
        if op == "BT":
            in_text = True; stack = []; x = 0.0; y = 0.0; continue
        if op == "ET":
            in_text = False; stack = []; continue
        if not in_text:
            stack = []
            continue
        if op == "Tf":
            if len(stack) >= 2 and stack[-2][0] == "name":
                font = str(stack[-2][1])
            stack = []
            continue
        if op == "Tm":
            nums = [v for k, v in stack if k == "num"]
            if len(nums) >= 6:
                try:
                    x = float(nums[-2]); y = float(nums[-1])
                except Exception:
                    pass
            stack = []
            continue
        if op in {"Td", "TD"}:
            nums = [v for k, v in stack if k == "num"]
            if len(nums) >= 2:
                try:
                    x += float(nums[-2]); y += float(nums[-1])
                except Exception:
                    pass
            stack = []
            continue
        if op == "T*":
            y -= 1.0; stack = []; continue
        if op in {"Tj", "TJ", "'", '"'}:
            operand = None
            for item in reversed(stack):
                if item[0] in {"str", "hex", "array"}:
                    operand = item; break
            if operand is not None:
                cmap = font_cmaps.get(font or "")
                piece = _v260_clean_text_piece(_v260_operand_text(operand, cmap))
                if piece:
                    segs.append((round(y, 2), round(x, 2), piece))
            stack = []
            continue
        stack = []
    if not segs:
        return "", "Text Stream token/byte limit reached before text was found." if limit_hit else None
    segs.sort(key=lambda t: (-t[0], t[1]))
    lines: List[str] = []
    current_y: Optional[float] = None
    current: List[Tuple[float, str]] = []
    for yv, xv, txt in segs:
        if current_y is None or abs(current_y - yv) <= 1.5:
            current_y = yv if current_y is None else current_y
            current.append((xv, txt))
        else:
            current.sort(key=lambda t: t[0])
            lines.append("\t".join(t for _, t in current))
            current_y = yv
            current = [(xv, txt)]
    if current:
        current.sort(key=lambda t: t[0])
        lines.append("\t".join(t for _, t in current))
    warning = "Text Stream token/byte limit reached; output may be partial." if limit_hit else None
    return "\n".join(lines).strip(), warning


def render_html(  # type: ignore[override]
    analysis: List[Dict[str, Any]],
    summary: Dict[str, Any],
    pdf_path: str,
    out_path: str,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Hard-safe progressive HTML writer for PDF X-Ray.

    This final override is intentionally self-contained.  It does not call the
    earlier render_html() implementation or the older Text Stream extraction
    helpers.  It preserves the public API and the usual sections, but each stage
    writes and flushes progressively so a report file is created even if a later
    object fails.
    """
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    parent = _pdfxray_os.path.dirname(_pdfxray_os.path.abspath(out_path))
    if parent:
        _pdfxray_os.makedirs(parent, exist_ok=True)

    collapse = bool(cfg.get("collapse_objects_by_default", True))
    details_open = bool(cfg.get("details_default_open", False))
    show_decoded = bool(cfg.get("show_decoded_stream", True))
    show_images = bool(cfg.get("show_image_previews", True))
    show_text = bool(cfg.get("show_text_stream", True))
    try:
        max_preview = cfg.get("max_stream_preview_bytes", None)
        max_preview = int(max_preview) if max_preview not in (None, "", 0) else 0
    except Exception:
        max_preview = 0
    try:
        text_max_bytes = int(cfg.get("text_stream_max_bytes", 250000) or 250000)
    except Exception:
        text_max_bytes = 250000
    try:
        text_max_tokens = int(cfg.get("text_stream_max_tokens", 80000) or 80000)
    except Exception:
        text_max_tokens = 80000

    font_cmaps: Dict[str, Dict[int, str]] = {}
    font_warning = ""
    try:
        font_cmaps = _v260_build_font_cmap_lookup(analysis or [])
    except Exception as e:
        font_warning = f"Font/CMap lookup failed: {e!r}"

    title = f"PDF x-ray: {pdf_path}"
    with open(out_path, "w", encoding="utf-8", errors="replace") as fp:
        _v260_write(fp, _html_header(title))
        _v260_write(fp, "<h1>PDF X-Ray</h1>")
        _v260_write(fp, f"<p><b>Version:</b> {_v260_escape(cfg.get('app_version', 'unknown'))}</p>")
        _v260_write(fp, f"<p><b>HTML renderer patch:</b> {_v260_escape(PDFXRAY_HTML_RENDERER_PATCH_VERSION)}</p>")
        _v260_write(fp, f"<p><b>File:</b> {_v260_escape(pdf_path)}</p>")
        _v260_write(fp, f"<p><b>Render started:</b> {_v260_escape(_v260_now())}</p>")
        if font_warning:
            _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(font_warning)}</div>")

        try:
            _v260_write(fp, _v260_summary_html(summary or {}))
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Summary failed: {_v260_escape(repr(e))}</div>")

        try:
            prefix = (summary or {}).get("prefix_text")
            if prefix:
                _v260_write(fp, "<h2>File header / pre-object bytes</h2>")
                if collapse:
                    _v260_write(fp, _v260_details(details_open)); _v260_write(fp, "<summary>Bytes before first indirect object</summary>")
                _v260_write(fp, f"<pre class='stream'>{_v260_escape(prefix)}</pre>")
                if collapse: _v260_write(fp, "</details>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Prefix rendering failed: {_v260_escape(repr(e))}</div>")

        total = len(analysis or [])
        _v260_write(fp, f"<h2>Objects ({total})</h2>")
        for idx, rec in enumerate(analysis or [], start=1):
            _v260_write(fp, f"<!-- PDFXRAY_RENDER_OBJECT_START {idx}/{total} -->")
            try:
                obj_num = rec.get("obj_num")
                gen_num = rec.get("gen_num")
                raw_obj = rec.get("raw_object_text") or ""
                stream_info = rec.get("stream_info") or {}
                stype = _v260_stream_type(stream_info)
                try:
                    gen_class = gen_class_for_generation(gen_num)
                except Exception:
                    gen_class = "gen0"
                hdr = f"{obj_num} {gen_num} obj" if obj_num is not None else "Object"
                _v260_write(fp, "<div class='obj'>")
                _v260_write(fp, f"<div class='hdr {_v260_escape(gen_class)}'>{_v260_escape(hdr)} &nbsp;[type: {_v260_escape(stype)}] &nbsp;[{idx}/{total}]</div>")

                for w in rec.get("warnings") or []:
                    _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(w)}</div>")
                for err in (stream_info.get("errors") or []):
                    _v260_write(fp, f"<div class='warning'>Stream decode note: {_v260_escape(err)}</div>")

                try:
                    if collapse:
                        _v260_write(fp, _v260_details(details_open)); _v260_write(fp, "<summary>Raw Object</summary>")
                    raw_txt = str(raw_obj)
                    _v260_write(fp, f"<pre class='stream'>{_v260_escape(raw_txt)}</pre>")
                    if collapse: _v260_write(fp, "</details>")
                except Exception as e:
                    _v260_write(fp, f"<div class='warning'>Raw Object failed: {_v260_escape(repr(e))}</div>")

                decoded = stream_info.get("decoded_bytes")
                if show_decoded and decoded is not None:
                    try:
                        decoded_txt, trunc = _v260_text_from_bytes(decoded, max_preview if max_preview > 0 else None)
                        if "\x00" in decoded_txt and stype == "content":
                            decoded_txt = decoded_txt.replace("\x00", "")
                        if collapse:
                            _v260_write(fp, _v260_details(details_open)); _v260_write(fp, "<summary>Decoded Stream</summary>")
                        _v260_write(fp, f"<pre class='stream'>{_v260_escape(decoded_txt)}")
                        if trunc:
                            _v260_write(fp, _v260_escape(f"\n\n[truncated at {max_preview} bytes for HTML preview]"))
                        _v260_write(fp, "</pre>")
                        if collapse: _v260_write(fp, "</details>")
                    except Exception as e:
                        _v260_write(fp, f"<div class='warning'>Decoded Stream failed: {_v260_escape(repr(e))}</div>")

                    if show_text and stype == "content":
                        try:
                            raw_len = _v260_safe_len(_v260_bytes(decoded))
                            if raw_len > text_max_bytes:
                                _v260_write(fp, f"<div class='warning'>Text Stream skipped: stream length {_v260_escape(raw_len)} exceeds safety limit {_v260_escape(text_max_bytes)} bytes.</div>")
                            else:
                                text_stream, warn = _v260_extract_text_stream(decoded, font_cmaps, text_max_bytes, text_max_tokens)
                                if warn:
                                    _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(warn)}</div>")
                                if text_stream:
                                    if collapse:
                                        _v260_write(fp, _v260_details(details_open)); _v260_write(fp, "<summary>Text Stream</summary>")
                                    else:
                                        _v260_write(fp, "<h4>Text Stream</h4>")
                                    _v260_write(fp, f"<pre class='stream text_stream'>{_v260_escape(text_stream)}</pre>")
                                    if collapse: _v260_write(fp, "</details>")
                        except Exception as e:
                            _v260_write(fp, f"<div class='warning'>Text Stream extraction failed: {_v260_escape(repr(e))}</div>")

                if show_images and stype == "image":
                    try:
                        aux = stream_info.get("aux_info") or {}
                        img = aux.get("image") or {}
                        raw_img = img.get("png_bytes")
                        mime = "image/png"
                        if raw_img is None:
                            raw_img = img.get("raw_bytes")
                            mime = img.get("image_mime") or "image/jpeg"
                        if raw_img:
                            raw_b = _v260_bytes(raw_img)
                            # Preserve previews, but cap enormous previews so future images cannot freeze report generation.
                            if len(raw_b) <= int(cfg.get("image_preview_max_bytes", 4_000_000) or 4_000_000):
                                b64 = base64.b64encode(raw_b).decode("ascii", errors="ignore")
                                _v260_write(fp, "<details class='image_preview' open>")
                                _v260_write(fp, "<summary><b>Image Preview</b></summary>")
                                _v260_write(fp, f"<img src='data:{_v260_escape(mime)};base64,{b64}' alt='Image preview for {_v260_escape(hdr)}' style='max-width:600px;max-height:600px;border:1px solid #aaa;margin-top:4px;' />")
                                _v260_write(fp, "</details>")
                            else:
                                _v260_write(fp, f"<div class='warning'>Image preview skipped: {_v260_escape(len(raw_b))} bytes exceeds safety limit.</div>")
                    except Exception as e:
                        _v260_write(fp, f"<div class='warning'>Image preview failed: {_v260_escape(repr(e))}</div>")
                _v260_write(fp, "</div>")
            except Exception as e:
                _v260_write(fp, f"<div class='obj'><div class='hdr'>Object render failure [{idx}/{total}]</div><div class='warning'>{_v260_escape(repr(e))}</div></div>")
            _v260_write(fp, f"<!-- PDFXRAY_RENDER_OBJECT_END {idx}/{total} -->")

        try:
            xref_text = (summary or {}).get("xref_trailer_text")
            if xref_text:
                _v260_write(fp, "<h2>File tail: classic xref / trailer</h2>")
                if collapse:
                    _v260_write(fp, _v260_details(details_open)); _v260_write(fp, "<summary>XRef / trailer section</summary>")
                _v260_write(fp, f"<pre class='stream'>{_v260_escape(xref_text)}</pre>")
                if collapse: _v260_write(fp, "</details>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>XRef/trailer rendering failed: {_v260_escape(repr(e))}</div>")

        try:
            trailers = (summary or {}).get("parsed_trailers") or []
            if trailers:
                _v260_write(fp, "<h2>Parsed trailer dictionaries</h2>")
                for t_i, t in enumerate(trailers, start=1):
                    if collapse:
                        _v260_write(fp, _v260_details(details_open)); _v260_write(fp, f"<summary>Trailer #{t_i}</summary>")
                    else:
                        _v260_write(fp, f"<h4>Trailer #{t_i}</h4>")
                    _v260_write(fp, f"<pre class='stream'>{_v260_escape(t)}</pre>")
                    if collapse: _v260_write(fp, "</details>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Trailer rendering failed: {_v260_escape(repr(e))}</div>")

        _v260_write(fp, f"<p><b>Render completed:</b> {_v260_escape(_v260_now())}</p>")
        _v260_write(fp, _html_footer())


# ---------------------------------------------------------------------------
# PDF X-Ray patch 2.7.0
# ---------------------------------------------------------------------------
# Purpose:
#   * preserve the 2.6 hard-safe/no-hang rendering behaviour
#   * restore a cleaner/original report appearance by removing renderer patch
#     noise from the final HTML report
#   * render classic xref/trailer sections at their byte position in the PDF
#     rather than dumping trailer information at the bottom or inside Summary
#   * add object type labelling in the object header as [Type: ...]
#   * add SHA-256 and MD5 file hashes underneath the File field
#   * add report generation date underneath Version

PDFXRAY_HTML_RENDERER_PATCH_VERSION = "2.7.0-layout-restore-hash-trailer-position"


def _v270_report_date() -> str:
    try:
        import time as _t
        return _t.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


def _v270_file_hashes(pdf_path: str) -> Dict[str, str]:
    """Return SHA-256/MD5 hashes using the drop-in hash.py module.

    Falls back to hashlib directly if hash.py is absent so report generation is
    not broken by a missing optional helper.
    """
    try:
        from hash import calculate_file_hashes  # local drop-in module
        hashes = calculate_file_hashes(pdf_path)
        return {
            "sha256": str(hashes.get("sha256", "")),
            "md5": str(hashes.get("md5", "")),
        }
    except Exception:
        try:
            import hashlib as _hashlib
            sha = _hashlib.sha256()
            md5 = _hashlib.md5()
            with open(pdf_path, "rb") as _f:
                while True:
                    _chunk = _f.read(1024 * 1024)
                    if not _chunk:
                        break
                    sha.update(_chunk)
                    md5.update(_chunk)
            return {"sha256": sha.hexdigest(), "md5": md5.hexdigest()}
        except Exception as e:
            return {"sha256": f"Unavailable ({e!r})", "md5": f"Unavailable ({e!r})"}


def _v270_summary_html(summary: Dict[str, Any]) -> str:
    """Render a concise forensic summary.

    Deliberately excludes bulky structural blobs such as file_tail_text and any
    renderer/report-generation fields. The detailed byte-level material is
    shown in its own location-aware section instead.
    """
    s = summary or {}
    lines: List[str] = ["<h2>Summary</h2>", "<ul>"]

    def add(label: str, key: str) -> None:
        if key in s and s.get(key) is not None:
            lines.append(f"<li>{_v260_escape(label)}: {_v260_escape(s.get(key))}</li>")

    add("Total objects", "object_count")
    add("Image streams", "image_stream_count")
    add("Incremental updates (approx)", "incremental_update_count")
    add("Classic xref tables", "xref_table_count")
    add("XRef streams", "xref_stream_count")
    add("startxref markers", "startxref_count")
    add("Trailer keyword count", "trailer_keyword_count")

    flags = s.get("flags") or []
    if flags:
        lines.append("<li>Flags / notes:</li>")
        lines.append("<ul>")
        for flag in flags:
            lines.append(f"<li>{_v260_escape(flag)}</li>")
        lines.append("</ul>")

    lines.append("</ul>")
    return "\n".join(lines)


def _v270_clean_pdf_name(value: str) -> str:
    try:
        import os as _os
        return _os.path.basename(str(value)) or str(value)
    except Exception:
        return str(value)


def _v270_raw_snippet(raw_obj: str, limit: int = 250000) -> str:
    try:
        raw = str(raw_obj or "")
    except Exception:
        raw = repr(raw_obj)
    if len(raw) > limit:
        return raw[:limit]
    return raw


def _v270_name_token_to_label(token: str) -> str:
    token = (token or "").strip().strip("/")
    if not token:
        return "Unknown"
    labels = {
        "Page": "Page",
        "Pages": "Pages",
        "Catalog": "Catalog",
        "Font": "Font",
        "FontDescriptor": "Font Descriptor",
        "XObject": "XObject",
        "Metadata": "Metadata",
        "ObjStm": "Object Stream",
        "XRef": "XRef Stream",
        "Encoding": "Encoding",
        "CMap": "CMap",
        "Annot": "Annotation",
        "Outlines": "Outlines",
        "Pattern": "Pattern",
        "ExtGState": "ExtGState",
    }
    return labels.get(token, token.replace("_", " "))


def _v270_object_type_label(rec: Dict[str, Any]) -> str:
    """Return examiner-friendly PDF object type text for the [Type: ] label."""
    try:
        raw = _v270_raw_snippet(rec.get("raw_object_text") or "")
        si = rec.get("stream_info") or {}
        stype = str(si.get("stream_type") or "").lower()

        type_match = re.search(r"/Type\s*/([A-Za-z0-9_.-]+)", raw)
        subtype_match = re.search(r"/Subtype\s*/([A-Za-z0-9_.-]+)", raw)
        base_type = _v270_name_token_to_label(type_match.group(1)) if type_match else ""
        subtype = _v270_name_token_to_label(subtype_match.group(1)) if subtype_match else ""

        if base_type and subtype:
            # Common compact labels.
            if base_type == "XObject" and subtype == "Image":
                return "Image XObject"
            if base_type == "Font":
                return f"Font / {subtype}"
            return f"{base_type} / {subtype}"
        if base_type:
            return base_type

        # No /Type entry: infer from decoder classification.
        if stype == "content":
            return "Content Stream"
        if stype == "image":
            return "Image Stream"
        if stype == "cmap":
            return "ToUnicode CMap"
        if stype == "font_program":
            return "Embedded Font Program"
        if stype == "icc_profile":
            return "ICC Profile"
        if stype == "xmp":
            return "XMP Metadata"
        if stype == "xref":
            return "XRef Stream"
        if stype == "generic":
            return "Generic Stream"
        if rec.get("dict_text"):
            return "Dictionary / Non-stream Object"
        return "Unknown"
    except Exception:
        return "Unknown"


def _v270_object_ranges(analysis: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    for rec in analysis or []:
        try:
            start = rec.get("header_offset")
            raw = rec.get("raw_object_text") or ""
            if isinstance(start, int) and start >= 0:
                end = start + len(str(raw).encode("latin-1", errors="replace"))
                if end > start:
                    ranges.append((start, end))
        except Exception:
            continue
    ranges.sort()
    return ranges


def _v270_in_ranges(pos: int, ranges: List[Tuple[int, int]]) -> bool:
    # Small PDFs for this tool path; linear scan is simple and robust.
    for start, end in ranges:
        if start <= pos < end:
            return True
        if start > pos:
            return False
    return False


def _v270_extract_nonobject_pdf_sections(pdf_path: str, analysis: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract classic non-object xref/trailer chunks with byte offsets.

    These chunks are inserted into the HTML in byte-order with indirect objects.
    This preserves where the trailer appears in the original file, including
    future incremental-update scenarios where xref/trailer sections can appear
    before later appended objects.
    """
    sections: List[Dict[str, Any]] = []
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
    except Exception:
        return sections

    text = data.decode("latin-1", errors="replace")
    ranges = _v270_object_ranges(analysis)
    captured: List[Tuple[int, int]] = []

    def already_captured(p: int) -> bool:
        return any(s <= p < e for s, e in captured)

    # Capture classic xref + trailer + startxref + EOF chunks.
    # Use line-start xref to avoid matching /Type /XRef inside objects.
    for m in re.finditer(r"(?m)(?:^|[\r\n])xref\b", text):
        pos = m.start()
        if text[pos:pos+1] in "\r\n":
            pos += 1
        if _v270_in_ranges(pos, ranges) or already_captured(pos):
            continue
        if pos > 0 and text[pos - 1] == "/":
            continue

        eof = text.find("%%EOF", pos)
        if eof != -1:
            end = eof + len("%%EOF")
            # Include the rest of the EOF line.
            nl_candidates = [p for p in (text.find("\n", end), text.find("\r", end)) if p != -1]
            if nl_candidates:
                end = min(nl_candidates) + 1
        else:
            # Fallback: stop before the next indirect object after this section.
            later_object_starts = [s for s, _e in ranges if s > pos]
            end = min(later_object_starts) if later_object_starts else len(text)

        if end > pos:
            chunk = text[pos:end].strip("\r\n")
            if chunk:
                captured.append((pos, end))
                sections.append({
                    "kind": "xref_trailer",
                    "offset": pos,
                    "title": "Classic xref / trailer section",
                    "text": chunk,
                })

    # Fallback: capture stray trailer chunks outside objects not already inside
    # an xref section. This protects unusual PDFs without duplicating normal ones.
    for m in re.finditer(r"(?m)(?:^|[\r\n])trailer\b", text):
        pos = m.start()
        if text[pos:pos+1] in "\r\n":
            pos += 1
        if _v270_in_ranges(pos, ranges) or already_captured(pos):
            continue
        end_candidates = []
        for token in ("startxref", "%%EOF"):
            p = text.find(token, pos + 7)
            if p != -1:
                end_candidates.append(p)
        later_object_starts = [s for s, _e in ranges if s > pos]
        if later_object_starts:
            end_candidates.append(min(later_object_starts))
        end = min(end_candidates) if end_candidates else len(text)
        if end > pos:
            chunk = text[pos:end].strip("\r\n")
            if chunk:
                captured.append((pos, end))
                sections.append({
                    "kind": "trailer",
                    "offset": pos,
                    "title": "Trailer section",
                    "text": chunk,
                })

    sections.sort(key=lambda s: int(s.get("offset") or 0))
    return sections


def _v270_write_nonobject_section(fp, section: Dict[str, Any], collapse: bool, details_open: bool) -> None:
    try:
        title = str(section.get("title") or "PDF non-object section")
        offset = section.get("offset")
        text = section.get("text") or ""
        _v260_write(fp, "<div class='obj'>")
        off_txt = f" @ byte offset {offset}" if isinstance(offset, int) else ""
        _v260_write(fp, f"<div class='hdr gen0'>{_v260_escape(title + off_txt)} &nbsp;[Type: File Structure]</div>")
        if collapse:
            _v260_write(fp, _v260_details(details_open))
            _v260_write(fp, f"<summary>{_v260_escape(title)}</summary>")
        _v260_write(fp, f"<pre class='stream'>{_v260_escape(text)}</pre>")
        if collapse:
            _v260_write(fp, "</details>")
        _v260_write(fp, "</div>")
    except Exception as e:
        _v260_write(fp, f"<div class='warning'>Non-object section render failed: {_v260_escape(repr(e))}</div>")


def _v270_render_object(
    fp,
    rec: Dict[str, Any],
    cfg: Dict[str, Any],
    collapse: bool,
    details_open: bool,
    show_decoded: bool,
    show_text: bool,
    show_images: bool,
    max_preview: int,
    text_max_bytes: int,
    text_max_tokens: int,
    font_cmaps: Dict[str, Dict[int, str]],
) -> None:
    obj_num = rec.get("obj_num")
    gen_num = rec.get("gen_num")
    raw_obj = rec.get("raw_object_text") or ""
    stream_info = rec.get("stream_info") or {}
    stype = _v260_stream_type(stream_info)
    type_label = _v270_object_type_label(rec)
    try:
        gen_class = gen_class_for_generation(gen_num)
    except Exception:
        gen_class = "gen0"
    hdr = f"{obj_num} {gen_num} obj" if obj_num is not None else "Object"

    _v260_write(fp, "<div class='obj'>")
    _v260_write(fp, f"<div class='hdr {_v260_escape(gen_class)}'>{_v260_escape(hdr)} &nbsp;[Type: {_v260_escape(type_label)}]</div>")

    for w in rec.get("warnings") or []:
        _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(w)}</div>")
    for err in (stream_info.get("errors") or []):
        _v260_write(fp, f"<div class='warning'>Stream decode note: {_v260_escape(err)}</div>")

    try:
        if collapse:
            _v260_write(fp, _v260_details(details_open))
            _v260_write(fp, "<summary>Raw Object</summary>")
        _v260_write(fp, f"<pre class='stream'>{_v260_escape(raw_obj)}</pre>")
        if collapse:
            _v260_write(fp, "</details>")
    except Exception as e:
        _v260_write(fp, f"<div class='warning'>Raw Object failed: {_v260_escape(repr(e))}</div>")

    decoded = stream_info.get("decoded_bytes")
    if show_decoded and decoded is not None:
        try:
            decoded_txt, trunc = _v260_text_from_bytes(decoded, max_preview if max_preview > 0 else None)
            if "\x00" in decoded_txt and stype == "content":
                decoded_txt = decoded_txt.replace("\x00", "")
            if collapse:
                _v260_write(fp, _v260_details(details_open))
                _v260_write(fp, "<summary>Decoded Stream</summary>")
            _v260_write(fp, f"<pre class='stream'>{_v260_escape(decoded_txt)}")
            if trunc:
                _v260_write(fp, _v260_escape(f"\n\n[truncated at {max_preview} bytes for HTML preview]"))
            _v260_write(fp, "</pre>")
            if collapse:
                _v260_write(fp, "</details>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Decoded Stream failed: {_v260_escape(repr(e))}</div>")

        if show_text and stype == "content":
            try:
                raw_len = _v260_safe_len(_v260_bytes(decoded))
                if raw_len > text_max_bytes:
                    _v260_write(fp, f"<div class='warning'>Text Stream skipped: stream length {_v260_escape(raw_len)} exceeds safety limit {_v260_escape(text_max_bytes)} bytes.</div>")
                else:
                    text_stream, warn = _v260_extract_text_stream(decoded, font_cmaps, text_max_bytes, text_max_tokens)
                    if warn:
                        _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(warn)}</div>")
                    if text_stream:
                        if collapse:
                            _v260_write(fp, _v260_details(details_open))
                            _v260_write(fp, "<summary>Text Stream</summary>")
                        else:
                            _v260_write(fp, "<h4>Text Stream</h4>")
                        _v260_write(fp, f"<pre class='stream text_stream'>{_v260_escape(text_stream)}</pre>")
                        if collapse:
                            _v260_write(fp, "</details>")
            except Exception as e:
                _v260_write(fp, f"<div class='warning'>Text Stream extraction failed: {_v260_escape(repr(e))}</div>")

    if show_images and stype == "image":
        try:
            aux = stream_info.get("aux_info") or {}
            img = aux.get("image") or {}
            raw_img = img.get("png_bytes")
            mime = "image/png"
            if raw_img is None:
                raw_img = img.get("raw_bytes")
                mime = img.get("image_mime") or "image/jpeg"
            if raw_img:
                raw_b = _v260_bytes(raw_img)
                if len(raw_b) <= int(cfg.get("image_preview_max_bytes", 4_000_000) or 4_000_000):
                    b64 = base64.b64encode(raw_b).decode("ascii", errors="ignore")
                    _v260_write(fp, "<details class='image_preview' open>")
                    _v260_write(fp, "<summary><b>Image Preview</b></summary>")
                    _v260_write(fp, f"<img src='data:{_v260_escape(mime)};base64,{b64}' alt='Image preview for {_v260_escape(hdr)}' style='max-width:600px;max-height:600px;border:1px solid #aaa;margin-top:4px;' />")
                    _v260_write(fp, "</details>")
                else:
                    _v260_write(fp, f"<div class='warning'>Image preview skipped: {_v260_escape(len(raw_b))} bytes exceeds safety limit.</div>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Image preview failed: {_v260_escape(repr(e))}</div>")

    _v260_write(fp, "</div>")


def render_html(  # type: ignore[override]
    analysis: List[Dict[str, Any]],
    summary: Dict[str, Any],
    pdf_path: str,
    out_path: str,
    config: Optional[Dict[str, Any]] = None,
) -> None:
    """Render PDF X-Ray HTML report using safe, layout-preserving output."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)

    collapse = bool(cfg.get("collapse_objects_by_default", True))
    details_open = bool(cfg.get("details_default_open", False))
    show_decoded = bool(cfg.get("show_decoded_stream", True))
    show_text = bool(cfg.get("show_text_stream", True))
    show_images = bool(cfg.get("show_image_previews", True))
    try:
        max_preview = int(cfg.get("max_stream_preview_bytes") or 0)
    except Exception:
        max_preview = 0
    try:
        text_max_bytes = int(cfg.get("text_stream_max_bytes", 250000) or 250000)
    except Exception:
        text_max_bytes = 250000
    try:
        text_max_tokens = int(cfg.get("text_stream_max_tokens", 80000) or 80000)
    except Exception:
        text_max_tokens = 80000

    font_cmaps: Dict[str, Dict[int, str]] = {}
    font_warning = ""
    try:
        font_cmaps = _v260_build_font_cmap_lookup(analysis or [])
    except Exception as e:
        font_warning = f"Font/CMap lookup failed: {e!r}"

    hashes = _v270_file_hashes(pdf_path)
    report_date = _v270_report_date()
    title = f"PDF x-ray: {pdf_path}"

    try:
        import os as _os
        parent = _os.path.dirname(_os.path.abspath(out_path))
        if parent:
            _os.makedirs(parent, exist_ok=True)
    except Exception:
        pass

    # Build byte-position render sequence: prefix first, then objects and
    # classic xref/trailer chunks in original file order.
    sections = _v270_extract_nonobject_pdf_sections(pdf_path, analysis or [])
    entries: List[Tuple[int, str, Any]] = []
    for rec in analysis or []:
        off = rec.get("header_offset")
        try:
            off_i = int(off) if off is not None else 10**18
        except Exception:
            off_i = 10**18
        entries.append((off_i, "object", rec))
    for section in sections:
        try:
            off_i = int(section.get("offset") or 0)
        except Exception:
            off_i = 0
        entries.append((off_i, "section", section))
    entries.sort(key=lambda item: (item[0], 0 if item[1] == "object" else 1))

    with open(out_path, "w", encoding="utf-8", errors="replace") as fp:
        _v260_write(fp, _html_header(title))
        _v260_write(fp, "<h1>PDF X-Ray</h1>")
        _v260_write(fp, f"<p><b>Version:</b> {_v260_escape(cfg.get('app_version', 'unknown'))}</p>")
        _v260_write(fp, f"<p><b>Report generated:</b> {_v260_escape(report_date)}</p>")
        _v260_write(fp, f"<p><b>File:</b> {_v260_escape(pdf_path)}</p>")
        _v260_write(fp, f"<p><b>SHA-256:</b> {_v260_escape(hashes.get('sha256', ''))}</p>")
        _v260_write(fp, f"<p><b>MD5:</b> {_v260_escape(hashes.get('md5', ''))}</p>")
        if font_warning:
            _v260_write(fp, f"<div class='warning'>Warning: {_v260_escape(font_warning)}</div>")

        try:
            _v260_write(fp, _v270_summary_html(summary or {}))
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Summary failed: {_v260_escape(repr(e))}</div>")

        try:
            prefix = (summary or {}).get("prefix_text")
            if prefix:
                _v260_write(fp, "<h2>File header / pre-object bytes</h2>")
                if collapse:
                    _v260_write(fp, _v260_details(details_open))
                    _v260_write(fp, "<summary>Bytes before first indirect object</summary>")
                _v260_write(fp, f"<pre class='stream'>{_v260_escape(prefix)}</pre>")
                if collapse:
                    _v260_write(fp, "</details>")
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Prefix rendering failed: {_v260_escape(repr(e))}</div>")

        _v260_write(fp, f"<h2>PDF body / file-order objects and trailers ({len(analysis or [])} objects)</h2>")
        for _off, kind, payload in entries:
            if kind == "section":
                _v270_write_nonobject_section(fp, payload, collapse, details_open)
            else:
                try:
                    _v270_render_object(
                        fp,
                        payload,
                        cfg,
                        collapse,
                        details_open,
                        show_decoded,
                        show_text,
                        show_images,
                        max_preview,
                        text_max_bytes,
                        text_max_tokens,
                        font_cmaps,
                    )
                except Exception as e:
                    _v260_write(fp, f"<div class='obj'><div class='hdr'>Object render failure</div><div class='warning'>{_v260_escape(repr(e))}</div></div>")

        # Last-resort fallback: if no offset-aware section was found but the
        # parser supplied file_tail_text, render it as a file-structure section.
        # This keeps trailer information visible without placing it in Summary.
        try:
            if not sections and (summary or {}).get("file_tail_text"):
                _v270_write_nonobject_section(
                    fp,
                    {
                        "title": "File tail: classic xref / trailer",
                        "offset": None,
                        "text": (summary or {}).get("file_tail_text"),
                    },
                    collapse,
                    details_open,
                )
        except Exception as e:
            _v260_write(fp, f"<div class='warning'>Fallback file-tail render failed: {_v260_escape(repr(e))}</div>")

        _v260_write(fp, _html_footer())
