"""
reporting.py

Builds overall summary blocks (e.g. incremental updates, counts per type).

Version: 1.1
"""

from __future__ import annotations
from typing import List, Dict, Any


def build_summary(
    analysis: List[Dict[str, Any]],
    xref_info: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Summarise key findings for rendering at the top of the report.

    Includes:
      - total object count
      - number of image streams
      - approximate incremental update count from xref_info
      - heuristic "flags" for unusual patterns (generations, xref layout, etc.)
    """
    obj_count = len(analysis)
    img_count = sum(
        1
        for a in analysis
        if a.get("stream_info", {}).get("stream_type") == "image"
    )

    inc_updates = xref_info.get("incremental_update_count", 0)

    flags: List[str] = []

    # 1) Incremental updates
    if inc_updates > 0:
        flags.append(
            f"PDF shows signs of incremental updates: approximately "
            f"{inc_updates} additional revision(s) beyond the original."
        )

    # 2) XRef layout statistics (classic tables vs streams)
    xref_tables = xref_info.get("xref_table_count")
    xref_streams = xref_info.get("xref_stream_count")
    if (xref_tables is not None) or (xref_streams is not None):
        flags.append(
            f"XRef layout: {xref_tables or 0} classic table(s), "
            f"{xref_streams or 0} stream-based xref section(s)."
        )

    # 3) Generation-number patterns / anomalies
    gens = [a.get("gen_num", 0) for a in analysis if a.get("gen_num") is not None]
    if gens:
        max_gen = max(gens)
        if max_gen > 0:
            flags.append(
                f"Non-zero generation numbers present (max generation = {max_gen})."
            )

        # Unusually high gens (>5 is the same threshold as warnings_engine)
        high_gen_objs = [
            (a.get("obj_num"), a.get("gen_num"))
            for a in analysis
            if (a.get("gen_num") or 0) > 5
        ]
        if high_gen_objs:
            sample = ", ".join(
                f"{obj} {gen} obj" for obj, gen in high_gen_objs[:5]
            )
            flags.append(
                f"Unusually high generation numbers (>5) on "
                f"{len(high_gen_objs)} object(s), e.g. {sample}."
            )

        # Objects seen in multiple generations (e.g. 12 0 / 12 2 / 12 5)
        by_obj: Dict[int, set] = {}
        for a in analysis:
            obj = a.get("obj_num")
            gen = a.get("gen_num", 0)
            if obj is None:
                continue
            by_obj.setdefault(obj, set()).add(gen)

        multi_gen = {obj: sorted(gset) for obj, gset in by_obj.items() if len(gset) > 1}
        if multi_gen:
            sample_items = list(multi_gen.items())[:5]
            sample_txt = ", ".join(f"{obj}: gens {gens}" for obj, gens in sample_items)
            flags.append(
                f"{len(multi_gen)} object number(s) appear in multiple generations "
                f"(possible incremental edits), e.g. {sample_txt}."
            )

        # Build base summary dictionary
    summary: Dict[str, Any] = {
        "object_count": obj_count,
        "image_stream_count": img_count,
        "incremental_update_count": inc_updates,
        "flags": flags,
        "notes": "Summary with heuristic incremental update and generation analysis.",
    }

    # Pass through some low-level xref/trailer details so renderers can show them
    for key in (
        "prefix_text",
        "xref_sections",
        "trailers",
        "xref_table_count",
        "xref_stream_count",
        "startxref_count",
        "trailer_keyword_count",
        "file_tail_text",
    ):
        if key in xref_info:
            summary[key] = xref_info[key]

    return summary


