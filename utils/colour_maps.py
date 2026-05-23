"""
utils/colour_maps.py

Maps generation numbers to CSS classes for HTML styling.
"""

from __future__ import annotations


def gen_class_for_generation(gen_num: int | None) -> str:
    if gen_num is None:
        return ""
    if gen_num == 0:
        return "gen0"
    if gen_num > 5:
        return "gen_high"
    if gen_num % 2 == 1:
        return "gen_odd"
    return "gen0"
