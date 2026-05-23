"""
cmap_parser.py

Parsing ToUnicode CMap streams (begincmap / beginbfchar / beginbfrange)
into a simple CID → Unicode-character mapping.

This is a lightweight transplant of the V32 CMap logic, adapted so the
HTML "Decoded Stream" section shows the *actual characters* rather than
their U+XXXX codes.
"""

from __future__ import annotations
from typing import Dict, Any
import re

# Global mapping of source CIDs → Unicode strings, aggregated from all
# ToUnicode CMaps encountered in the file. This will be used by the HTML
# renderer when decoding hex <...> Tj/TJ payloads.
GLOBAL_CMAP_MAPPING: Dict[int, str] = {}


def _parse_cmap_mappings(cmap_text: str) -> Dict[int, str]:
    """
    Parse beginbfrange / beginbfchar sections from a ToUnicode CMap.
    Returns a dict mapping source CIDs (ints) to Unicode strings.
    """

    mapping: Dict[int, str] = {}

    # -------------------------
    # beginbfrange blocks
    # -------------------------
    # We handle the simple, very common form:
    #   <start> <end> <dstStart>
    # and build a contiguous sequence of Unicode codepoints.
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap_text, flags=re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("%"):
                continue

            # Extract all hex angle-bracket tokens on the line
            # e.g. <0024> <0028> <0041>
            m = re.findall(r"<([0-9A-Fa-f]+)>", line)
            if len(m) == 3:
                start_hex, end_hex, dst_hex = m
                try:
                    start = int(start_hex, 16)
                    end = int(end_hex, 16)
                    dst_start = int(dst_hex, 16)
                except ValueError:
                    continue

                for offset, src in enumerate(range(start, end + 1)):
                    dst = dst_start + offset
                    try:
                        ch = chr(dst)
                    except ValueError:
                        ch = "?"
                    mapping[src] = ch

    # -------------------------
    # beginbfchar blocks
    # -------------------------
    # 1:1 mappings. Destination can be multiple 4-hex chunks, e.g.
    #   <0041> <0041>
    #   <00E9> <00E90041>
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap_text, flags=re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("%"):
                continue

            m = re.findall(r"<([0-9A-Fa-f]+)>", line)
            if len(m) >= 2:
                src_hex = m[0]
                dst_hex = m[1]
                try:
                    src = int(src_hex, 16)
                except ValueError:
                    continue

                chars = []
                # Destination may be multiple 4-digit codepoints concatenated.
                if len(dst_hex) % 4 == 0 and dst_hex:
                    for i in range(0, len(dst_hex), 4):
                        chunk = dst_hex[i:i+4]
                        try:
                            cp = int(chunk, 16)
                            chars.append(chr(cp))
                        except ValueError:
                            chars.append("?")
                else:
                    # Fallback if length is unexpected
                    chars.append("?")

                mapping[src] = "".join(chars)

    # IMPORTANT: aggregate into the global mapping so that other parts of the
    # system (e.g. the HTML renderer) can reuse the ToUnicode data when they
    # later encounter hex <...> text strings in content streams.
    GLOBAL_CMAP_MAPPING.update(mapping)

    return mapping

def _build_visible_table(mapping: Dict[int, str]) -> str:
    """
    Build a human-readable mapping table where the *right-hand side*
    is the actual Unicode character(s), not U+XXXX codes.
    """
    if not mapping:
        return ""

    lines: list[str] = []
    lines.append("% Decoded text from ToUnicode CMap (srcCID -> Unicode characters)")
    lines.append("% Each line shows the characters produced, not U+ codes.")
    lines.append("%")

    for src in sorted(mapping.keys()):
        dst = mapping[src] or ""

        if dst:
            # Replace control chars with "?" so the table stays readable.
            display = "".join(ch if ord(ch) >= 32 else "?" for ch in dst)
        else:
            display = "(none)"

        # Example line:
        #   <0024> -> A
        #   <004F> -> No
        lines.append(f"<{src:04X}> -> {display}")

    lines.append("%")
    lines.append("% This block describes the mapping only.")
    lines.append("% For actual text in reading order, see decoded BT/ET streams.")
    return "\n".join(lines)


def parse_cmap_stream(decoded_bytes: bytes) -> Dict[str, Any]:
    """
    Parse a decompressed ToUnicode CMap stream.

    Returns:
        {
            "mappings": {0x0024: "A", 0x0047: "d", ...},
            "raw_text": "<original CMap text>",
            "visible_table": "<ASCII table with actual characters>",
            "notes": "...",
        }
    """
    text = decoded_bytes.decode("latin-1", errors="replace")
    mapping = _parse_cmap_mappings(text)
    visible_table = _build_visible_table(mapping)

    return {
        "mappings": mapping,
        "raw_text": text,
        "visible_table": visible_table,
        "notes": (
            "Parsed ToUnicode CMap (beginbfchar/beginbfrange). "
            "visible_table shows actual characters, not U+ codes."
        ),
    }
