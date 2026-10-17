from app.paging import PAGE_SIZE, extend_wall, last_page_of, slice_page


def test_page_size_fills_common_grids():
    assert PAGE_SIZE == 24
    assert PAGE_SIZE % 4 == 0
    assert PAGE_SIZE % 6 == 0
    assert PAGE_SIZE % 2 == 0
    assert PAGE_SIZE % 3 == 0


def test_extend_wall_skips_duplicates_and_stops_on_a_short_page():
    first = extend_wall(
        None,
        [{"id": "a"}, {"id": "b"}],
        src=1,
        raw_count=24,
        full=24,
    )
    assert [row["id"] for row in first["items"]] == ["a", "b"]
    assert first["exhausted"] is False
    again = extend_wall(
        first,
        [{"id": "b"}, {"id": "a"}],
        src=2,
        raw_count=24,
        full=24,
    )
    assert [row["id"] for row in again["items"]] == ["a", "b"]
    assert again["exhausted"] is True
    short = extend_wall(
        None,
        [{"code": "A"}, {"code": "B"}],
        src=1,
        raw_count=2,
        full=20,
        id_key="code",
    )
    assert short["exhausted"] is True
    assert slice_page(short["items"], 1) == short["items"]
    assert last_page_of(24) == 1
    assert last_page_of(25) == 2
