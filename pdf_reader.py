"""
pdf_reader.py

Low-level PDF loading and very basic object extraction.

This is intentionally simple and conservative.
You can later replace `extract_objects()` with the more robust logic from
your existing Vxx stream parser (xref-aware object parsing, etc.).
"""

from __future__ import annotations
from typing import List, Dict


def load_pdf_bytes(pdf_path: str) -> bytes:
    with open(pdf_path, "rb") as f:
        return f.read()


def extract_objects(raw_pdf: bytes) -> List[Dict]:
    """
    VERY basic object extractor.

    Returns a list of dictionaries like:
    {
        "obj_num": 1,
        "gen_num": 0,
        "header_offset": int,
        "dict_text": "<< ... >>",
        "stream_bytes": b"...",   # or None
        "raw_object_text": "full textual representation",
    }

    This is a placeholder; you should later plug in your full object parser.
    """
    text = raw_pdf.decode("latin-1", errors="replace")

    objects: List[Dict] = []
    idx = 0
    length = len(text)

    while True:
        start = text.find(" obj", idx)
        if start == -1:
            break

        # Find the beginning of the object header line (number number obj)
        # Walk backwards to whitespace/newline.
        header_start = text.rfind("\n", 0, start)
        if header_start == -1:
            header_start = 0
        else:
            header_start += 1

        header = text[header_start:start + 4]  # includes " obj"
        try:
            parts = header.strip().split()
            obj_num = int(parts[0])
            gen_num = int(parts[1])
        except Exception:
            # Not a well-formed "n n obj" – skip
            idx = start + 4
            continue

        end_marker = "endobj"
        end = text.find(end_marker, start)
        if end == -1:
            break

        raw_obj = text[header_start:end + len(end_marker)]

        # Attempt to locate a stream
        stream_idx = raw_obj.find("stream")
        stream_bytes = None
        dict_text = None

        if stream_idx != -1:
            dict_text = raw_obj.split("stream", 1)[0].strip()
            # naive: assume 'endstream' appears before 'endobj'
            endstream_idx = raw_obj.find("endstream", stream_idx)
            if endstream_idx != -1:
                # locate in original bytes
                header_offset = raw_pdf.find(raw_obj.encode("latin-1", errors="replace"))
                if header_offset != -1:
                    stream_start = header_offset + raw_obj[:stream_idx].encode(
                        "latin-1", errors="replace"
                    ).__len__() + len("stream")

                    # Consume any whitespace after the 'stream' keyword:
                    #   space (32), tab (9), LF (10), FF (12), CR (13)
                    while stream_start < len(raw_pdf) and raw_pdf[stream_start] in (9, 10, 12, 13, 32):
                        stream_start += 1


                    stream_end = header_offset + raw_obj[:endstream_idx].encode(
                        "latin-1", errors="replace"
                    ).__len__()
                    stream_bytes = raw_pdf[stream_start:stream_end]
        else:
            # No stream, treat the whole object text (minus header/endobj) as dictionary/content
            dict_text = raw_obj

        objects.append(
            {
                "obj_num": obj_num,
                "gen_num": gen_num,
                "header_offset": raw_pdf.find(header.encode("latin-1", errors="replace")),
                "dict_text": dict_text,
                "stream_bytes": stream_bytes,
                "raw_object_text": raw_obj,
            }
        )

        idx = end + len(end_marker)

    return objects
