from __future__ import annotations

import re

_SIZE_RE = re.compile(
    r"([\d.]+)\s*(TB|GB|MB|KB|B|TIB|GIB|MIB|KIB)",
    re.IGNORECASE,
)
_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}


def parse_size(text: str | None) -> int | None:
    if not text:
        return None
    m = _SIZE_RE.search(text.replace(",", ""))
    if not m:
        return None
    n = float(m.group(1))
    unit = m.group(2).upper()
    return int(n * _UNITS[unit])


def format_size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit, div in (("TB", 1000**4), ("GB", 1000**3), ("MB", 1000**2), ("KB", 1000)):
        if n >= div:
            val = n / div
            return f"{val:.2f} {unit}" if val < 10 else f"{val:.1f} {unit}"
    return f"{n} B"


def strip_em(html_or_text: str) -> str:
    text = re.sub(r"<[^>]+>", "", html_or_text)
    return re.sub(r"\s+", " ", text).strip()
