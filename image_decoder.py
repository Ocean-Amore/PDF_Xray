"""
image_decoder.py

Image decoding and preview generation for PDF Xray.

This module is intentionally self-contained so it can be called safely from
stream_decoder without introducing circular imports.

It focuses on:
  * Extracting basic image parameters from the object dictionary
  * Handling FlateDecode image data that uses /DecodeParms /Predictor
    (PNG and TIFF-style predictors)
  * Handling FlateDecode + RunLengthDecode image masks (e.g. RA50003587.PDF)
  * Detecting native image formats (JPEG, PNG, JP2, TIFF)
  * Optionally wrapping raw RGB/Gray pixels into a minimal PNG so that
    callers can embed an image preview in HTML.

Version: 1.2
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import re
import zlib
import struct
import binascii


# ---------------------------------------------------------------------------
# Small helpers for pulling values out of the object dictionary text
# ---------------------------------------------------------------------------


def _extract_int(dict_text: str, key: str, default: Optional[int] = None) -> Optional[int]:
    """
    Extract an integer value: /Key 123
    """
    if not dict_text:
        return default
    m = re.search(rf"/{re.escape(key)}\s+(-?\d+)", dict_text)
    if not m:
        return default
    try:
        return int(m.group(1))
    except ValueError:
        return default


def _extract_name(dict_text: str, key: str) -> Optional[str]:
    """
    Extract a name value: /Key /DeviceRGB
    Returns the full name including leading '/' if present.
    """
    if not dict_text:
        return None
    m = re.search(rf"/{re.escape(key)}\s+(/[\w#+\.\-]+)", dict_text)
    if not m:
        return None
    return m.group(1)


def _extract_bool(dict_text: str, key: str) -> bool:
    """
    Extract a boolean: /Key true|false
    """
    if not dict_text:
        return False
    m = re.search(rf"/{re.escape(key)}\s+(true|false)", dict_text)
    if not m:
        return False
    return m.group(1) == "true"


def _extract_filters(dict_text: str) -> List[str]:
    """
    Extract /Filter entries from the dictionary.

    Handles:
        /Filter /FlateDecode
        /Filter [/FlateDecode /RunLengthDecode]

    NOTE: we deliberately ignore forms like `/Filter 6 0 R` here because
    resolving indirect filters is the responsibility of the main stream decoder.
    """
    if not dict_text:
        return []

    # Array form: /Filter [ /FlateDecode /RunLengthDecode ]
    m = re.search(r"/Filter\s+\[([^\]]+)\]", dict_text)
    if m:
        return re.findall(r"/[\w#+\.\-]+", m.group(1))

    # Single name: /Filter /FlateDecode
    m = re.search(r"/Filter\s+(/[\w#+\.\-]+)", dict_text)
    if m:
        return [m.group(1)]

    return []


def _extract_predictor(dict_text: str) -> int:
    """
    Extract /Predictor from /DecodeParms.
    If not present, returns 1 (no prediction).
    """
    if not dict_text:
        return 1
    m = re.search(r"/Predictor\s+(\d+)", dict_text)
    if not m:
        return 1
    try:
        return int(m.group(1))
    except ValueError:
        return 1


def _extract_columns(dict_text: str) -> Optional[int]:
    """
    Extract /Columns from /DecodeParms.
    """
    if not dict_text:
        return None
    m = re.search(r"/Columns\s+(\d+)", dict_text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Filter / format helpers
# ---------------------------------------------------------------------------


def _detect_file_format(data: bytes) -> Optional[str]:
    """
    Very small signature-based detection for common image formats.

    Returns one of: "jpeg", "png", "jp2", "tiff", or None.
    """
    if not data or len(data) < 4:
        return None

    sig2 = data[:2]
    sig4 = data[:4]
    sig8 = data[:8]

    # JPEG
    if sig2 == b"\xFF\xD8":
        return "jpeg"

    # PNG
    if sig8 == b"\x89PNG\r\n\x1a\n":
        return "png"

    # JPEG2000 / JP2 (very rough)
    if sig4 == b"\x00\x00\x00\x0c" and data[4:8] in (b"jP  ", b"jP2 "):
        return "jp2"

    # TIFF (very rough)
    if sig4 in (b"II*\x00", b"MM\x00*"):
        return "tiff"

    return None


def _compute_expected_lengths(
    width: Optional[int],
    height: Optional[int],
    components: int,
    bpc: int,
    predictor: int,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Compute the expected raw and predictor-encoded data lengths.

    raw_len:
        bytes for plain raw pixels (no predictor)
    pred_len:
        bytes for PNG-style predictor where each row is prefixed by 1 filter byte
    """
    if not (width and height and components and bpc):
        return None, None

    row_bits = width * components * bpc
    row_bytes_raw = (row_bits + 7) // 8
    raw_len = row_bytes_raw * height

    if predictor and predictor > 1:
        pred_len = (row_bytes_raw + 1) * height
    else:
        pred_len = None

    return raw_len, pred_len


def run_length_decode(data: bytes) -> bytes:
    """
    PDF RunLengthDecode implementation.

    The data consists of "runs":
      - 0–127   : literal run of length + 1 bytes
      - 129–255 : repeat next single byte (257 - length) times
      - 128     : EOD marker
    """
    out = bytearray()
    i = 0
    n = len(data)

    while i < n:
        b = data[i]
        i += 1

        if b == 128:
            # End of data
            break

        if b < 128:
            run_length = b + 1
            out.extend(data[i : i + run_length])
            i += run_length
        else:
            run_length = 257 - b
            if i >= n:
                break
            out.extend([data[i]] * run_length)
            i += 1

    return bytes(out)


def _apply_png_tiff_predictor(
    encoded: bytes,
    width: Optional[int],
    height: Optional[int],
    components: int,
    bpc: int,
    predictor: int,
    columns: Optional[int],
) -> bytes:
    """
    Apply PNG/TIFF-style predictors to 8-bit sample data.

    Supports:
      * Predictor = 2   (TIFF, horizontal differencing)
      * Predictor >= 10 (PNG filters with per-row filter byte)
    """
    # For now we limit predictor handling to simple, 8-bpc data
    if bpc != 8 or not width or not height or not components:
        return encoded

    if not columns:
        columns = width

    row_samples = columns * components
    row_bytes = row_samples  # 8-bit samples

    # TIFF predictor 2: horizontal differencing
    if predictor == 2:
        out = bytearray(encoded)
        for row in range(height):
            row_start = row * row_bytes
            for i in range(components, row_bytes):
                out[row_start + i] = (
                    out[row_start + i] + out[row_start + i - components]
                ) & 0xFF
        return bytes(out)

    # PNG predictors 10–15: each row begins with a filter byte
    if predictor >= 10:
        out = bytearray()
        i = 0
        data_len = len(encoded)
        prev_row = b"\x00" * row_bytes

        while i < data_len:
            if i >= data_len:
                break

            filter_type = encoded[i]
            i += 1

            row = bytearray(encoded[i : i + row_bytes])
            i += row_bytes

            if len(row) < row_bytes:
                # truncated
                break

            if filter_type == 0:
                # None
                pass

            elif filter_type == 1:
                # Sub
                for x in range(row_bytes):
                    left = row[x - 1] if x >= components else 0
                    row[x] = (row[x] + left) & 0xFF

            elif filter_type == 2:
                # Up
                for x in range(row_bytes):
                    row[x] = (row[x] + prev_row[x]) & 0xFF

            elif filter_type == 3:
                # Average
                for x in range(row_bytes):
                    left = row[x - 1] if x >= components else 0
                    up = prev_row[x]
                    row[x] = (row[x] + ((left + up) // 2)) & 0xFF

            elif filter_type == 4:
                # Paeth
                for x in range(row_bytes):
                    a = row[x - 1] if x >= components else 0
                    b = prev_row[x]
                    c = prev_row[x - 1] if x >= components else 0
                    p = a + b - c
                    pa = abs(p - a)
                    pb = abs(p - b)
                    pc = abs(p - c)
                    if pa <= pb and pa <= pc:
                        pr = a
                    elif pb <= pc:
                        pr = b
                    else:
                        pr = c
                    row[x] = (row[x] + pr) & 0xFF

            # Other filter types are treated as "None"
            out.extend(row)
            prev_row = bytes(row)

        return bytes(out)

    return encoded


def _wrap_raw_pixels_to_png(
    raw: bytes,
    width: int,
    height: int,
    color_space: str,
    bpc: int,
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Wrap raw pixel bytes into a tiny PNG.

    Supports grayscale (DeviceGray) and RGB (DeviceRGB) with 1 or 8 bits per
    component. CMYK is not currently converted.
    """
    if not raw or not width or not height:
        return None, "Missing raw image data or dimensions."

    cs = (color_space or "").lstrip("/")
    cs_upper = cs.upper()

    if cs_upper in ("DEVICERGB", "RGB"):
        components = 3
        colour_type = 2  # truecolour
    elif cs_upper in ("DEVICEGRAY", "DEVICEGREY", "G", ""):
        components = 1
        colour_type = 0  # grayscale
    elif cs_upper in ("DEVICECMYK", "CMYK"):
        # CMYK → RGB requires proper colour conversion; skip for now.
        return None, "CMYK images are not wrapped to PNG (preview not implemented)."
    else:
        # Fallback: treat as grayscale
        components = 1
        colour_type = 0

    if bpc not in (1, 8):
        return None, f"PNG preview: unsupported bits per component {bpc}; only 1 or 8 are handled."

    if bpc == 1:
        row_bytes_raw = (width * components + 7) // 8
    else:
        row_bytes_raw = width * components

    expected_len = row_bytes_raw * height
    if len(raw) != expected_len:
        return None, (
            f"Raw data length {len(raw)} does not match expected {expected_len} "
            f"for {width}x{height}, {components} comps, {bpc} bpc."
        )

    # Build PNG scanlines: one filter byte (0) per row + raw data
    scanlines = bytearray()
    idx = 0
    for _ in range(height):
        scanlines.append(0)  # filter type 0 (None)
        scanlines.extend(raw[idx : idx + row_bytes_raw])
        idx += row_bytes_raw

    compressed = zlib.compress(bytes(scanlines))

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", binascii.crc32(tag + payload) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    bit_depth = 1 if bpc == 1 else 8

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, colour_type, 0, 0, 0)

    png_bytes = sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b"")
    return png_bytes, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def decode_image_stream(decoded_bytes: Optional[bytes], dict_text: str) -> Dict[str, Any]:
    """
    Best-effort image decoder.

    Parameters
    ----------
    decoded_bytes:
        The bytes after the main stream filters have been applied by stream_decoder.
        For some tricky cases (e.g. /Filter 6 0 R with Flate+RunLength), this may
        still be compressed – we try to recover where possible.
    dict_text:
        The raw object dictionary text (starting at "<<", ending at ">>").

    Returns
    -------
    Dict with:
      - width, height, color_space, bits_per_component
      - png_bytes: PNG-encoded preview (where possible)
      - image_bytes / image_mime: native encoded image (JPEG/JPX/TIFF/etc) if available
      - notes: human-readable summary of what was done
      - predictor: predictor value when applicable
      - filters: list of declared filters (if any)
    """
    if dict_text is None:
        dict_text = ""

    notes_parts: List[str] = []

    # Basic metadata
    width = _extract_int(dict_text, "Width")
    height = _extract_int(dict_text, "Height")
    bpc = _extract_int(dict_text, "BitsPerComponent", 8) or 8
    color_space = _extract_name(dict_text, "ColorSpace") or ""
    image_mask = _extract_bool(dict_text, "ImageMask")
    filters = _extract_filters(dict_text)
    predictor = _extract_predictor(dict_text)
    columns = _extract_columns(dict_text)

    if image_mask and not color_space:
        # Image masks are always 1-bit; treat as DeviceGray
        color_space = "/DeviceGray"
        if bpc == 0:
            bpc = 1

    cs_upper = color_space.lstrip("/").upper()
    if cs_upper in ("DEVICERGB", "RGB"):
        components = 3
    elif cs_upper in ("DEVICECMYK", "CMYK"):
        components = 4
    else:
        components = 1

    notes_parts.append(f"Filters: {', '.join(filters) if filters else 'none/unknown'}")
    if predictor and predictor > 1:
        notes_parts.append(f"Predictor={predictor}")

    # If we weren't given any bytes, just return the metadata
    if decoded_bytes is None:
        notes_parts.append("No decoded bytes were provided for this image stream.")
        return {
            "width": width,
            "height": height,
            "color_space": color_space,
            "bits_per_component": bpc,
            "png_bytes": None,
            "image_bytes": None,
            "image_mime": None,
            "notes": " ".join(notes_parts),
            "predictor": predictor if predictor and predictor > 1 else None,
            "filters": filters,
        }

    data = decoded_bytes

    # 1) If the data already looks like a standard image format (JPEG, PNG, JP2, TIFF),
    #    keep it as-is so the HTML renderer can embed it directly.
    fmt = _detect_file_format(data)
    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None
    png_bytes: Optional[bytes] = None

    if fmt is not None:
        image_bytes = data
        if fmt == "jpeg":
            image_mime = "image/jpeg"
        elif fmt == "png":
            image_mime = "image/png"
        elif fmt == "jp2":
            image_mime = "image/jp2"
        elif fmt == "tiff":
            image_mime = "image/tiff"

        notes_parts.append(
            f"Native {fmt.upper()} image stream detected; using bytes as-is for preview."
        )

        # If it's already PNG, also expose it via png_bytes for backwards compatibility.
        if fmt == "png":
            png_bytes = data

    else:
        # 2) Flate / RunLength / Predictor handling for raw pixel images.
        raw_len_expected, _pred_len_expected = _compute_expected_lengths(
            width, height, components, bpc, predictor
        )

        stage = data
        used_flate = False

        # Heuristic: data looks like zlib OR /FlateDecode is declared.
        looks_zlib = (
            len(data) >= 2
            and data[0] == 0x78
            and data[1] in (0x01, 0x5E, 0x9C, 0xDA)
        )

        if looks_zlib or any("FlateDecode" in f for f in filters):
            try:
                stage = zlib.decompress(data)
                used_flate = True
                notes_parts.append("Applied Flate decompression in image decoder.")
            except Exception:
                notes_parts.append(
                    "Flate decompression in image decoder failed; treating bytes as raw."
                )
                stage = data

        # If RunLength is declared (including via indirect filter references resolved
        # in the main decoder) OR if the Flate output is obviously smaller than the
        # expected raw size, try a RunLengthDecode pass and prefer the result if it
        # matches/dominates the expected size.
        use_runlength = any("RunLengthDecode" in f for f in filters)
        if used_flate and raw_len_expected and len(stage) != raw_len_expected:
            use_runlength = True

        if use_runlength:
            try:
                rl = run_length_decode(stage)
                if raw_len_expected and len(rl) == raw_len_expected:
                    stage = rl
                    notes_parts.append(
                        "Applied RunLengthDecode in image decoder (size matches expected raw length)."
                    )
                else:
                    # If both lengths are known, pick the one closer to expected.
                    if raw_len_expected and abs(len(rl) - raw_len_expected) < abs(
                        len(stage) - raw_len_expected
                    ):
                        stage = rl
                        notes_parts.append(
                            "Applied RunLengthDecode; chose RL output as closer to expected size."
                        )
                    else:
                        notes_parts.append(
                            "RunLengthDecode was attempted but retained previous stage (size mismatch)."
                        )
            except Exception:
                notes_parts.append(
                    "RunLengthDecode in image decoder failed; using previous stage."
                )

        # Optional PNG/TIFF predictors for 8-bit data
        if predictor and predictor > 1 and width and height and components and bpc == 8:
            before_len = len(stage)
            stage = _apply_png_tiff_predictor(
                stage, width, height, components, bpc, predictor, columns
            )
            if len(stage) != before_len:
                notes_parts.append(
                    f"Applied PNG/TIFF predictor decoding (predictor={predictor}, "
                    f"columns={columns or width})."
                )

        raw_pixels = stage

        # Wrap raw pixels into PNG for preview
        png_bytes, wrap_err = _wrap_raw_pixels_to_png(
            raw_pixels, width or 0, height or 0, color_space or "", bpc
        )
        if png_bytes is not None:
            notes_parts.append("Wrapped raw image pixels into a minimal PNG for preview.")
        else:
            notes_parts.append(
                wrap_err
                or "Could not wrap raw pixels into PNG (unsupported colour space/BPC or size mismatch)."
            )

    # Backwards compatibility: if caller only knows about png_bytes, still give them
    # something when we produced a PNG.
    if image_bytes is None and png_bytes is not None:
        image_bytes = png_bytes
        image_mime = "image/png"

    notes = " ".join(notes_parts) if notes_parts else "Image decoding completed."

    return {
        "width": width,
        "height": height,
        "color_space": color_space,
        "bits_per_component": bpc,
        "png_bytes": png_bytes,
        "image_bytes": image_bytes,
        "image_mime": image_mime,
        "notes": notes,
        "predictor": predictor if predictor and predictor > 1 else None,
        "filters": filters,
    }
