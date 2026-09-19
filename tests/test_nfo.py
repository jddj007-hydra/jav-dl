from xml.etree import ElementTree as ET

from app.nfo import build_nfo, runtime_minutes
from app.sources.javbus import parse_javbus
from pathlib import Path

HTML = (Path(__file__).parent / "fixtures" / "javbus_ssis001.html").read_text(
    encoding="utf-8"
)


def test_runtime_minutes():
    assert runtime_minutes("120分鐘") == "120"
    assert runtime_minutes("") == ""
    assert runtime_minutes(None) == ""


def test_nfo_from_javbus():
    meta = parse_javbus(HTML, "https://www.javbus.com", "SSIS-001")
    xml = build_nfo(meta)
    root = ET.fromstring(xml)
    assert root.findtext("id") == "SSIS-001"
    assert root.findtext("title").startswith("SSIS-001")
    assert "禁欲" in root.findtext("title") or "禁慾" in root.findtext("title")
    assert root.findtext("premiered") == "2021-02-18"
    assert root.findtext("year") == "2021"
    names = [a.findtext("name") for a in root.findall("actor")]
    assert "葵つかさ" in names
    assert "多P" in [g.text for g in root.findall("genre")]
    uid = root.find("uniqueid")
    assert uid is not None and uid.text == "SSIS-001"


def test_nfo_escapes_xml():
    xml = build_nfo({
        "code": "ABC-123",
        "title": "A & B <C>",
        "studio": "Foo & Bar",
        "actors": [{"name": "X & Y"}],
        "genres": ["A&B"],
    })
    root = ET.fromstring(xml)
    assert root.findtext("originaltitle") == "A & B <C>"
    assert root.findtext("studio") == "Foo & Bar"
