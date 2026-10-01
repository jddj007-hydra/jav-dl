from __future__ import annotations

import re

CODE_RE = re.compile(r"^([A-Z]{2,5})-?(\d{2,5})$")
CODE_HYPHEN_RE = re.compile(r"([A-Z]{2,5})-(\d{2,5})", re.I)
CODE_LOOSE_RE = re.compile(r"([A-Z]{2,5})(\d{3,5})", re.I)
# DANDYHQVR / URVRSP / 3DSVR 比普通厂牌长，或带数字。短正则会切成 YHQVR-015。
VR_HYPHEN_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9]{2,12})-(\d{2,5})(?!\d)", re.I)
VR_CODE_HYPHEN_RE = re.compile(r"^([A-Z0-9]{2,12})-(\d{2,5})$")
VR_CODE_COMPACT_RE = re.compile(r"^([A-Z0-9]{2,12}?)(\d{3,5})$")
VR_LOOSE_RE = re.compile(r"([A-Z0-9]{2,12}?)(\d{3,5})")
# 加勒比、一本道、天然むすめ这一类是日期加序号，不是字母厂牌。
DATE_CODE_RE = re.compile(r"^(\d{6})-(\d{2,4})$")
DATE_HYPHEN_RE = re.compile(r"(?<!\d)(\d{6})-(\d{2,4})(?!\d)")
# JavBus 上的页面是 FC2-PPV-编号。文件名里常常写成 FC2 或 FC2PPV。
FC2_CODE_RE = re.compile(r"^FC2(?:-?PPV)?-?(\d{6,8})$")
FC2_HYPHEN_RE = re.compile(r"(?<![A-Z0-9])FC2(?:-?PPV)?-?(\d{6,8})(?!\d)", re.I)
FALSE_PREFIXES = frozenset({
    "HD", "FHD", "UHD", "SD", "CD", "DVD", "VOL", "PART", "FILE", "FPS",
    "HDR", "HEVC", "AV1", "AAC", "DTS", "MKV", "MP4", "WMV", "ISO", "WEB",
    "REMUX", "BLURAY", "H264", "H265", "X264", "X265", "AVC",
})
# 番号 VR 厂牌。EXMO / MKCK / CBIKMV 名字里没有 VR，但现网 vrporn/jav 是这一套。
_JAV_VR_MAKERS = frozenset({
    "AJVR", "BIBIVR", "CBIKMV", "CJVR", "CRVR", "DANDYHQVR", "DSVR",
    "EBVR", "EXMO", "FCVR", "HUNVR", "IPVR", "JUVR", "KAVR", "KIVR",
    "KIWVR", "KMVR", "MDVR", "MKCK", "NHVR", "NKKVR", "PPVR", "PXVR",
    "SAVR", "SIVR", "TMAVR", "URVR", "VRKM", "WAVR", "3DSVR",
})


def _fc2_code(number: str) -> str:
    return f"FC2-PPV-{number}"


def _vr_code(maker: str, number: str) -> str | None:
    maker = (maker or "").upper()
    number = (number or "").strip()
    if not maker or not number or maker in FALSE_PREFIXES:
        return None
    if maker in _JAV_VR_MAKERS or "VR" in maker:
        return f"{maker}-{number}"
    return None


def normalize_code(raw: str) -> str | None:
    """ssis001 / SSIS-001 / 092126-001 / FC2PPV-3237415 → 统一番号。非法输入 → None。"""
    if raw is None:
        return None
    s = raw.strip().upper().replace("_", "-").replace(" ", "")
    fc2 = FC2_CODE_RE.fullmatch(s)
    if fc2:
        return _fc2_code(fc2.group(1))
    dated = DATE_CODE_RE.fullmatch(s)
    if dated:
        return f"{dated.group(1)}-{dated.group(2)}"
    m = CODE_RE.fullmatch(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    vr = VR_CODE_HYPHEN_RE.fullmatch(s)
    if vr:
        return _vr_code(vr.group(1), vr.group(2))
    compact = VR_CODE_COMPACT_RE.fullmatch(s)
    if compact:
        return _vr_code(compact.group(1), compact.group(2))
    return None


def compact_code(code: str) -> str:
    return code.replace("-", "").upper()


def _fc2_number(code: str) -> str:
    matched = FC2_CODE_RE.fullmatch(code or "")
    return matched.group(1) if matched else ""


def title_mentions_code(title: str, code: str) -> bool:
    compact_title = re.sub(r"[^A-Z0-9]", "", (title or "").upper())
    if compact_code(code) in compact_title:
        return True
    number = _fc2_number(code)
    if not number:
        return False
    return f"FC2{number}" in compact_title or f"FC2PPV{number}" in compact_title


def _add_code(found: list[str], seen: set[str], code: str) -> None:
    if code in seen:
        return
    seen.add(code)
    found.append(code)


def extract_codes(raw: str) -> list[str]:
    if not raw:
        return []
    text = raw.upper().replace("_", "-")
    found: list[str] = []
    seen: set[str] = set()
    fc2_spans: list[tuple[int, int]] = []
    for m in FC2_HYPHEN_RE.finditer(text):
        _add_code(found, seen, _fc2_code(m.group(1)))
        fc2_spans.append(m.span())
    for m in DATE_HYPHEN_RE.finditer(text):
        _add_code(found, seen, f"{m.group(1)}-{m.group(2)}")
    vr_spans: list[tuple[int, int]] = []
    for m in VR_HYPHEN_RE.finditer(text):
        code = _vr_code(m.group(1), m.group(2))
        if not code:
            continue
        _add_code(found, seen, code)
        vr_spans.append(m.span())
    for m in CODE_HYPHEN_RE.finditer(text):
        if any(m.start() < end and m.end() > start for start, end in (*fc2_spans, *vr_spans)):
            continue
        prefix, num = m.group(1).upper(), m.group(2)
        if prefix in FALSE_PREFIXES:
            continue
        _add_code(found, seen, f"{prefix}-{num}")
    if found:
        return found
    compact = re.sub(r"[^A-Z0-9]", "", text)
    for m in VR_LOOSE_RE.finditer(compact):
        code = _vr_code(m.group(1), m.group(2))
        if code:
            _add_code(found, seen, code)
    if found:
        return found
    for m in CODE_LOOSE_RE.finditer(compact):
        prefix, num = m.group(1).upper(), m.group(2)
        if prefix in FALSE_PREFIXES:
            continue
        _add_code(found, seen, f"{prefix}-{num}")
    return found


def extract_code(raw: str) -> str | None:
    codes = extract_codes(raw)
    return codes[0] if len(codes) == 1 else None


def code_maker(code: str) -> str:
    normalized = normalize_code(code) or (code or "").strip().upper()
    if not normalized or "-" not in normalized:
        return normalized
    return normalized.split("-", 1)[0]


_MONTH_DIR = re.compile(r"^\d{6}$")


def jav_vr_maker(code: str) -> str | None:
    maker = code_maker(code)
    if not maker or maker.isdigit():
        return None
    if maker in _JAV_VR_MAKERS or "VR" in maker:
        return maker
    return None


def is_jav_vr_entry(code: str, month: str = "") -> bool:
    """番号 VR 厂牌，或 vrporn/jav 下按片商归档的无码（Caribbean / 10mu 日期番号）。"""
    if jav_vr_maker(code):
        return True
    bucket = (month or "").strip()
    return bool(bucket) and _MONTH_DIR.fullmatch(bucket) is None
