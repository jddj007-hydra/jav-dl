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


def test_normalize_date_and_fc2_codes():
    assert normalize_code("092126-001") == "092126-001"
    assert normalize_code("092126_001") == "092126-001"
    assert normalize_code("fc2-3237415") == "FC2-PPV-3237415"
    assert normalize_code("FC2PPV-3237415") == "FC2-PPV-3237415"
    assert normalize_code("FC2-PPV-3237415") == "FC2-PPV-3237415"


def test_title_mentions_code():
    assert title_mentions_code("kks11.cc@SSIS-001C", "SSIS-001")
    assert title_mentions_code("SSIS001 葵つかさ", "SSIS-001")
    assert not title_mentions_code("IPX-999 别人", "SSIS-001")
    assert title_mentions_code("FC2PPV-3237415-3.mp4", "FC2-PPV-3237415")
    assert title_mentions_code("FC2-3237415.mp4", "FC2-PPV-3237415")


def test_extract_code_from_torrent_names():
    assert extract_code("ssis-001-uncensored") == "SSIS-001"
    assert extract_code("kks11.cc@SSIS-001C") == "SSIS-001"
    assert extract_code("SSIS001 葵つかさ") == "SSIS-001"
    assert extract_code("IPX-643.mp4") == "IPX-643"
    assert extract_code("092126-001.mp4") == "092126-001"
    assert extract_code("FC2PPV-3237415-3.mp4") == "FC2-PPV-3237415"
    assert extract_code("FC2-3237415.mp4") == "FC2-PPV-3237415"
    assert all(not code[0].isdigit() for code in extract_codes("Brazzers.24.01.02.Ann.Example"))
    assert extract_code("HD-1080.mp4") is None
    assert extract_code("FHD-1080 SSIS") is None
    assert extract_code("SSIS-001 IPX-999 合集") is None
    assert extract_codes("SSIS-001-C") == ["SSIS-001"]


def test_jav_vr_maker():
    from app.codes import jav_vr_maker

    assert jav_vr_maker("DSVR-1124") == "DSVR"
    assert jav_vr_maker("PPVR-002") == "PPVR"
    assert jav_vr_maker("EXMO-011") == "EXMO"
    assert jav_vr_maker("DANDYHQVR-015") == "DANDYHQVR"
    assert jav_vr_maker("3DSVR-2028") == "3DSVR"
    assert jav_vr_maker("URVRSP-605") == "URVRSP"
    assert jav_vr_maker("SSIS-001") is None
    assert jav_vr_maker("092126-001") is None
    assert normalize_code("DANDYHQVR-015") == "DANDYHQVR-015"
    assert normalize_code("3DSVR-2028") == "3DSVR-2028"
    assert normalize_code("URVRSP-605") == "URVRSP-605"
    assert normalize_code("CBIKMV001") == "CBIKMV-001"
    assert extract_code("DANDYHQVR-015.mp4") == "DANDYHQVR-015"
    assert extract_code("3DSVR-2028") == "3DSVR-2028"
    assert extract_code("urvrsp605") == "URVRSP-605"


def test_date_code_in_studio_folder_counts_as_jav_vr():
    from app.codes import is_jav_vr_entry, jav_vr_maker

    assert jav_vr_maker("021622-001") is None
    assert is_jav_vr_entry("DSVR-1124")
    assert is_jav_vr_entry("DSVR-1124", "DSVR")
    assert not is_jav_vr_entry("SSIS-001", "202102")
    assert not is_jav_vr_entry("092126-001", "202102")
    assert is_jav_vr_entry("021622-001", "Caribbean")
    assert is_jav_vr_entry("012822-01", "10mu")
    assert not is_jav_vr_entry("021622-001")
    assert not is_jav_vr_entry("021622-001", "")
