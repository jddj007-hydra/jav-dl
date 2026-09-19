from __future__ import annotations

import re

CODE_RE = re.compile(r"^([A-Z]{2,5})-?(\d{2,5})$")
CODE_HYPHEN_RE = re.compile(r"([A-Z]{2,5})-(\d{2,5})", re.I)
CODE_LOOSE_RE = re.compile(r"([A-Z]{2,5})(\d{3,5})", re.I)
FALSE_PREFIXES = frozenset({
    "HD", "FHD", "UHD", "SD", "CD", "DVD", "VOL", "PART", "FILE", "FPS",
    "HDR", "HEVC", "AV1", "AAC", "DTS", "MKV", "MP4", "WMV", "ISO", "WEB",
    "REMUX", "BLURAY", "H264", "H265", "X264", "X265", "AVC",
})


def normalize_code(raw: str) -> str | None:
    """ssis001 / SSIS-001 / ssis_001 → SSIS-001. Invalid input → None."""
    if raw is None:
        return None
    s = raw.strip().upper().replace("_", "-").replace(" ", "")
    m = CODE_RE.fullmatch(s)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def compact_code(code: str) -> str:
    return code.replace("-", "").upper()


def title_mentions_code(title: str, code: str) -> bool:
    return compact_code(code) in re.sub(r"[^A-Z0-9]", "", title.upper())


def extract_codes(raw: str) -> list[str]:
    if not raw:
        return []
    text = raw.upper().replace("_", "-")
    found: list[str] = []
    seen: set[str] = set()
    for m in CODE_HYPHEN_RE.finditer(text):
        prefix, num = m.group(1).upper(), m.group(2)
        if prefix in FALSE_PREFIXES:
            continue
        code = f"{prefix}-{num}"
        if code not in seen:
            seen.add(code)
            found.append(code)
    if found:
        return found
    compact = re.sub(r"[^A-Z0-9]", "", text)
    for m in CODE_LOOSE_RE.finditer(compact):
        prefix, num = m.group(1).upper(), m.group(2)
        if prefix in FALSE_PREFIXES:
            continue
        code = f"{prefix}-{num}"
        if code not in seen:
            seen.add(code)
            found.append(code)
    return found


def extract_code(raw: str) -> str | None:
    codes = extract_codes(raw)
    return codes[0] if len(codes) == 1 else None
