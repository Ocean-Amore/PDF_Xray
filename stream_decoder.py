"""
stream_decoder.py

Central dispatch for decoding PDF streams based on /Filter entries.

Version: 1.1
"""

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple, List
import re
import struct
from datetime import datetime

from utils.filters import (
    decode_flate,
    decode_ascii_hex,
    decode_ascii85,
    decode_lzw,
)
from image_decoder import decode_image_stream
from cmap_parser import parse_cmap_stream
from font_decoder import parse_font_program


def decode_stream(
    obj_meta: Dict[str, Any],
    stream_bytes: Optional[bytes],
    all_objects: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Returns a dictionary:
    {
        "decoded_bytes": bytes or None,
        "stream_type": str (e.g. "image", "cmap", "font_program", "generic"),
        "aux_info": dict,   # e.g. parsed CMap, font tables, image dimensions
        "errors": list[str],
    }

    `all_objects` is an optional list of all parsed objects, used to resolve
    indirect /Filter references such as `/Filter 6 0 R` where 6 0 obj is
    something like `[/FlateDecode/RunLengthDecode]`.
    """
    result = {
        "decoded_bytes": None,
        "stream_type": "unknown",
        "aux_info": {},
        "errors": [],
    }

    if stream_bytes is None:
        return result

    dict_text = obj_meta.get("dict_text") or ""

    # First try direct /Filter forms (/Filter /FlateDecode, /Filter [ ... ]).
    filters = _extract_filters(dict_text)

    # If nothing was found, look for indirect `/Filter N 0 R` and resolve it
    # via the other objects, e.g. 6 0 obj: [/FlateDecode/RunLengthDecode].
    if not filters and all_objects:
        filters = _resolve_indirect_filters(dict_text, all_objects)

    try:
        decoded = _apply_filters(stream_bytes, filters)
    except Exception as e:  # pragma: no cover (generic catch)
        result["errors"].append(f"Filter decode error: {e!r}")
        return result

        # At this point, `decoded` is the raw stream content after the filter
    # pipeline (Flate, RunLength, etc.).
    result["decoded_bytes"] = decoded

    # Dispatch to specialised handlers where possible.
    if _looks_like_image_object(dict_text, decoded):
        # Image XObject or image-like stream: decode for preview and build a
        # human-readable summary. This also catches native image streams whose
        # dictionaries omit /Subtype /Image but still expose classic image keys
        # such as /Width, /Height, /BitsPerComponent, /ColorSpace and /Filter.
        result["stream_type"] = "image"

        img_info = decode_image_stream(decoded, dict_text)
        result["aux_info"]["image"] = img_info

        # Build an examiner-friendly textual summary instead of dumping raw pixels/JPEG.
        try:
            summary_lines: list[str] = []
            summary_lines.append("[Image XObject stream]")

            if isinstance(img_info, dict):
                width = img_info.get("width")
                height = img_info.get("height")
                if width is not None and height is not None:
                    summary_lines.append(f"Dimensions: {width} x {height} px")

                color_space = img_info.get("color_space")
                if color_space:
                    summary_lines.append(f"Color space: {color_space}")

                bpc = img_info.get("bits_per_component")
                if bpc is not None:
                    summary_lines.append(f"Bits per component: {bpc}")

                mime = img_info.get("image_mime")
                if mime:
                    summary_lines.append(f"Detected image format: {mime}")

                # Filters from the image decoder (if available)
                filters_from_decoder = img_info.get("filters")
                if filters_from_decoder:
                    summary_lines.append(
                        "Filters (from dictionary): "
                        + ", ".join(str(f) for f in filters_from_decoder)
                    )

                predictor = img_info.get("predictor")
                if predictor:
                    summary_lines.append(f"Predictor: {predictor}")

                notes = img_info.get("notes")
                if notes:
                    summary_lines.append(f"Notes: {notes}")

            if len(summary_lines) == 1:
                # We didn't manage to pull any metadata
                summary_lines.append("(No additional image metadata available.)")

            result["decoded_bytes"] = (
                "\n".join(summary_lines) + "\n"
            ).encode("utf-8", errors="replace")

        except Exception as e:
            # If something goes wrong building the summary, record the error and
            # leave the raw decoded bytes in place as a fallback.
            result["errors"].append(f"Image summary error: {e!r}")

    elif (
        "/Type /Font" in dict_text
        or "/FontFile" in dict_text
        or "/Length1" in dict_text  # typical for embedded font programs
        or _looks_like_font_program(decoded)
    ):
        # Embedded TrueType / OpenType font program
        result["stream_type"] = "font_program"

        # Start with this object's dictionary text
        font_dict_text = dict_text

        # If this looks like a bare FontFile stream (like 24 0 obj) with no
        # /FontName, try to pull a richer dictionary from the referencing
        # /FontDescriptor (e.g. 25 0 obj with /FontName/RJWWCL+Tahoma).
        descriptor_text = _find_font_descriptor_dict(obj_meta, all_objects)
        if descriptor_text:
            font_dict_text = descriptor_text

        font_info = parse_font_program(decoded, font_dict_text)
        result["aux_info"]["font"] = font_info

        # Replace raw binary with a concise human-readable summary so the
        # HTML "Decoded Stream" block is examiner-friendly.
        summary_bytes = _render_font_program_summary(font_info, font_dict_text)
        if summary_bytes is not None:
            result["decoded_bytes"] = summary_bytes


    elif "/Type /XRef" in dict_text:
        # Cross-reference / trailer stream
        result["stream_type"] = "xref"
        # Parse the xref/trailer stream into human-readable entries.
        parsed_bytes, xref_info, xref_errors = _parse_xref_stream(decoded, dict_text)
        result["decoded_bytes"] = parsed_bytes
        result["aux_info"]["xref"] = xref_info
        if xref_errors:
            result["errors"].extend(xref_errors)

    elif _looks_like_icc_profile(decoded):
        # ICCBased colour profile stream (used by /ICCBased color spaces)
        result["stream_type"] = "icc_profile"
        icc_bytes, icc_info, icc_errors = _parse_icc_profile_stream(decoded)
        result["decoded_bytes"] = icc_bytes
        result["aux_info"]["icc_profile"] = icc_info
        if icc_errors:
            result["errors"].extend(icc_errors)

    elif _looks_like_xmp_metadata(decoded, dict_text):
        # XMP metadata stream: keep XML text but normalise encoding and
        # provide a little structured summary alongside the raw packet.
        result["stream_type"] = "xmp"
        xmp_bytes, xmp_info = _parse_xmp_metadata_stream(decoded)
        result["decoded_bytes"] = xmp_bytes
        result["aux_info"]["xmp"] = xmp_info

    elif "/CMap" in dict_text or "begincmap" in decoded.decode("latin-1", errors="ignore"):
        # ToUnicode CMap stream
        result["stream_type"] = "cmap"
        cmap_info = parse_cmap_stream(decoded)
        result["aux_info"]["cmap"] = cmap_info

        # For the HTML "Decoded Stream" section, show the *character* mapping
        # table instead of the raw CMap program so you see actual characters,
        # not just U+XXXX codes.
        visible = cmap_info.get("visible_table")
        if visible:
            try:
                # html_renderer decodes this as latin-1 before escaping;
                # anything outside latin-1 becomes "?".
                result["decoded_bytes"] = visible.encode("latin-1", errors="replace")
            except Exception:
                # Fall back to the original raw bytes if anything goes wrong.
                pass


    elif "/CMap" in dict_text or "begincmap" in decoded.decode("latin-1", errors="ignore"):
        # ToUnicode CMap stream
        result["stream_type"] = "cmap"
        cmap_info = parse_cmap_stream(decoded)
        result["aux_info"]["cmap"] = cmap_info

        # For the HTML "Decoded Stream" section, show the *character* mapping
        # table instead of the raw CMap program so you see actual characters,
        # not just U+XXXX codes.
        visible = cmap_info.get("visible_table")
        if visible:
            try:
                # html_renderer decodes this as latin-1 before escaping;
                # anything outside latin-1 becomes "?".
                result["decoded_bytes"] = visible.encode("latin-1", errors="replace")
            except Exception:
                # Fall back to the original raw bytes if anything goes wrong.
                pass

    else:
        # ------------------------------------------------------------------
        # Generic / page-content streams
        # ------------------------------------------------------------------
        #
        # At this point we've applied any /Filter pipeline and stored the
        # result in `decoded_bytes`. Many objects (like 11 0 obj in the
        # ServiceEstimateSummary PDF) are just page content:
        #   - BT / ET text objects
        #   - Tj / TJ show-text operators
        #   - path/rect stroking/filling, etc.
        #
        # These should be shown verbatim in the HTML "Decoded Stream"
        # section so the examiner can read the actual text and operators.
        #
        # We keep the raw decoded bytes, but tag the stream_type so the
        # UI can label it more clearly than just "generic".
        try:
            txt = decoded.decode("latin-1", errors="ignore")
        except Exception:
            txt = ""

        # Simple heuristic: if it contains a text object and a text-show
        # operator, treat as page contents.
        # NOTE: we no longer require a leading space before Tj/TJ so that
        # cases like ")Tj" are also recognised.
        if ("BT" in txt and "ET" in txt) and ("Tj" in txt or "TJ" in txt):
            result["stream_type"] = "content"
        else:
            result["stream_type"] = "generic"


    return result


# ---------------------------------------------------------------------------
# Font-program helpers (TrueType/OpenType FontFile streams)
# ---------------------------------------------------------------------------

def _looks_like_image_object(dict_text: str, decoded: bytes) -> bool:
    """
    Heuristic check for image-like streams.

    Besides normal /Subtype /Image XObjects, some producers embed native JPEG
    image streams with only image parameters (/Width, /Height, /BitsPerComponent,
    /ColorSpace, /Filter /DCTDecode). Those should still be treated as images so
    they get counted, summarised, and previewed in HTML.
    """
    if not dict_text:
        dict_text = ""

    if "/Subtype /Image" in dict_text or "/Image" in dict_text:
        return True

    has_dimensions = (re.search(r"/Width\s+\d+", dict_text) is not None and
                      re.search(r"/Height\s+\d+", dict_text) is not None)
    has_image_params = (
        re.search(r"/BitsPerComponent\s+\d+", dict_text) is not None
        or "/ImageMask" in dict_text
        or "/ColorSpace" in dict_text
    )
    has_image_filter = re.search(
        r"/Filter\s+(?:\[(?:[^\]]*(?:/DCTDecode|/JPXDecode|/JBIG2Decode|/CCITTFaxDecode|/FlateDecode)[^\]]*)\]|/(?:DCTDecode|JPXDecode|JBIG2Decode|CCITTFaxDecode|FlateDecode))",
        dict_text,
    ) is not None

    if has_dimensions and has_image_params and has_image_filter:
        return True

    # Final safety net: if the decoded bytes already look like a native image
    # and the object dictionary exposes basic dimensions, treat it as image-like.
    if has_dimensions and isinstance(decoded, (bytes, bytearray)):
        head = bytes(decoded[:12])
        if head.startswith(b"\xFF\xD8") or head.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if head[:4] in (b"II*\x00", b"MM\x00*"):
            return True
        if head[:4] == b"\x00\x00\x00\x0c" and head[4:8] in (b"jP  ", b"jP2 "):
            return True

    return False


def _looks_like_font_program(decoded: bytes) -> bool:
    """
    Heuristic check for embedded SFNT font programs (TrueType / OpenType).

    We treat streams as font programs if they start with a typical SFNT
    header, e.g.:
      - 0x00010000 (TrueType 1.0)
      - 'OTTO' (CFF-based OpenType)
      - 'true', 'typ1', 'ttcf', etc.
    """
    if not isinstance(decoded, (bytes, bytearray)) or len(decoded) < 4:
        return False

    head = bytes(decoded[:4])

    # Classic TrueType header
    if head in (b"\x00\x01\x00\x00", b"\x00\x00\x01\x00"):
        return True

    # Common SFNT tags
    try:
        tag = head.decode("ascii")
    except Exception:
        return False

    return tag in ("OTTO", "true", "typ1", "ttcf")


def _find_font_descriptor_dict(
    obj_meta: Dict[str, Any],
    all_objects: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """
    Given a stream object that *is* the font program (e.g. 24 0 obj),
    try to locate the /FontDescriptor dictionary that references it via
    /FontFile, /FontFile2, or /FontFile3 so we can pull out /FontName
    and related metadata.

    Returns the raw object text of the descriptor, or None if not found.
    """
    if not all_objects:
        return None

    obj_num = obj_meta.get("obj_num")
    gen_num = obj_meta.get("gen_num", 0)
    if obj_num is None:
        return None

    ref = f"{obj_num} {gen_num} R"

    for other in all_objects:
        raw = other.get("raw_object_text") or ""
        if "/FontDescriptor" in raw and ref in raw:
            return raw

    return None


# ---------------------------------------------------------------------------
# ICC profile / font / XMP helpers
# ---------------------------------------------------------------------------

def _looks_like_icc_profile(decoded: bytes) -> bool:
    """Heuristic check for ICC profile streams.

    ICC profiles have "acsp" as a 4-byte profile signature at offset 36.
    """
    if not isinstance(decoded, (bytes, bytearray)):
        return False
    if len(decoded) < 132:
        return False
    sig = decoded[36:40]
    return sig in (b"acsp", b"ACSP")


def _parse_s15_fixed16_be(b: bytes) -> float:
    """Parse a 4-byte signed 15.16 fixed-point value used by ICC for XYZ."""
    if len(b) != 4:
        return 0.0
    val = struct.unpack(">i", b)[0]
    return val / 65536.0


def _parse_icc_profile_stream(decoded: bytes) -> Tuple[bytes, Dict[str, Any], List[str]]:
    """Parse an ICC profile stream into a concise, human-readable summary."""
    errors: List[str] = []
    info: Dict[str, Any] = {}

    try:
        size = struct.unpack(">I", decoded[0:4])[0]
        cmm = decoded[4:8].decode("ascii", "replace")
        version_raw = decoded[8]
        version = f"{version_raw >> 4}.{version_raw & 0x0F}"

        device_class = decoded[12:16].decode("ascii", "replace")
        color_space = decoded[16:20].decode("ascii", "replace")
        pcs = decoded[20:24].decode("ascii", "replace")

        year, month, day, hour, minute, second = struct.unpack(">6H", decoded[24:36])
        try:
            created = datetime(year, month, day, hour, minute, second)
            created_str = created.isoformat(sep=" ")
        except Exception:
            created_str = (
                f"{year:04d}-{month:02d}-{day:02d} "
                f"{hour:02d}:{minute:02d}:{second:02d}"
            )

        signature = decoded[36:40].decode("ascii", "replace")
        platform = decoded[40:44].decode("ascii", "replace")
        flags = struct.unpack(">I", decoded[44:48])[0]
        manufacturer = decoded[48:52].decode("ascii", "replace")
        model = decoded[52:56].decode("ascii", "replace")
        attrs_hi = struct.unpack(">I", decoded[56:60])[0]
        attrs_lo = struct.unpack(">I", decoded[60:64])[0]
        rendering_intent = struct.unpack(">I", decoded[64:68])[0]

        illum_x = _parse_s15_fixed16_be(decoded[68:72])
        illum_y = _parse_s15_fixed16_be(decoded[72:76])
        illum_z = _parse_s15_fixed16_be(decoded[76:80])
        creator = decoded[80:84].decode("ascii", "replace")

        tag_count = struct.unpack(">I", decoded[128:132])[0]
        tags: List[Tuple[str, int, int]] = []
        offset = 132
        for i in range(min(tag_count, 32)):
            if offset + 12 > len(decoded):
                errors.append(f"Tag table ended early at index {i}.")
                break
            sig_tag = decoded[offset: offset + 4].decode("ascii", "replace")
            tag_off = struct.unpack(">I", decoded[offset + 4: offset + 8])[0]
            tag_len = struct.unpack(">I", decoded[offset + 8: offset + 12])[0]
            offset += 12
            tags.append((sig_tag, tag_off, tag_len))

        info.update(
            {
                "size": size,
                "cmm_type": cmm,
                "version": version,
                "device_class": device_class,
                "color_space": color_space,
                "pcs": pcs,
                "created": created_str,
                "signature": signature,
                "platform": platform,
                "flags": flags,
                "manufacturer": manufacturer,
                "model": model,
                "attributes_hi": attrs_hi,
                "attributes_lo": attrs_lo,
                "rendering_intent": rendering_intent,
                "illuminant_xyz": (illum_x, illum_y, illum_z),
                "creator": creator,
                "tag_count": tag_count,
                "tags": tags,
            }
        )

        lines: List[str] = []
        lines.append("[ICC profile stream – decoded summary]")
        lines.append(f"Size:             {size} bytes")
        lines.append(f"CMM type:         {cmm}")
        lines.append(f"Version:          {version}")
        lines.append(f"Device class:     {device_class}")
        lines.append(f"Color space:      {color_space}")
        lines.append(f"PCS:              {pcs}")
        lines.append(f"Creation time:    {created_str}")
        lines.append(f"Profile signature:{signature}")
        lines.append(f"Platform:         {platform}")
        lines.append(f"Flags:            0x{flags:08X}")
        lines.append(f"Manufacturer:     {manufacturer}")
        lines.append(f"Model:            {model}")
        lines.append(
            f"Attributes:       hi=0x{attrs_hi:08X} lo=0x{attrs_lo:08X}"
        )
        lines.append(f"Rendering intent: {rendering_intent}")
        lines.append(
            f"Illuminant XYZ:   X={illum_x:.4f} Y={illum_y:.4f} Z={illum_z:.4f}"
        )
        lines.append(f"Creator:          {creator}")
        lines.append("")
        lines.append(
            f"Tag table: ({tag_count} entr{'y' if tag_count == 1 else 'ies'})"
        )
        for sig_tag, tag_off, tag_len in tags:
            lines.append(
                f"  {sig_tag:4s}: offset={tag_off:6d}, length={tag_len:6d}"
            )

        summary = "\n".join(lines).encode("latin-1", errors="replace")
        return summary, info, errors

    except Exception as e:  # very defensive
        errors.append(f"ICC parse error: {e!r}")
        # IMPORTANT: no non-ASCII characters inside a bytes literal
        summary_text = "[ICC profile stream - parse error]"
        summary = summary_text.encode("ascii", "replace")
        return summary, info, errors


def _render_font_program_summary(font_info: Dict[str, Any], dict_text: str) -> Optional[bytes]:
    """Render a concise textual summary of an embedded font program.

    This keeps the heavy SFNT parsing in :func:`parse_font_program` but
    converts its result into examiner-friendly lines that can be shown in
    the "Decoded Stream" block instead of raw binary.
    """
    if not font_info:
        return None

    sfnt_version = font_info.get("sfnt_version") or "unknown"
    table_count = font_info.get("table_count")
    tables = font_info.get("tables") or []

    # Try to pull the /BaseFont (or /FontName) from the dictionary text
    base_font = None
    m = re.search(r"/(?:BaseFont|FontName)\s+/([^\s>]+)", dict_text or "")
    if m:
        base_font = m.group(1)

    lines: List[str] = []
    lines.append("[Font Program Stream – Embedded Subset Font]")
    if base_font:
        # This produces e.g. "/RJWWCL+Tahoma"
        lines.append(f"Base font:        /{base_font}")
    lines.append(f"SFNT version:     {sfnt_version}")
    if table_count is not None:
        lines.append(f"Table count:      {table_count}")
    lines.append("")

    if tables:
        lines.append("Table directory (subset):")
        for t in tables[:32]:
            tag = t.get("tag") or "????"
            offset = t.get("offset", 0)
            length = t.get("length", 0)
            lines.append(
                f"  {tag:4s}  offset={offset:7d}  length={length:6d}"
            )

    notes = font_info.get("notes")
    if notes:
        lines.append("")
        lines.append(f"Notes: {notes}")

    return "\n".join(lines).encode("latin-1", errors="replace")


def _looks_like_xmp_metadata(decoded: bytes, dict_text: str) -> bool:
    """Heuristic check for XMP metadata streams.

    We look for typical XML/XMP markers in either the dict or the content.
    """
    txt_hint = dict_text or ""
    if "/Metadata" in txt_hint or "/Subtype /XML" in txt_hint:
        return True

    try:
        sample = decoded[:1024].decode("utf-8", errors="ignore")
    except Exception:
        return False

    if "<?xpacket" in sample or "<x:xmpmeta" in sample or "<rdf:RDF" in sample:
        return True

    return False


def _normalise_xml_bytes(decoded: bytes) -> bytes:
    """Normalise bytes that are believed to contain XML/XMP to UTF-8."""
    if decoded.startswith(b"\xff\xfe") or decoded.startswith(b"\xfe\xff"):
        # UTF-16 with BOM
        try:
            text = decoded.decode("utf-16")
            return text.encode("utf-8")
        except Exception:
            return decoded
    try:
        text = decoded.decode("utf-8")
        return text.encode("utf-8")
    except Exception:
        try:
            text = decoded.decode("latin-1")
            return text.encode("utf-8")
        except Exception:
            return decoded


def _parse_xmp_metadata_stream(decoded: bytes) -> Tuple[bytes, Dict[str, Any]]:
    """Create a slightly annotated XMP packet for display plus light metadata."""
    xml_bytes = _normalise_xml_bytes(decoded)
    try:
        xml_text = xml_bytes.decode("utf-8", errors="replace")
    except Exception:
        xml_text = ""  # fall back to empty on very broken data

    info: Dict[str, Any] = {
        "approx_size": len(xml_bytes),
        "has_xpacket": "<?xpacket" in xml_text,
        "has_rdf": "<rdf:RDF" in xml_text,
        "has_dc": ":dc" in xml_text or "<dc:" in xml_text,
    }

    # We keep the original XML content so you can see every field in situ.
    annotated = "[XMP metadata stream – decoded XML]\n" + xml_text
    return annotated.encode("utf-8"), info




# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _extract_filters(dict_text: str) -> List[str]:
    """
    Very simple /Filter parser: looks for /Filter [...] or /Filter /Name.
    Returns a list of filter names as strings, e.g. ["/FlateDecode", "/ASCII85Decode"].

    This handles only direct forms such as:

        /Filter /FlateDecode
        /Filter [/FlateDecode /ASCII85Decode]

    Indirect forms like `/Filter 6 0 R` are handled separately by
    `_resolve_indirect_filters`.
    """
    txt = dict_text or ""
    filters: List[str] = []

    # Match either:
    #   /Filter /FlateDecode
    # or
    #   /Filter [/FlateDecode /ASCII85Decode]
    m = re.search(r"/Filter\s*(\[[^\]]+\]|/[A-Za-z0-9#+.\-]+)", txt)
    if not m:
        return filters

    val = m.group(1).strip()
    if val.startswith("["):
        # Array form: /Filter [/FlateDecode /ASCII85Decode]
        # Split on whitespace and keep only tokens that look like /Name
        for token in val[1:-1].split():
            if token.startswith("/"):
                filters.append(token)
    else:
        # Single-name form: /Filter /FlateDecode
        filters.append(val)

    return filters


def _resolve_indirect_filters(
    dict_text: str,
    all_objects: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """
    Resolve `/Filter N 0 R` where N 0 obj is a small object describing the
    filter pipeline, typically an array like:

        6 0 obj
        [/FlateDecode/RunLengthDecode]
        endobj

    Returns a list of filter names, e.g. ["/FlateDecode", "/RunLengthDecode"].
    If resolution fails, returns [].
    """
    if not dict_text or not all_objects:
        return []

    m = re.search(r"/Filter\s+(\d+)\s+0\s+R", dict_text)
    if not m:
        return []

    ref_obj_num = int(m.group(1))

    # Find the referenced object (e.g. 6 0 obj).
    for obj in all_objects:
        if obj.get("obj_num") != ref_obj_num:
            continue

        # Prefer the full raw text in case the array is outside a dictionary.
        ref_text = (obj.get("raw_object_text") or obj.get("dict_text") or "").strip()
        if not ref_text:
            continue

        # Look for an array [ ... ] inside this object.
        arr_match = re.search(r"\[([^\]]+)\]", ref_text)
        if arr_match:
            inner = arr_match.group(1)
        else:
            # Fallback: use the whole object text if no explicit array was found.
            inner = ref_text

        # Extract tokens that look like /Name.
        filters = re.findall(r"/[A-Za-z0-9#+.\-]+", inner)
        return filters

    return []


def _run_length_decode(data: bytes) -> bytes:
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


def _apply_filters(stream_bytes: bytes, filters: List[str]) -> bytes:
    """
    Apply filters in order. This is intentionally minimal.
    Extended to handle RunLengthDecode for image masks etc.
    """
    data = stream_bytes
    for f in filters:
        if f in ("/FlateDecode", "/Fl"):
            data = decode_flate(data)
        elif f in ("/ASCIIHexDecode", "/AHx"):
            data = decode_ascii_hex(data)
        elif f in ("/ASCII85Decode", "/A85"):
            data = decode_ascii85(data)
        elif f in ("/LZWDecode", "/LZW"):
            data = decode_lzw(data)
        elif f in ("/RunLengthDecode", "/RL"):
            data = _run_length_decode(data)
        # TODO: add more filters as required
    return data

# ---------------------------------------------------------------------------
# ICC profile helpers
# ---------------------------------------------------------------------------

def _looks_like_icc_profile(decoded: bytes) -> bool:
    """
    Heuristic check for ICC profile streams.

    ICC profiles have "acsp" as a 4-byte profile signature at offset 36.
    We keep this deliberately simple so it works across typical ICCBased
    colour profiles used in PDFs.
    """
    if not isinstance(decoded, (bytes, bytearray)):
        return False
    if len(decoded) < 132:
        return False
    sig = decoded[36:40]
    return sig in (b"acsp", b"ACSP")


def _parse_s15_fixed16_be(b: bytes) -> float:
    """
    Parse a 4-byte signed 15.16 fixed-point value used by ICC for XYZ.
    """
    if len(b) != 4:
        return 0.0
    val = struct.unpack(">i", b)[0]
    return val / 65536.0


def _parse_icc_profile_stream(
    decoded: bytes,
) -> Tuple[bytes, Dict[str, Any], List[str]]:
    """
    Parse an ICC profile stream into a concise, human-readable summary.

    Returns:
        (summary_bytes, aux_info_dict, errors_list)
    """
    errors: List[str] = []
    info: Dict[str, Any] = {}

    try:
        size = struct.unpack(">I", decoded[0:4])[0]
        cmm = decoded[4:8].decode("ascii", "replace")
        version_raw = decoded[8]
        version = f"{version_raw >> 4}.{version_raw & 0x0F}"

        device_class = decoded[12:16].decode("ascii", "replace")
        color_space = decoded[16:20].decode("ascii", "replace")
        pcs = decoded[20:24].decode("ascii", "replace")

        year, month, day, hour, minute, second = struct.unpack(">6H", decoded[24:36])
        try:
            created = datetime(year, month, day, hour, minute, second)
            created_str = created.isoformat(sep=" ")
        except Exception:
            created_str = (
                f"{year:04d}-{month:02d}-{day:02d} "
                f"{hour:02d}:{minute:02d}:{second:02d}"
            )

        signature = decoded[36:40].decode("ascii", "replace")
        platform = decoded[40:44].decode("ascii", "replace")
        flags = struct.unpack(">I", decoded[44:48])[0]
        manufacturer = decoded[48:52].decode("ascii", "replace")
        model = decoded[52:56].decode("ascii", "replace")
        attrs_hi = struct.unpack(">I", decoded[56:60])[0]
        attrs_lo = struct.unpack(">I", decoded[60:64])[0]
        rendering_intent = struct.unpack(">I", decoded[64:68])[0]

        illum_x = _parse_s15_fixed16_be(decoded[68:72])
        illum_y = _parse_s15_fixed16_be(decoded[72:76])
        illum_z = _parse_s15_fixed16_be(decoded[76:80])
        creator = decoded[80:84].decode("ascii", "replace")

        # Tag table
        tag_count = struct.unpack(">I", decoded[128:132])[0]
        tags: List[Tuple[str, int, int]] = []
        offset = 132
        for i in range(min(tag_count, 32)):  # cap for safety
            if offset + 12 > len(decoded):
                errors.append(f"Tag table ended early at index {i}.")
                break
            sig_tag = decoded[offset : offset + 4].decode("ascii", "replace")
            tag_off = struct.unpack(">I", decoded[offset + 4 : offset + 8])[0]
            tag_len = struct.unpack(">I", decoded[offset + 8 : offset + 12])[0]
            offset += 12
            tags.append((sig_tag, tag_off, tag_len))

        info.update(
            {
                "size": size,
                "cmm_type": cmm,
                "version": version,
                "device_class": device_class,
                "color_space": color_space,
                "pcs": pcs,
                "created": created_str,
                "signature": signature,
                "platform": platform,
                "flags": flags,
                "manufacturer": manufacturer,
                "model": model,
                "attributes_hi": attrs_hi,
                "attributes_lo": attrs_lo,
                "rendering_intent": rendering_intent,
                "illuminant_xyz": (illum_x, illum_y, illum_z),
                "creator": creator,
                "tag_count": tag_count,
                "tags": tags,
            }
        )

        # Human-readable summary suitable for the "Decoded Stream" block
        lines: List[str] = []
        lines.append("[ICC profile stream – decoded summary]")
        lines.append(f"Size:             {size} bytes")
        lines.append(f"CMM type:         {cmm}")
        lines.append(f"Version:          {version}")
        lines.append(f"Device class:     {device_class}")
        lines.append(f"Color space:      {color_space}")
        lines.append(f"PCS:              {pcs}")
        lines.append(f"Creation time:    {created_str}")
        lines.append(f"Profile signature:{signature}")
        lines.append(f"Platform:         {platform}")
        lines.append(f"Flags:            0x{flags:08X}")
        lines.append(f"Manufacturer:     {manufacturer}")
        lines.append(f"Model:            {model}")
        lines.append(
            f"Attributes:       hi=0x{attrs_hi:08X} lo=0x{attrs_lo:08X}"
        )
        lines.append(f"Rendering intent: {rendering_intent}")
        lines.append(
            f"Illuminant XYZ:   X={illum_x:.4f} "
            f"Y={illum_y:.4f} Z={illum_z:.4f}"
        )
        lines.append(f"Creator:          {creator}")
        lines.append("")
        lines.append(
            f"Tag table: ({tag_count} entr{'y' if tag_count == 1 else 'ies'})"
        )
        for sig_tag, tag_off, tag_len in tags:
            lines.append(
                f"  {sig_tag:4s}: offset={tag_off:6d}, length={tag_len:6d}"
            )

        summary = "\n".join(lines).encode("latin-1", errors="replace")
        return summary, info, errors

    except Exception as e:  # very defensive
        errors.append(f"ICC parse error: {e!r}")
        # IMPORTANT: no non-ASCII characters inside a bytes literal
        summary_text = "[ICC profile stream - parse error]"
        summary = summary_text.encode("ascii", "replace")
        return summary, info, errors



# ---------------------------------------------------------------------------
# XRef stream parser (unchanged)
# ---------------------------------------------------------------------------

def _parse_xref_stream(decoded: bytes, dict_text: str) -> Tuple[bytes, Dict[str, Any], List[str]]:
    """
    Parse an /XRef (trailer) stream into a textual listing and structured aux_info.

    Returns (text_bytes, aux_info, errors).
    """
    errors: List[str] = []
    aux: Dict[str, Any] = {}

    # Extract /W [w0 w1 w2]
    m_w = re.search(r"/W\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s*\]", dict_text)
    if not m_w:
        errors.append("No /W array found in xref stream dictionary.")
        return decoded, aux, errors

    w0, w1, w2 = map(int, m_w.groups())
    aux["W"] = [w0, w1, w2]

    # Extract /Size
    m_size = re.search(r"/Size\s+(\d+)", dict_text)
    if m_size:
        aux["Size"] = int(m_size.group(1))
    else:
        errors.append("No /Size found in xref stream dictionary.")

    # Extract /Index [first count first2 count2 ...]
    m_index = re.search(r"/Index\s*\[([^\]]+)\]", dict_text)
    segments: List[Tuple[int, int]] = []
    if m_index:
        nums = [int(n) for n in m_index.group(1).replace(",", " ").split()]
        if len(nums) % 2 != 0:
            errors.append("Odd number of integers in /Index array.")
        for i in range(0, len(nums) - 1, 2):
            segments.append((nums[i], nums[i + 1]))
    elif "Size" in aux:
        segments.append((0, aux["Size"]))
    else:
        entry_width = w0 + w1 + w2
        total_entries = len(decoded) // entry_width if entry_width > 0 else 0
        segments.append((0, total_entries))

    aux["IndexSegments"] = segments

    entry_width = w0 + w1 + w2
    lines: List[str] = []
    lines.append("# Parsed xref stream")
    lines.append(f"#   W = [{w0} {w1} {w2}]")
    if "Size" in aux:
        lines.append(f"#   Size = {aux['Size']}")
    lines.append(f"#   Index segments = {segments}")

    entries: List[Tuple[int, int, int, int]] = []
    offset = 0

    for start_obj, count in segments:
        for i in range(count):
            if offset + entry_width > len(decoded):
                errors.append(f"Decoded xref stream ended early at byte {offset}.")
                break

            rec = decoded[offset : offset + entry_width]
            offset += entry_width

            p = 0

            def read_int(width: int) -> int:
                nonlocal p
                if width == 0:
                    return 0
                v = 0
                for b in rec[p : p + width]:
                    v = (v << 8) | b
                p += width
                return v

            # If w0 == 0, default type is 1 (in-use entry) per spec.
            f_type = read_int(w0) if w0 > 0 else 1
            f2 = read_int(w1) if w1 > 0 else 0
            f3 = read_int(w2) if w2 > 0 else 0
            obj_num = start_obj + i

            entries.append((obj_num, f_type, f2, f3))

            if f_type == 0:
                desc = f"{obj_num:5d}: free; next={f2}, gen={f3}"
            elif f_type == 1:
                desc = f"{obj_num:5d}: in file at offset {f2}, gen={f3}"
            elif f_type == 2:
                desc = f"{obj_num:5d}: in object stream {f2}, index={f3}"
            else:
                desc = f"{obj_num:5d}: type={f_type}, field2={f2}, field3={f3}"

            lines.append(desc)

    aux["Entries"] = entries

    text = "\n".join(lines).encode("ascii", errors="replace")
    return text, aux, errors
