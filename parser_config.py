
"""
parser_config.py

Central place for toggles / settings for the PDF x-ray parser.
Adjust these flags instead of editing logic everywhere.
"""

CONFIG = {
    # Application metadata
    "app_version": "0.0.20",   # PDF X-Ray version (edit here when changing it)
    
    # Output toggles
    "generate_html": True,
    "generate_txt": False,

    # Stream decoding & output behaviour
    "show_decoded_stream": True,
    "show_operator_annotated_stream": True,  # legacy: second decoded view with inline comments
    "show_image_previews": True,
    "show_text_stream": True,  # NEW: add "Text Stream" dropdown for content streams

    # Analysis / warnings
    "warn_strange_generations": True,
    "color_code_generations": True,
    "summarise_incremental_updates": True,

    # Rendering options
    # - collapse_objects_by_default:
    #       True  -> wrap raw/decoded blocks in <details> sections (collapsible)
    #       False -> show everything flat, no <details> wrapper
    # - details_default_open:
    #       True  -> <details open> (expanded by default but still collapsible)
    #       False -> <details> (collapsed by default)
    "collapse_objects_by_default": True,
    "details_default_open": False,
    "max_stream_preview_bytes": None,  # how much raw stream to show before truncation. Set to None or 0 for no truncation.

    # File naming
    "html_suffix": "_xray.html",
    "txt_suffix": "_xray.txt",
}
