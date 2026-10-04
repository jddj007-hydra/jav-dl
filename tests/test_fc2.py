from pathlib import Path

import pytest

from app.codes import fc2_number, normalize_code
from app.sources.fc2 import _full_image, parse_fc2
from app.sources.javbus import MetadataError

FIXTURES = Path(__file__).parent / "fixtures"


def test_fc2_number_helpers():
    assert fc2_number("FC2-3237415") == "3237415"
    assert fc2_number("fc2ppv-3237415") == "3237415"
    assert fc2_number("FC2-PPV-3237415") == "3237415"
    assert fc2_number("SSIS-001") is None
    assert normalize_code("FC2-3237415") == "FC2-PPV-3237415"


def test_full_image_upgrades_thumbnail_cdn():
    src = "//contents-thumbnail2.fc2.com/w276/storage63000.contents.fc2.com/file/a.jpg"
    assert _full_image(src) == "https://storage63000.contents.fc2.com/file/a.jpg"
    assert _full_image("https://storage63000.contents.fc2.com/file/a.jpg").startswith("https://")


def test_parse_fc2_article():
    html = (FIXTURES / "fc2_2896377.html").read_text(encoding="utf-8")
    meta = parse_fc2(html, "FC2-PPV-2896377")
    assert meta["code"] == "FC2-PPV-2896377"
    assert meta["source"] == "fc2"
    assert "乱交" in meta["title"]
    assert "90%OFF" not in meta["title"]
    assert meta["cover"] == "https://storage63000.contents.fc2.com/file/377/37682362/1652683602.07.jpg"
    assert meta["release_date"] == "2022-05-21"
    assert meta["runtime"] == "43分鐘"
    assert meta["studio"] == "素人0930"
    assert meta["studio_url"] == ""
    assert meta["url"].endswith("/article/2896377/")
    names = {item["name"] for item in meta["genres"]}
    assert {"人妻", "中出し", "巨乳"} <= names
    assert len(meta["samples"]) >= 2
    assert meta["samples"][0]["full"].startswith("https://storage201000.contents.fc2.com/")
    assert "貴重" in (meta.get("plot") or "")


def test_parse_fc2_missing_product():
    html = (FIXTURES / "fc2_missing.html").read_text(encoding="utf-8")
    with pytest.raises(MetadataError, match="未找到"):
        parse_fc2(html, "FC2-PPV-3237415")


def test_parse_fc2_drops_placeholder_cover():
    html = (FIXTURES / "fc2_noimage.html").read_text(encoding="utf-8")
    meta = parse_fc2(html, "FC2-PPV-1111111")
    assert meta["title"] == "测试无封面"
    assert meta["cover"] == ""
    assert meta["release_date"] == "2024-01-02"
