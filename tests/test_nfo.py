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
    thumbs = {a.findtext("name"): a.findtext("thumb") for a in root.findall("actor")}
    assert thumbs["葵つかさ"] == "https://www.javbus.com/pics/actress/2xi_a.jpg"
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


def test_nfo_actor_thumb_skips_blank_photo():
    xml = build_nfo({
        "code": "SSIS-001",
        "actors": [
            {"name": "葵つかさ", "photo": "https://www.javbus.com/pics/actress/2xi_a.jpg"},
            {"name": "空白", "photo": "  "},
            {"name": "没有", "photo": ""},
            "只是名字",
        ],
    })
    root = ET.fromstring(xml)
    by_name = {actor.findtext("name"): actor for actor in root.findall("actor")}
    assert by_name["葵つかさ"].findtext("thumb") == "https://www.javbus.com/pics/actress/2xi_a.jpg"
    assert by_name["空白"].find("thumb") is None
    assert by_name["没有"].find("thumb") is None
    assert by_name["只是名字"].find("thumb") is None
