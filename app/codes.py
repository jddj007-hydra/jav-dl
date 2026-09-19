from __future__ import annotations

import re

CODE_RE = re.compile(r"^([A-Z]{2,5})-?(\d{2,5})$")


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
