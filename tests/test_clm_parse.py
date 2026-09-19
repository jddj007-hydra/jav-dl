from pathlib import Path

from app.sources.clm import (
    clm_id_to_infohash,
    parse_search_html,
    unwrap_search_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_base32_id_to_infohash():
    assert (
        clm_id_to_infohash("hbxayibbc325p3oy6krdmvowsa35klpc")
        == "386e0c202116f5d7edd8f2a23655d69037d52de2"
    )
    assert clm_id_to_infohash("386E0C202116F5D7EDD8F2A23655D69037D52DE2") == (
        "386e0c202116f5d7edd8f2a23655d69037d52de2"
    )


def test_unwrap_wrapped_page():
    html = (FIXTURES / "clm_search_wrapped.html").read_text(encoding="utf-8")
    inner, redirect = unwrap_search_html(html)
    assert redirect is None
    assert "SearchListTitle_result_title" in inner


def test_parse_decoded_list():
    html = (FIXTURES / "clm_search_decoded.html").read_text(encoding="utf-8")
    items = parse_search_html(html, "SSIS-001")
    assert len(items) >= 5
    hashes = {i["info_hash"] for i in items}
    assert "386e0c202116F5d7edd8f2a23655d69037d52de2".lower() in hashes
    first = next(i for i in items if i["info_hash"].startswith("386e"))
    assert first["title"].startswith("kks11.cc@")
    assert first["magnet"].startswith("magnet:?xt=urn:btih:")
    assert first["size_bytes"]
