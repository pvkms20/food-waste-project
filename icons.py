"""Inline SVG icons (constant markup written here, never built from user input)."""
from markupsafe import Markup

PATHS = {
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "predict": '<path d="M3 17l6-6 4 4 8-9"/><path d="M15 6h6v6"/>',
    "donations": '<path d="M12 21s-7-4.6-9-9.2C1.6 8 3.8 4.5 7.2 4.5c2 0 3.7 1.1 4.8 2.7 1.1-1.6 2.8-2.7 4.8-2.7 3.4 0 5.6 3.5 4.2 7.3C19 16.4 12 21 12 21z"/>',
    "track": '<path d="M3 7l9-4 9 4v10l-9 4-9-4z"/><path d="M3 7l9 4 9-4M12 11v10"/>',
    "verify": '<path d="M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5L15.5 10"/>',
    "records": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "model": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "status": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "chev": '<path d="M15 6l-6 6 6 6"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "leaf": '<path d="M5 19c0-9 5-14 15-14 0 10-5 15-14 15"/><path d="M5 19c3-5 6-8 10-10"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    "truck": '<path d="M2 6h11v10H2zM13 10h4l3 3v3h-7z"/><circle cx="7" cy="18" r="1.8"/><circle cx="17" cy="18" r="1.8"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "box": '<path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M4 8l8 4 8-4M12 12v8"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "inbox": '<path d="M3 13l3-8h12l3 8v6H3z"/><path d="M3 13h5l1 3h6l1-3h5"/>',
    "stop": '<circle cx="12" cy="12" r="9"/><path d="M6 6l12 12"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
}


def icon(name):
    """Returns the <svg> for a known icon name (Markup, because it is our own constant markup)."""
    return Markup('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + PATHS[name] + "</svg>")
