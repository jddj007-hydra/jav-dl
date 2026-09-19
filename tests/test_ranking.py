from app.ranking import detect_tags, is_pack, sort_resources
from app.textutil import parse_size, format_size


def test_detect_uc_and_c():
    assert detect_tags("SSIS-001UC 无码破解")[0] == "UC"
    assert "C" in detect_tags("SSIS-001C 中文字幕")
    assert "U" in detect_tags("SSIS-001 无码")


def test_pack_by_size_and_title():
    assert is_pack("SSIS 001-500", parse_size("3.09 TB"))
    assert is_pack("合集打包", 100)
    assert not is_pack("SSIS-001 葵つかさ", parse_size("6.11 GB"))


def test_sort_prefers_tagged_relevant():
    items = [
        {"title": "SSIS 001-500 合集", "heat": 9999, "size": "3.09 TB"},
        {"title": "广告站 SSIS-001C", "heat": 10, "size": "2.06 GB"},
        {"title": "SSIS-001 一ヶ月間の禁慾 中文字幕", "heat": 596, "size": "6.11 GB"},
    ]
    ranked = sort_resources(items, "SSIS-001")
    assert "中文字幕" in ranked[0]["title"]
    assert ranked[0]["rank"] == 1


def test_size_parse():
    assert parse_size("2.06 GB") == 2060000000
    assert format_size(2060000000).startswith("2.06")
