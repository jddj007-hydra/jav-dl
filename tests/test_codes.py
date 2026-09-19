from app.codes import normalize_code, title_mentions_code


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
