TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://tracker.cyberia.is:6969/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
    "http://sukebei.tracker.wf:8888/announce",
]

DHT_ENTRYPOINT = "dht.transmissionbt.com:6881"


def magnet_for(info_hash: str, title: str = "") -> str:
    from urllib.parse import quote

    parts = [f"magnet:?xt=urn:btih:{info_hash.lower()}"]
    if title:
        parts.append(f"dn={quote(title)}")
    parts.extend(f"tr={quote(t, safe='')}" for t in TRACKERS)
    return "&".join(parts)


def aria2_tracker_arg() -> str:
    return ",".join(TRACKERS)
