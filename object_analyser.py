"""
object_analyser.py

High-level analysis: combines objects + decoded streams + xref info
into a structure used by HTML/TXT renderers.
"""

from __future__ import annotations
from typing import List, Dict, Any

from stream_decoder import decode_stream
from utils.warnings_engine import analyse_generations


def analyse_objects(
    objects: List[Dict[str, Any]],
    xref_info: Dict[str, Any],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For each object, decode streams (if any) and attach analysis metadata.

    Returns a list of "analysis records", e.g.:
    {
        "obj_num": 1,
        "gen_num": 0,
        "header_offset": 1234,
        "dict_text": "...",
        "raw_object_text": "...",
        "stream_info": {decoded_bytes, stream_type, aux_info, errors},
        "warnings": [...],
    }
    """
    analysed: List[Dict[str, Any]] = []

    gen_warnings = analyse_generations(objects, config)

    for obj in objects:
        # NOTE: we now pass `objects` so decode_stream can resolve
        # indirect /Filter references like `/Filter 6 0 R`.
        stream_info = decode_stream(obj, obj.get("stream_bytes"), objects)

        record = {
            "obj_num": obj.get("obj_num"),
            "gen_num": obj.get("gen_num"),
            "header_offset": obj.get("header_offset"),
            "dict_text": obj.get("dict_text"),
            "raw_object_text": obj.get("raw_object_text"),
            "stream_info": stream_info,
            "warnings": [],
        }

        # Attach generation-pattern warnings if applicable
        gw = gen_warnings.get((record["obj_num"], record["gen_num"]))
        if gw:
            record["warnings"].extend(gw)

        analysed.append(record)

    return analysed
