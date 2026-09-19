from app.codes import extract_code, extract_codes, normalize_code, title_mentions_code


def test_normalize_variants():
    assert normalize_code("ssis001") == "SSIS-001"
    assert normalize_code("SSIS-001") == "SSIS-001"
    assert normalize_code(" ssis_001 ") == "SSIS-001"
    assert normalize_code("IPX-123") == "IPX-123"


def test_normalize_rejects_garbage():
    assert normalize_code("") is None
    assert normalize_code("hello") is None
    assert normalize_code("SSIS") is None
    assert normalize_code("123-001") is None


def test_title_mentions_code():
    assert title_mentions_code("kks11.cc@SSIS-001C", "SSIS-001")
    assert title_mentions_code("SSIS001 葵つかさ", "SSIS-001")
    assert not title_mentions_code("IPX-999 别人", "SSIS-001")


def test_extract_code_from_torrent_names():
    assert extract_code("ssis-001-uncensored") == "SSIS-001"
    assert extract_code("kks11.cc@SSIS-001C") == "SSIS-001"
    assert extract_code("SSIS001 葵つかさ") == "SSIS-001"
    assert extract_code("IPX-643.mp4") == "IPX-643"
    assert extract_code("HD-1080.mp4") is None
    assert extract_code("FHD-1080 SSIS") is None
    assert extract_code("SSIS-001 IPX-999 合集") is None
    assert extract_codes("SSIS-001-C") == ["SSIS-001"]
