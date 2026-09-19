from app.downloader.xunlei import file_index_from_list, map_phase


def test_file_index_single():
    listed = {"list": {"resources": [{"name": "a", "file_count": 1, "file_size": 10}]}}
    assert file_index_from_list(listed) == "--1,"


def test_file_index_multi():
    listed = {
        "list": {
            "resources": [{
                "name": "pack",
                "file_count": 3,
                "file_size": 100,
                "dir": {"resources": [
                    {"file_index": 0},
                    {"file_index": 2},
                ]},
            }]
        }
    }
    assert file_index_from_list(listed) == "0-2"


def test_map_phase_running():
    assert map_phase({"phase": "PHASE_TYPE_RUNNING", "params": {"status": '{"phase":"running"}'}}) == "active"


def test_map_phase_complete():
    assert map_phase({"phase": "PHASE_TYPE_COMPLETE", "params": {}}) == "complete"
