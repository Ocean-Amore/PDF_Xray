"""
utils/filters.py

Basic implementations for common PDF filters (skeleton).
"""

from __future__ import annotations
import zlib


def decode_flate(data: bytes) -> bytes:
    return zlib.decompress(data)


def decode_ascii_hex(data: bytes) -> bytes:
    """
    Very basic ASCIIHex decoder; not production-hard.
    """
    hex_chars = []
    for c in data.decode("latin-1", errors="ignore"):
        if c in " \t\r\n<>":
            continue
        if c == ">":
            break
        hex_chars.append(c)
    if len(hex_chars) % 2 == 1:
        hex_chars.append("0")
    hex_str = "".join(hex_chars)
    return bytes.fromhex(hex_str)


def decode_ascii85(data: bytes) -> bytes:
    """
    Placeholder for ASCII85 decoding.
    Implement fully later or reuse your existing implementation.
    """
    # TODO: implement ASCII85Decode
    return data


def decode_lzw(data: bytes) -> bytes:
    """
    Placeholder for LZWDecode.
    """
    # TODO: implement proper LZW decoding
    return data
