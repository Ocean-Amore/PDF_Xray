"""
txt_renderer.py

Plain-text version of the x-ray output.

Version: 1.0
"""

from __future__ import annotations
from typing import List, Dict, Any


def render_txt(
    analysis: List[Dict[str, Any]],
    summary: Dict[str, Any],
    pdf_path: str,
    out_path: str,
    config: Dict[str, Any],
) -> None:
    lines: List[str] = []
    lines.append(f"PDF X-Ray: {pdf_path}")
    lines.append("=" * 80)
    lines.append("Summary:")
    lines.append(f"  Total objects: {summary.get('object_count')}")
    lines.append(f"  Image streams: {summary.get('image_stream_count')}")
    lines.append(f"  Incremental updates (approx): {summary.get('incremental_update_count')}")

    flags = summary.get("flags") or []
    if flags:
        lines.append("  Unusual patterns / heuristic flags:")
        for msg in flags:
            lines.append(f"    - {msg}")

    lines.append("")

    lines.append("Objects:")
    lines.append("")

    for rec in analysis:
        obj_num = rec.get("obj_num")
        gen_num = rec.get("gen_num")
        lines.append(f"{obj_num} {gen_num} obj")
        if rec.get("warnings"):
            lines.append("  Warnings:")
            for w in rec["warnings"]:
                lines.append(f"    - {w}")
        lines.append("  Raw object:")
        lines.append("  " + "-" * 70)
        for l in (rec.get("raw_object_text") or "").splitlines():
            lines.append("  " + l)
        lines.append("")

        stream_info = rec.get("stream_info", {})
        decoded_bytes = stream_info.get("decoded_bytes")
        if decoded_bytes is not None:
            lines.append("  Decoded stream:")
            lines.append("  " + "-" * 70)
            text_decoded = decoded_bytes.decode("latin-1", errors="replace")
            for l in text_decoded.splitlines():
                lines.append("  " + l)
            lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
