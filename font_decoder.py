"""
font_decoder.py

Lightweight decoder for embedded TrueType/OpenType font programs.

This is not a full TrueType engine – it only extracts the SFNT header and
table directory so that higher layers can render an examiner-friendly summary.
"""

from __future__ import annotations
from typing import Dict, Any, List
import struct
import re


def _decode_sfnt_version(raw: bytes) -> str:
    """
    Convert the 4-byte SFNT version field into something readable.
    """
    if raw in (b"\x00\x01\x00\x00", b"\x00\x00\x01\x00"):
        return "TrueType 1.0 (0x00010000)"
    try:
        # 'OTTO', 'true', 'typ1', 'ttcf', etc.
        txt = raw.decode("ascii")
        if txt.isprintable():
            return txt
    except Exception:
        pass
    return "0x" + raw.hex()


def parse_font_program(decoded_bytes: bytes, dict_text: str) -> Dict[str, Any]:
    """
    Parse an embedded SFNT font program (TrueType / OpenType) enough to
    describe the subset in human terms.

    Returns a dictionary such as:

        {
            "sfnt_version": "OTTO",
            "table_count": 11,
            "tables": [
                {"tag": "cmap", "checksum": 0x..., "offset": 14920, "length": 154},
                ...
            ],
            "base_font": "ACLPFL+ArialMT",
            "notes": "Subset detected (ACLPFL+ prefix).",
        }

    The raw binary is *not* returned here – the caller already has it and
    will build a textual summary using this metadata.
    """
    info: Dict[str, Any] = {
        "sfnt_version": None,
        "table_count": None,
        "tables": [],  # type: ignore[list-item]
        "base_font": None,
        "notes": "",
    }

    if not isinstance(decoded_bytes, (bytes, bytearray)) or len(decoded_bytes) < 12:
        info["notes"] = "Font program too short to contain an SFNT header."
        return info

    # Try to identify the base font name from the object dictionary.
    m = re.search(r"/(?:BaseFont|FontName)\s+/([^\s>]+)", dict_text or "")
    if m:
        info["base_font"] = m.group(1)

    try:
        sfnt_raw = decoded_bytes[0:4]
        info["sfnt_version"] = _decode_sfnt_version(sfnt_raw)

        num_tables = struct.unpack(">H", decoded_bytes[4:6])[0]
        info["table_count"] = int(num_tables)

        tables: List[Dict[str, Any]] = []
        offset = 12
        for i in range(min(num_tables, 64)):  # hard cap for safety
            if offset + 16 > len(decoded_bytes):
                break
            tag = decoded_bytes[offset: offset + 4].decode("ascii", "replace")
            checksum, t_offset, length = struct.unpack(
                ">III", decoded_bytes[offset + 4: offset + 16]
            )
            offset += 16
            tables.append(
                {
                    "tag": tag,
                    "checksum": int(checksum),
                    "offset": int(t_offset),
                    "length": int(length),
                }
            )

        info["tables"] = tables

        # Simple note about subset prefix.
        if info["base_font"] and "+" in str(info["base_font"]):
            prefix = str(info["base_font"]).split("+", 1)[0]
            info["notes"] = f"Likely subset font (prefix '{prefix}+')."
        else:
            info["notes"] = "Embedded font program (full or subset)."

    except Exception as e:  # very defensive
        info["notes"] = f"Font parsing error: {e!r}"

    return info
