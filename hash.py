"""
hash.py

Small hashing helper for PDF X-Ray reports.

Provides SHA-256 and MD5 digests for the input PDF so the HTML report can
record file identity without changing the main parsing workflow.
"""

from __future__ import annotations

import hashlib
from typing import Dict


def _digest_file(file_path: str, algorithm: str, chunk_size: int = 1024 * 1024) -> str:
    """Return the hexadecimal digest for *file_path* using *algorithm*."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_file(file_path: str) -> str:
    """Return the SHA-256 hash of *file_path*."""
    return _digest_file(file_path, "sha256")


def md5_file(file_path: str) -> str:
    """Return the MD5 hash of *file_path*.

    MD5 is included for compatibility/identification workflows only; SHA-256
    remains the preferred integrity hash.
    """
    return _digest_file(file_path, "md5")


def calculate_file_hashes(file_path: str) -> Dict[str, str]:
    """Return both SHA-256 and MD5 hashes for *file_path*."""
    return {
        "sha256": sha256_file(file_path),
        "md5": md5_file(file_path),
    }
