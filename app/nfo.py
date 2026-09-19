from __future__ import annotations

import re
import xml.etree.ElementTree as ET


def _text(parent: ET.Element, tag: str, value: object | None) -> None:
    if value is None:
        return
    s = str(value).strip()
    if not s:
        return
    el = ET.SubElement(parent, tag)
    el.text = s


def runtime_minutes(runtime: str | None) -> str:
    m = re.search(r"(\d+)", runtime or "")
    return m.group(1) if m else ""


def build_nfo(meta: dict) -> str:
    code = (meta.get("code") or "").strip()
    title = (meta.get("title") or "").strip()
    full_title = f"{code} {title}".strip() if code else title
    release = (meta.get("release_date") or "").strip()
    year = release[:4] if re.match(r"\d{4}", release) else ""

    movie = ET.Element("movie")
    _text(movie, "title", full_title)
    _text(movie, "originaltitle", title or full_title)
    _text(movie, "sorttitle", code)
    _text(movie, "year", year)
    _text(movie, "premiered", release)
    _text(movie, "releasedate", release)
    _text(movie, "runtime", runtime_minutes(meta.get("runtime")))
    _text(movie, "studio", meta.get("studio"))
    _text(movie, "maker", meta.get("studio"))
    _text(movie, "label", meta.get("label"))
    _text(movie, "director", meta.get("director"))
    _text(movie, "id", code)
    if code:
        uid = ET.SubElement(movie, "uniqueid", {"type": "num", "default": "true"})
        uid.text = code
        uid_bus = ET.SubElement(movie, "uniqueid", {"type": "javbus"})
        uid_bus.text = code
    series = (meta.get("series") or "").strip()
    if series:
        s_el = ET.SubElement(movie, "set")
        _text(s_el, "name", series)
    for genre in meta.get("genres") or []:
        _text(movie, "genre", genre)
        _text(movie, "tag", genre)
    actors = meta.get("actors") or []
    for actor in actors:
        name = actor["name"] if isinstance(actor, dict) else actor
        if not name:
            continue
        a_el = ET.SubElement(movie, "actor")
        _text(a_el, "name", name)
        _text(a_el, "type", "Actor")
    url = (meta.get("url") or "").strip()
    if url:
        _text(movie, "website", url)

    ET.indent(movie, space="  ")
    return ET.tostring(movie, encoding="unicode", xml_declaration=True)
