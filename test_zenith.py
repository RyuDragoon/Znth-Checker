"""Offline tests: builds a fake library and fakes TMDB, the tracker API and Upload-Assistant.

    python3 test_zenith.py
"""
import builtins
import json
import os
import tempfile
import time
from pathlib import Path

import zenith_check as zc

OLD = time.time() - 3 * 3600      # files older than min_age_minutes


def mk(root, rel, size_mb=200, mtime=OLD):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(size_mb * 1024 * 1024)
    os.utime(p, (mtime, mtime))


def aired(n, total=None):
    return {"episodes": [{"air_date": "2010-01-01"}] * n + [{"air_date": "2099-01-01"}] * ((total or n) - n)}


TMDB = {
    ("movie", "inception"): [{"id": 27205, "title": "Inception", "release_date": "2010-07-15", "genre_ids": [28]}],
    ("movie", "dune"): [{"id": 438631, "title": "Dune", "release_date": "2021-09-15", "genre_ids": [878]}],
    ("movie", "obscure film"): [{"id": 999, "title": "Obscure Film", "release_date": "2015-01-01", "genre_ids": []}],
    ("movie", "night watch"): [{"id": 4444, "title": "The Night Watch", "release_date": "2004-07-08", "genre_ids": []}],
    ("tv", "breaking bad"): [{"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20", "genre_ids": [18]}],
    ("tv", "the wire"): [{"id": 1438, "name": "The Wire", "first_air_date": "2002-06-02", "genre_ids": [80]}],
    ("tv", "airing show"): [{"id": 5000, "name": "Airing Show", "first_air_date": "2026-09-01", "genre_ids": []}],
    ("tv", "mixed show"): [{"id": 6000, "name": "Mixed Show", "first_air_date": "2020-01-01", "genre_ids": []}],
    ("tv", "frieren"): [{"id": 209867, "name": "Frieren: Beyond Journey's End", "original_name": "Frieren",
                         "first_air_date": "2023-09-29", "genre_ids": [16], "original_language": "ja"}],
}
SEASONS = {(1396, 1): aired(3), (1438, 1): aired(13), (5000, 1): aired(2, 10)}
SITE = {
    27205: [{"name": "Inception 2010 1080p BluRay x264-GRP", "resolution": "Full HD", "type": "BD Encode",
             "type_id": 3, "resolution_id": 3}],
    438631: [{"name": "Dune 2021 1080p WEB-DL", "resolution": "1080p", "type": "WEB-DL"}],
    1396: [{"name": "Breaking Bad S01E01 1080p", "resolution": "1080p", "type": "WEB-DL",
            "season_number": 1, "episode_number": 1}],
}
seen_headers = []


class Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.headers = data, code, {}

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class FakeSession:
    headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        if "themoviedb" in url:
            if "/season/" in url:
                parts = url.rstrip("/").split("/")
                return Resp(SEASONS.get((int(parts[-3]), int(parts[-1])), {"episodes": []}))
            kind = "movie" if "/search/movie" in url else "tv"
            return Resp({"results": TMDB.get((kind, params["query"].lower()), [])})
        seen_headers.append(headers or {})
        if url.endswith("/api/blacklists/releasegroups"):
            return Resp({"data": [{"name": "NEWBAD", "reason": "Site-only ban"}], "meta": {"next_cursor": None}})
        p = dict(params)
        assert "api_token" not in p, "API key must not be in the URL"
        rows = SITE.get(p.get("tmdbId"), [])
        if "seasonNumber" in p:
            rows = [r for r in rows if r.get("season_number") == p["seasonNumber"]]
        return Resp({"data": [{"attributes": r} for r in rows]})


def build_library(lib):
    mk(lib, "Movies/Inception (2010)/Inception.2010.1080p.BluRay.x264-GRP.mkv")       # DUPE
    mk(lib, "Movies/Inception (2010)/Sample/sample.mkv", 50)                            # ignored
    mk(lib, "Movies/Dune (2021)/Dune.2021.2160p.UHD.BluRay.REMUX.HDR.HEVC-FraMeSToR.mkv")    # NEW_QUALITY
    mk(lib, "Movies/Obscure.Film.2015.1080p.WEB-DL.DDP5.1.H.264-XYZ.mkv")               # MISSING
    mk(lib, "Backup/Obscure.Film.2015.1080p.WEB-DL.DDP5.1.H.264-XYZ.mkv")               # MISSING, same as above
    mk(lib, "Movies/Night.Watch.2004.720p.WEB-DL.DDP5.1.H.264-XYZ.mkv")                 # MISSING, loose TMDB match
    mk(lib, "Movies/Unknown.Thing.1999.720p.HDTV.x264-ABC.mkv")                          # UNMATCHED
    mk(lib, "Movies/Obscure.Film.2015.720p.HDTV.x264-ABC.mkv")                           # SKIPPED: no HDTV type
    mk(lib, "Movies/Bad.Movie.2020.1080p.WEB-DL-YIFY.mkv")                               # SKIPPED banned
    mk(lib, "Movies/Galaxy.Film.2020.1080p.WEBRip.x265-GalaxyRG265[TGx].mkv")            # SKIPPED banned (built-in)
    mk(lib, "Movies/Live.Ban.2020.1080p.WEB-DL.H.264-NEWBAD.mkv")                       # SKIPPED banned (site's live list)
    for e, grp in ((1, "NTb"), (2, "FGT")):
        mk(lib, f"TV/Banned Pack/Season 01/Banned.Pack.S01E0{e}.1080p.WEB-DL.H.264-{grp}.mkv")  # one bad episode
    mk(lib, "Movies/Fresh.Download.2021.1080p.WEB-DL-GRP.mkv", mtime=time.time())       # too new, not scanned
    for e in (1, 2, 3):
        mk(lib, f"TV/Breaking Bad/Season 01/Breaking.Bad.S01E0{e}.1080p.WEB-DL.DDP5.1.H.264-NTb.mkv")  # MISSING pack
    for e in (1, 2):
        mk(lib, f"TV/The Wire/Season 01/The.Wire.S01E0{e}.1080p.WEB-DL.H.264-NTb.mkv")  # incomplete pack
    for e in (1, 2):
        mk(lib, f"TV/Airing Show/Season 01/Airing.Show.S01E0{e}.1080p.WEB-DL.H.264-NTb.mkv")  # still airing
    for name in ("S01E01", "S01E02", "S02E01"):
        mk(lib, f"TV/Mixed Show/Mixed.Show.{name}.1080p.WEB-DL.H.264-NTb.mkv")         # two seasons, one folder
    for e in (1, 2):
        mk(lib, f"Anime/Frieren/[SubsPlease] Frieren - 0{e} (1080p) [ABCD1234].mkv")     # MISSING, anime
    mk(lib, "Movies/Some Disc 2005/BDMV/STREAM/00001.m2ts", 300)                         # full disc


def run():
    tmp = Path(tempfile.mkdtemp())
    lib = tmp / "Storage9"
    build_library(lib)
    ua = tmp / "UA"
    (tmp / "config.toml").write_text(f'''
[[directories]]
path = "{lib}"
type = "auto"
[tracker]
api_key = "SECRET"
request_delay = 0
[tracker.types]
"FULL DISC" = 1
REMUX = 2
ENCODE = 3
"WEB-DL" = 4
WEBRIP = 5
[tracker.resolutions]
"2160p" = 2
"1080p" = 3
"720p" = 5
[tmdb]
api_key = "TMDBKEY"
[scan]
banned_groups = ["YIFY"]
[upload_assistant]
path = "{ua}"
''')
    zc.STATE_DIR, zc.REPORT_DIR = tmp / "data", tmp / "reports"
    zc.requests.Session = FakeSession
    zc.time.sleep = lambda s: None
    cfg = zc.load_config(tmp / "config.toml")

    # ---- checking ----
    items = zc.scan_directories(cfg, verbose=True)
    zc.check_items(cfg, items, verbose=True)
    rep = zc.write_report(cfg, items)
    by = {}
    for i in items:
        by.setdefault(i.label, []).append(i)
        print("   ", i.label, i.verdict, i.reason, Path(i.path).name)

    def one(label):
        assert len(by[label]) == 1, (label, by[label])
        return by[label][0]

    assert one("Inception (2010)").verdict == "DUPE"
    assert one("Dune (2021)").verdict == "NEW_QUALITY"
    assert sorted(i.verdict for i in by["Obscure Film (2015)"]) == ["MISSING", "MISSING", "SKIPPED"]
    nw = one("Night Watch (2004)")
    assert nw.verdict == "MISSING" and nw.tmdb_title == "The Night Watch" and not nw.confident, nw
    assert one("Unknown Thing (1999)").verdict == "UNMATCHED"
    assert one("Bad Movie (2020)").verdict == "SKIPPED"
    assert one("Galaxy Film (2020)").verdict == "SKIPPED" and "GalaxyRG" in one("Galaxy Film (2020)").reason
    assert one("Live Ban (2020)").verdict == "SKIPPED" and "Site-only ban" in one("Live Ban (2020)").reason
    assert one("Banned Pack S01").verdict == "SKIPPED" and "FGT" in one("Banned Pack S01").reason
    assert "Fresh Download (2021)" not in by
    bb = one("Breaking Bad S01")
    assert bb.verdict == "MISSING" and Path(bb.path).name == "Season 01" and bb.confident, bb
    assert one("The Wire S01").verdict == "SKIPPED" and "incomplete" in one("The Wire S01").reason
    assert one("Airing Show S01").verdict == "SKIPPED" and "airing" in one("Airing Show S01").reason
    # a folder holding two seasons is never treated as one pack
    assert {"Mixed Show S01E01", "Mixed Show S01E02", "Mixed Show S02E01"} <= set(by) and "Mixed Show S01" not in by
    fr = one("Frieren")
    assert fr.verdict == "MISSING" and fr.anime and Path(fr.path).name == "Frieren"
    assert Path(one("Some Disc (2005)").path).name == "Some Disc 2005"
    assert not any("sample" in i.path.lower() for i in items)
    for f in zc.REPORT_DIR.iterdir():
        assert "SECRET" not in f.read_text(), f
    assert seen_headers and all(h.get("Authorization") == "Bearer SECRET" for h in seen_headers)

    # ---- uploading against a stub Upload-Assistant that records its arguments ----
    (ua / "src" / "trackers").mkdir(parents=True)
    (ua / "data").mkdir()
    (ua / "upload.py").write_text(
        "import sys, json\nopen(__file__ + '.calls', 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")
    setup = ua / "src" / "trackersetup.py"
    setup.write_text("from src.trackers.ULCX import ULCX\n\ntracker_class_map = {\n    'ULCX': ULCX,\n}\n\n"
                     "api_trackers = {\n    'ULCX',\n}\n")
    (ua / "src" / "trackers" / "UNIT3D.py").write_text("")
    cfg_py = ua / "data" / "config.py"
    cfg_py.write_text('config = {\n    "DEFAULT": {\n        "tmdb_api": "",\n    },\n'
                      '    "TRACKERS": {\n        "default_trackers": "",\n    },\n}\n')
    calls_file = ua / "upload.py.calls"
    done_file = zc.STATE_DIR / "uploaded.json"

    def calls():
        return [json.loads(l) for l in calls_file.read_text().splitlines()] if calls_file.exists() else []

    def reset():
        for f in (calls_file, done_file, zc.STATE_DIR / "uploaded_keys.json"):
            if f.exists():
                f.unlink()

    def refused(fn):
        try:
            fn()
        except SystemExit as e:
            return str(e)
        raise AssertionError("should have refused")

    assert zc.parse_verdicts("missing,new_quality", cfg) == {"MISSING", "NEW_QUALITY"}
    assert zc.parse_verdicts(None, cfg) == {"MISSING"}

    # Preflight: ZNTH not registered, no ZNTH section, keep_images missing (= on) -> refuse
    msg = refused(lambda: zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True))
    assert "registered" in msg and "ZNTH section" in msg and "keep_images" in msg, msg
    assert calls() == []

    # install-ua's own steps fix all three
    zc.register_znth(ua)
    zc.write_ua_config(ua, own_screenshots=True)
    text = cfg_py.read_text()
    compile(text, "config.py", "exec")
    assert '"keep_images": False,' in text and '"ZNTH"' in text and not zc.keep_images_on(text), text
    assert "'ZNTH': ZNTH" in setup.read_text() and zc.unregister_znth(setup.read_text()) == \
        setup.read_text().replace("from src.trackers.ZNTH import ZNTH\n", "").replace("\n    'ZNTH': ZNTH,", "").replace("\n    'ZNTH',", "")

    # ...but the ZNTH api_key is still empty -> refuse
    msg = refused(lambda: zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True))
    assert "api_key" in msg and "empty" in msg, msg
    cfg_py.write_text(cfg_py.read_text().replace('"api_key": ""', '"api_key": "UAKEY"'))

    # Unattended, MISSING only: Obscure Film once (copy held back), Breaking Bad pack, Frieren.
    # Night Watch (loose TMDB match) and every non-MISSING item stay out.
    zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True)
    c = calls()
    sent = sorted(Path(x[0]).name for x in c)
    assert len(c) == 6, sent      # Obscure Film, Breaking Bad pack, Frieren, 3 single Mixed Show episodes
    assert sum("Obscure.Film.2015.1080p" in x[0] for x in c) == 1
    assert not any("Night.Watch" in x[0] or "Inception" in x[0] or "Wire" in x[0] or "Airing" in x[0] for x in c)
    for flag in ("--unattended", "--skip-dupe-asking", "--onlyID", "--trackers", "ZNTH"):
        assert all(flag in x for x in c), flag
    # nothing gets sent twice, including the second copy of Obscure Film on a later run
    zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True)
    assert len(calls()) == 6

    # NEW_QUALITY only
    zc.upload(cfg, rep, {"NEW_QUALITY"}, True, None, False, unattended=True)
    assert len(calls()) == 7 and "Dune" in calls()[-1][0]

    # Interactive mode still offers the low-confidence item (a human can judge it)
    reset()
    answers = iter(["n"] * 20)
    builtins.input = lambda prompt="": next(answers)
    zc.upload(cfg, rep, {"MISSING"}, False, None, False, unattended=False)
    assert calls() == []

    # Unattended without --yes: one confirmation; anything but 'upload' cancels; no terminal cancels too
    builtins.input = lambda prompt="": "no"
    zc.upload(cfg, rep, {"MISSING"}, False, None, False, unattended=True)
    assert calls() == []

    def no_tty(prompt=""):
        raise EOFError
    builtins.input = no_tty
    zc.upload(cfg, rep, {"MISSING"}, False, None, False, unattended=True)
    assert calls() == []
    builtins.input = lambda prompt="": "upload"
    zc.upload(cfg, rep, {"MISSING"}, False, 1, False, unattended=True)
    assert len(calls()) == 1

    # --debug rehearsal: Upload-Assistant runs, but nothing is marked as uploaded
    reset()
    cfg["upload_assistant"]["extra_args"] = ["--debug"]
    zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True)
    assert len(calls()) == 6 and not done_file.exists()
    cfg["upload_assistant"]["extra_args"] = []

    # Stale report: unattended refuses
    old = time.time() - 30 * 3600
    os.utime(rep, (old, old))
    assert "hours old" in refused(lambda: zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True))
    os.utime(rep, None)

    # keep_images switched back on by hand -> refuse again
    cfg_py.write_text(cfg_py.read_text().replace('"keep_images": False', '"keep_images": True'))
    assert "keep_images" in refused(lambda: zc.upload(cfg, rep, {"MISSING"}, True, None, False, unattended=True))

    # Config edits that would break Upload-Assistant's config.py are refused, file untouched
    broken = 'config = {\n    "DEFAULT": {\n        "keep_images": True\n    },\n    "TRACKERS": {\n        "default_trackers": ""\n    },\n}\n'
    cfg_py.write_text(broken)
    refused(lambda: zc.write_ua_config(ua, own_screenshots=True))
    assert cfg_py.read_text() == broken

    # Reports written by an older version (extra or missing fields) still load
    data = json.loads(rep.read_text())
    data[0]["some_old_field"] = 1
    data[1].pop("tmdb_score")
    rep.write_text(json.dumps(data))
    assert len(zc.load_report(rep)) == len(data)

    # ---- the short command line maps onto the same upload settings ----
    got = []
    real = (zc.upload, zc.scan_directories, zc.check_items, zc.write_report)
    zc.upload = lambda cfg_, report, verdicts, yes, limit, dry, unatt: got.append((sorted(verdicts), limit, dry, unatt))
    zc.scan_directories = lambda cfg_, verbose=False: []
    zc.check_items = lambda cfg_, items_, verbose=False: None
    zc.write_report = lambda cfg_, items_: rep
    cfgp = str(tmp / "config.toml")
    zc.main(["-c", cfgp, "auto"])
    zc.main(["-c", cfgp, "auto", "--all", "-l", "10"])
    zc.main(["-c", cfgp, "auto", "--new", "-n"])
    zc.main(["-c", cfgp, "run", "--unattended", "--yes", "--only", "missing,new_quality", "--limit", "3"])
    zc.main(["-c", cfgp, "upload", "--include-new-quality"])
    assert got == [(["MISSING"], None, False, True), (["MISSING", "NEW_QUALITY"], 10, False, True),
                   (["NEW_QUALITY"], None, True, True), (["MISSING", "NEW_QUALITY"], 3, False, True),
                   (["MISSING", "NEW_QUALITY"], None, False, False)], got

    # ---- presets: by name, with overrides, and from the bare-run menu ----
    got.clear()
    zc.main(["-c", cfgp, "nightly"])                       # built-in defaults (no [presets] in this config)
    zc.main(["-c", cfgp, "nightly", "-l", "3", "--new"])   # typed flags win
    zc.main(["-c", cfgp, "rehearsal"])
    assert got == [(["MISSING", "NEW_QUALITY"], 10, False, True), (["NEW_QUALITY"], 3, False, True),
                   (["MISSING", "NEW_QUALITY"], None, True, True)], got

    class Tty:
        def isatty(self):
            return True
    real_stdin = zc.sys.stdin
    zc.sys.stdin = Tty()
    got.clear()
    names = list(zc.DEFAULT_PRESETS)
    builtins.input = lambda prompt="": str(names.index("missing") + 1)
    zc.main(["-c", cfgp])                                   # bare at a terminal -> menu -> "missing"
    builtins.input = lambda prompt="": "nightly"            # or type the name
    zc.main(["-c", cfgp])
    builtins.input = lambda prompt="": "q"
    zc.main(["-c", cfgp])                                   # quit does nothing
    zc.sys.stdin = real_stdin
    assert got == [(["MISSING"], None, False, True), (["MISSING", "NEW_QUALITY"], 10, False, True)], got

    # your own presets replace the defaults; bad ones are refused with a plain message
    own = (tmp / "config.toml").read_text() + '\n[presets.weekend]\naction = "auto"\nwhat = "new"\nlimit = 2\n'
    (tmp / "own.toml").write_text(own)
    got.clear()
    zc.main(["-c", str(tmp / "own.toml"), "weekend"])
    assert got == [(["NEW_QUALITY"], 2, False, True)], got
    assert "Unknown command or preset" in refused(lambda: zc.main(["-c", str(tmp / "own.toml"), "nightly"]))
    for bad in ('[presets.check]\naction = "check"\n', '[presets.x]\naction = "explode"\n',
                '[presets.x]\naction = "auto"\nwhat = "everything"\n'):
        (tmp / "bad.toml").write_text((tmp / "config.toml").read_text() + "\n" + bad)
        refused(lambda: zc.load_config(tmp / "bad.toml"))
    zc.upload, zc.scan_directories, zc.check_items, zc.write_report = real

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    run()
