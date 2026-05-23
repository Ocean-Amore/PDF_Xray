"""
xref_parser.py

Heuristic parsing of xref tables/streams and trailer dictionaries.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional
import re


def _extract_prefix_text(raw_pdf: bytes, objects: Optional[List[Dict[str, Any]]]) -> str:
    """
    Return all bytes *before* the first parsed indirect object as a latin-1 string.

    This covers the PDF header, initial comments, and any other pre-object
    data that may be forensically interesting.
    """
    text = raw_pdf.decode("latin-1", errors="replace")

    # Prefer object header offsets if we have them
    if objects:
        offsets = [
            o.get("header_offset")
            for o in objects
            if isinstance(o.get("header_offset"), int)
            and o.get("header_offset", -1) >= 0
        ]
        if offsets:
            first = min(offsets)
            if first > 0:
                return text[:first]

    # Fallback: look for the first "n n obj" header pattern
    m = re.search(r"\n\d+\s+\d+\s+obj", text)
    if m and m.start() > 0:
        return text[:m.start()]

    return ""


def _extract_xref_and_trailers(text: str) -> Dict[str, List[str]]:
    """
    Very lightweight extraction of classic xref tables and their trailer blocks.

    Returns:
        {
            "xref_sections": ["xref ... trailer ... startxref ...", ...],
            "trailers": ["trailer << ... >>", ...],
        }
    """
    xref_sections: List[str] = []
    trailers: List[str] = []

    n = len(text)
    idx = 0

    while True:
        pos = text.find("xref", idx)
        if pos == -1:
            break

        # Skip /Type /XRef (handled as an object stream elsewhere)
        if pos > 0 and text[pos - 1] == "/":
            idx = pos + 4
            continue

        # Heuristic end of this xref section: before the next xref/startxref/%%EOF
        end_candidates: List[int] = []

        next_xref = text.find("\nxref", pos + 4)
        if next_xref != -1:
            end_candidates.append(next_xref)

        next_startxref = text.find("startxref", pos + 4)
        if next_startxref != -1:
            end_candidates.append(next_startxref)

        next_eof = text.find("%%EOF", pos + 4)
        if next_eof != -1:
            end_candidates.append(next_eof)

        end = min(end_candidates) if end_candidates else n

        chunk = text[pos:end].strip()
        if chunk:
            xref_sections.append(chunk)

            # Look for one or more trailer blocks inside this chunk
            t_idx = 0
            while True:
                t_pos = chunk.find("trailer", t_idx)
                if t_pos == -1:
                    break

                # From 'trailer' up to the next control keyword or end of chunk
                t_end_candidates: List[int] = []
                for token in ("startxref", "xref", "%%EOF"):
                    c = chunk.find(token, t_pos + 7)
                    if c != -1:
                        t_end_candidates.append(c)
                t_end = min(t_end_candidates) if t_end_candidates else len(chunk)

                t_block = chunk[t_pos:t_end].strip()
                if t_block and t_block not in trailers:
                    trailers.append(t_block)

                t_idx = t_pos + 7

        idx = pos + 4

    # Fallback: capture any stray 'trailer' blocks not associated with an 'xref'
    t_idx = 0
    while True:
        t_pos = text.find("trailer", t_idx)
        if t_pos == -1:
            break

        # Skip trailers that are already inside the captured xref sections
        already_covered = any(
            (section_pos := text.find(snippet)) != -1
            and section_pos <= t_pos < section_pos + len(snippet)
            for snippet in xref_sections
        )
        if already_covered:
            t_idx = t_pos + 7
            continue

        t_end_candidates: List[int] = []
        for token in ("startxref", "xref", "%%EOF"):
            c = text.find(token, t_pos + 7)
            if c != -1:
                t_end_candidates.append(c)
        t_end = min(t_end_candidates) if t_end_candidates else n

        t_block = text[t_pos:t_end].strip()
        if t_block and t_block not in trailers:
            trailers.append(t_block)

        t_idx = t_pos + 7

    return {"xref_sections": xref_sections, "trailers": trailers}




def _extract_file_tail_text(raw_pdf: bytes, objects: Optional[List[Dict[str, Any]]]) -> str:
    """
    Return the raw bytes from the end of the last parsed indirect object to EOF.

    This preserves the classic xref / trailer / startxref / %%EOF tail so it
    can be displayed together with the final real object in the report.
    """
    text = raw_pdf.decode("latin-1", errors="replace")

    if objects:
        end_positions: List[int] = []
        for o in objects:
            raw_obj = o.get("raw_object_text")
            header_offset = o.get("header_offset")
            if isinstance(raw_obj, str) and isinstance(header_offset, int) and header_offset >= 0:
                end_positions.append(header_offset + len(raw_obj))
        if end_positions:
            last_end = max(end_positions)
            if 0 <= last_end < len(text):
                return text[last_end:].lstrip("\r\n")

    matches = list(re.finditer(r"endobj", text))
    if matches:
        return text[matches[-1].end():].lstrip("\r\n")

    return ""

def parse_xref_and_trailers(
    raw_pdf: bytes,
    objects: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Lightweight, heuristic summary of xref / trailer layout plus
    the pre-object header region.

    The returned dictionary is designed to be passed straight through into
    the summary and HTML renderers.
    """
    text = raw_pdf.decode("latin-1", errors="replace")

    # Simple keyword-based statistics
    xref_table_count = text.count("\nxref")
    # Stream-based xref sections are exposed as /Type /XRef in object dictionaries
    xref_stream_count = text.count("/Type /XRef")
    startxref_count = text.count("startxref")
    trailer_kw_count = text.count("trailer")

    total_xref_like = xref_table_count + xref_stream_count
    incremental_updates = max(total_xref_like - 1, 0)

    # Structural extraction
    prefix_text = _extract_prefix_text(raw_pdf, objects)
    xref_struct = _extract_xref_and_trailers(text)
    file_tail_text = _extract_file_tail_text(raw_pdf, objects)

    return {
        # Structural snippets
        "prefix_text": prefix_text,
        "xref_sections": xref_struct.get("xref_sections", []),
        "file_tail_text": file_tail_text,
        "trailers": xref_struct.get("trailers", []),

        # Heuristic counts / stats
        "incremental_update_count": incremental_updates,
        "xref_table_count": xref_table_count,
        "xref_stream_count": xref_stream_count,
        "startxref_count": startxref_count,
        "trailer_keyword_count": trailer_kw_count,
        "notes": "Heuristic xref/trailer summary with prefix capture.",
    }
