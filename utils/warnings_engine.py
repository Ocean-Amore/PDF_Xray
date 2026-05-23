"""
utils/warnings_engine.py

Generation-pattern warnings and other heuristic flags.
"""

from __future__ import annotations
from typing import Dict, Tuple, List, Any


def analyse_generations(
    objects: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[Tuple[int, int], List[str]]:
    """
    Return a mapping:
        (obj_num, gen_num) -> list of warnings

    This is intentionally simple; extend with your own rules.
    """
    warn = config.get("warn_strange_generations", True)
    results: Dict[Tuple[int, int], List[str]] = {}

    if not warn:
        return results

    for obj in objects:
        obj_num = obj.get("obj_num")
        gen_num = obj.get("gen_num")
        if obj_num is None or gen_num is None:
            continue

        key = (obj_num, gen_num)
        msgs: List[str] = []

        if gen_num > 5:
            msgs.append(f"Unusually high generation number ({gen_num}).")

        # You can add more heuristics here:
        # - repeated generations across many objects
        # - gaps in object numbering, etc.

        if msgs:
            results[key] = msgs

    return results

