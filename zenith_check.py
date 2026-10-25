#!/usr/bin/env python3
"""
Zenith exists checker.

Walks your media folders, works out what each movie / TV season / anime is,
asks znth.cx (a UNIT3D tracker) whether it is already there, and writes a
report. Uploading is a separate, opt-in step that hands the missing items to
Upload-Assistant.

    python3 zenith_check.py setup            # once: install Upload-Assistant with the ZNTH module
    python3 zenith_check.py check            # scan + check, write a report (no uploads)
    python3 zenith_check.py upload           # upload from that report, asking before each one
    python3 zenith_check.py auto --all       # check + upload unattended (see README)

No Sonarr / Radarr needed.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

import requests
from guessit import guessit

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.toml"
STATE_DIR = HERE / "data"
REPORT_DIR = HERE / "reports"

TMDB_API = "https://api.themoviedb.org/3"

# Verdicts, in the order they appear in the report.
MISSING = "MISSING"          # title (or this season) is not on the site at all
NEW_QUALITY = "NEW_QUALITY"  # title is there, but not in your resolution + type
DUPE = "DUPE"                # same resolution + type (or same release) already there
UNMATCHED = "UNMATCHED"      # couldn't identify the title, check it by hand
SKIPPED = "SKIPPED"          # banned group, ignored, etc.
ERROR = "ERROR"
VERDICT_ORDER = [MISSING, NEW_QUALITY, UNMATCHED, DUPE, SKIPPED, ERROR]

DISC_MARKERS = {"BDMV", "VIDEO_TS"}

# znth.cx's banned release groups (from the site's list, 2026-10-25). config.toml's
# scan.banned_groups adds to these, and `check` also pulls the live list from the site when it can.
ZNTH_BANNED_GROUPS = {
    "4K4U": "Quality", "Alcaide_Kira": "Retagging", "AROMA": "Retagging", "aXXo": "Quality",
    "BiTOR": "Faking DV/Atmos, falsifying MediaInfo", "BlackTV": "Retags", "BRrip": "Quality",
    "CM8": "Quality", "CrEwSaDe": "Quality", "d3g": "Quality", "DNL": "Quality", "EVO": "Quality",
    "FaNGDiNG0": "Quality", "FGT": "Retagging", "FRDS": "Quality", "GalaxyRG": "Quality",
    "GalaxyTV": "Quality", "HD2DVD": "Quality", "HDTime": "Quality", "Hi10": "Re-encoding",
    "ION10": "Quality", "iPlanet": "Quality", "KiNGDOM": "Quality", "LAMA": "Retagging",
    "MeGusta": "Quality", "mHD": "Quality", "mSD": "Quality", "NhaNc3": "Quality", "nHD": "Quality",
    "nikt0": "Quality", "nSD": "Quality", "OFT": "Quality, nikt0 alt", "PRODJi": "Quality",
    "RARBG": "Quality", "SANTi": "Quality", "SPDVD": "Modified full discs", "STUTTERSHIT": "Quality",
    "Telly": "Retagging", "TGx": "Quality", "TSP": "Quality", "TSPxL": "Quality", "WAF": "Quality",
    "x0r": "Quality", "YIFY": "Quality", "YTS": "Quality",
}
# Banned names that show up as a word anywhere in a release name, not only in the group slot.
BANNED_ANYWHERE = {"brrip"}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

COMMANDS = {"setup", "install-ua", "check", "upload", "auto", "run", "probe"}

# Preset `action` -> the command line it stands for.
PRESET_ACTIONS = {
    "check": ["check"],             # scan + check the site, write a report
    "local": ["check", "--local"],  # only list what's on disk
    "upload": ["upload"],           # upload from the last report, asking before each
    "auto": ["auto"],               # check + unattended upload
    "setup": ["setup"],             # install Upload-Assistant / the ZNTH module
    "update": ["setup", "--update"],
    "probe": ["probe"],
}
PRESET_WHAT = {"default": [], "missing": ["--missing"], "new": ["--new"], "all": ["--all"]}

# Used when config.toml has no [presets.*] of its own. Same as config.example.toml.
DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "report": {"description": "See what's missing on the site (no uploads)", "action": "check"},
    "ask": {"description": "Upload what's missing, asking before each one", "action": "upload", "what": "missing"},
    "rehearsal": {"description": "Dry run: what would an unattended upload send?", "action": "auto",
                  "what": "all", "dry_run": True},
    "missing": {"description": "Upload everything missing, unattended", "action": "auto", "what": "missing"},
    "nightly": {"description": "Missing + new quality, unattended, at most 10", "action": "auto",
                "what": "all", "limit": 10},
    "update": {"description": "Update Upload-Assistant and the ZNTH module", "action": "update"},
}


def preset_argv(preset: dict[str, Any], extra: list[str]) -> list[str]:
    """A preset as a command line. Flags typed after the preset name win over the preset's own."""
    argv = list(PRESET_ACTIONS[preset["action"]])
    if preset["action"] in ("upload", "auto"):
        if not any(a in extra for a in ("--missing", "--new", "--all", "--only")):
            argv += PRESET_WHAT[preset.get("what", "default")]
        if preset.get("limit"):
            argv += ["--limit", str(preset["limit"])]
        if preset.get("dry_run"):
            argv.append("--dry-run")
    return argv + extra


def _split_config_arg(argv: list[str]) -> tuple[list[str], list[str]]:
    """Pull -c/--config off the front so presets can be read before the real parse."""
    head: list[str] = []
    rest = list(argv)
    while rest and rest[0].startswith("-"):
        a = rest.pop(0)
        head.append(a)
        if a in ("-c", "--config") and rest:
            head.append(rest.pop(0))
    return head, rest


def menu(cfg: dict[str, Any]) -> Optional[list[str]]:
    """Numbered menu built from the presets. Returns the command line to run, or None."""
    names = list(cfg["presets"])
    print("Zenith checker. Pick one (edit the list in config.toml under [presets.*]):\n")
    descs = [cfg["presets"][name].get("description") or name for name in names]
    width = max(len(d) for d in descs)
    for n, (name, desc) in enumerate(zip(names, descs), 1):
        print(f"  {n:>2}. {desc:<{width}}   {name}")
    print("   q. Quit\n")
    ans = _ask("Choice: ", on_eof="q")
    if ans in ("q", "quit", ""):
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        name = names[int(ans) - 1]
    elif ans in cfg["presets"]:
        name = ans
    else:
        print(f"No option {ans!r}.")
        return None
    argv = preset_argv(cfg["presets"][name], [])
    print(f"\n> zenith_check.py {name}   ({' '.join(argv)})\n")
    return argv


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        sys.exit(f"Config not found: {path}\nCopy config.example.toml to config.toml and edit it.")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    tracker = cfg.setdefault("tracker", {})
    tracker.setdefault("url", "https://znth.cx")
    tracker["url"] = tracker["url"].rstrip("/")
    tracker.setdefault("request_delay", 2.0)
    tracker.setdefault("fetch_banned_groups", True)
    tracker["api_key"] = os.environ.get("ZNTH_API_KEY") or tracker.get("api_key", "")
    cats = tracker.setdefault("categories", {})
    cats.setdefault("movie", [1])
    cats.setdefault("tv", [2])
    cats.setdefault("anime_movie", [])
    cats.setdefault("anime_tv", [])
    # name -> site id. Empty means "accept anything" (used for UNSUPPORTED checks).
    tracker["types"] = {k.upper(): int(v) for k, v in tracker.get("types", {}).items()}
    tracker["resolutions"] = {k.lower(): int(v) for k, v in tracker.get("resolutions", {}).items()}

    tmdb = cfg.setdefault("tmdb", {})
    tmdb["api_key"] = os.environ.get("TMDB_API_KEY") or tmdb.get("api_key", "")

    scan = cfg.setdefault("scan", {})
    scan.setdefault("extensions", ["mkv", "mp4", "avi", "m2ts", "ts", "wmv", "mov"])
    scan.setdefault("min_size_mb", 100)
    scan.setdefault("ignore", ["sample", "extras", "featurettes", "trailers", "behind the scenes"])
    scan.setdefault("tv_mode", "season")
    scan.setdefault("banned_groups", [])
    scan.setdefault("anime_folder_hints", ["anime"])
    scan.setdefault("min_age_minutes", 60)
    if scan["tv_mode"] not in ("season", "episode"):
        sys.exit(f'scan.tv_mode must be "season" or "episode", not {scan["tv_mode"]!r}')

    cfg.setdefault("directories", [])
    for n, d in enumerate(cfg["directories"], 1):
        if not isinstance(d, dict) or not d.get("path"):
            sys.exit(f"[[directories]] block #{n} in {path} has no path = \"...\"")
        if d.get("type", "auto").lower() not in ("auto", "movie", "tv", "anime", "anime_movie"):
            sys.exit(f"[[directories]] {d['path']}: type must be auto, movie, tv, anime or anime_movie")
    ua = cfg.setdefault("upload_assistant", {})
    ua.setdefault("path", "~/Upload-Assistant")
    if not ua["path"]:
        ua["path"] = "~/Upload-Assistant"
    ua.setdefault("python", sys.executable or "python3")
    ua.setdefault("tracker", "ZNTH")
    ua.setdefault("extra_args", [])
    ua.setdefault("verdicts", ["MISSING"])
    ua.setdefault("own_screenshots", True)
    ua.setdefault("unattended_limit", 0)

    presets = cfg.get("presets") or DEFAULT_PRESETS
    for name, pr in presets.items():
        where = f"[presets.{name}] in {path}"
        if name in COMMANDS:
            sys.exit(f"{where}: {name!r} is also a command name; call the preset something else.")
        if not isinstance(pr, dict) or pr.get("action") not in PRESET_ACTIONS:
            sys.exit(f"{where}: action must be one of {', '.join(PRESET_ACTIONS)}")
        if pr.get("what", "default") not in PRESET_WHAT:
            sys.exit(f"{where}: what must be missing, new, all (or leave it out for the config default)")
        if "limit" in pr and (not isinstance(pr["limit"], int) or pr["limit"] < 0):
            sys.exit(f"{where}: limit must be a whole number (0 = no limit)")
    cfg["presets"] = presets
    return cfg


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #

@dataclass
class Item:
    kind: str                     # "movie" or "tv"
    title: str
    path: str                     # what gets handed to Upload-Assistant
    root: str
    anime: bool = False
    year: Optional[int] = None
    season: Optional[int] = None
    episodes: list[int] = field(default_factory=list)
    resolution: Optional[str] = None
    release_type: Optional[str] = None   # FULL DISC / REMUX / ENCODE / WEB-DL / WEBRIP / HDTV / OTHER
    group: Optional[str] = None
    tags: list[str] = field(default_factory=list)   # every spot a group name can hide in the name(s)
    size_bytes: int = 0
    file_count: int = 1
    # filled in by the checker
    tmdb_id: Optional[int] = None
    tmdb_title: Optional[str] = None
    tmdb_year: Optional[int] = None
    tmdb_score: float = 0.0       # title similarity to the TMDB match, 0..1
    verdict: str = ""
    reason: str = ""
    site_matches: list[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        """Safe to upload without a human looking: a near-exact TMDB title, and the year agrees."""
        if not self.tmdb_id or self.tmdb_score < 0.9:
            return False
        if self.kind == "movie":
            return bool(self.year and self.tmdb_year and abs(self.year - self.tmdb_year) <= 1)
        return not (self.year and self.tmdb_year and abs(self.year - self.tmdb_year) > 1)

    @property
    def label(self) -> str:
        s = self.title
        if self.year:
            s += f" ({self.year})"
        if self.season is not None:
            s += f" S{self.season:02d}"
            if len(self.episodes) == 1:
                s += f"E{self.episodes[0]:02d}"
        return s


def release_type_from_guess(g: dict[str, Any], path: str) -> str:
    source = str(g.get("source") or "").lower()
    other = g.get("other") or []
    if isinstance(other, str):
        other = [other]
    other = [o.lower() for o in other]
    if "remux" in other or re.search(r"\bremux\b", path, re.I):
        return "REMUX"
    if "web" in source:
        return "WEBRIP" if "rip" in other or re.search(r"web-?rip", path, re.I) else "WEB-DL"
    if "hdtv" in source or "tv" == source:
        return "HDTV"
    if "blu-ray" in source or "dvd" in source or "hd-dvd" in source:
        return "ENCODE"
    return "OTHER"


def resolution_from_guess(g: dict[str, Any]) -> Optional[str]:
    size = g.get("screen_size")
    if not size:
        return None
    size = str(size).lower()
    return {"4k": "2160p", "8k": "4320p"}.get(size, size)


def _guess(rel: Path, forced: Optional[str]) -> dict[str, Any]:
    # Parent folder + filename is enough context ("Show/Season 1/01.mkv") without
    # letting the scan root's own name leak into the title.
    text = "/".join(rel.parts[-3:])
    opts = {"type": forced} if forced else {}
    return dict(guessit(text, opts))


def _is_ignored(path: Path, ignore: list[str]) -> bool:
    low = [p.lower() for p in path.parts]
    for word in ignore:
        w = word.lower()
        if any(part == w or part.startswith(w + ".") or re.search(rf"(^|[ ._\-\[(]){re.escape(w)}([ ._\-\])]|$)", part) for part in low):
            return True
    return False


def group_tags(name: str, release_group: Optional[str] = None) -> set[str]:
    """Lower-cased candidates for the release group: guessit's pick, the trailing -GROUP,
    [tags] / {tags} at either end, and those with a trailing x264/x265-style number dropped
    (GalaxyRG265 -> galaxyrg)."""
    stem = re.sub(r"\.(mkv|mp4|avi|m2ts|ts|wmv|mov)$", "", name, flags=re.I).strip()
    found: set[str] = set()
    if release_group:
        found.add(str(release_group))
    lead = re.match(r"^\s*[\[{(]([^\]})]+)[\]})]", stem)
    if lead:
        found.add(lead.group(1))
    while True:     # peel trailing [TGx] / {GROUP} / (...) tags
        m = re.search(r"[\[{(]([^\]})]*)[\]})]\s*$", stem)
        if not m:
            break
        found.add(m.group(1))
        stem = stem[:m.start()].rstrip(" .-_")
    m = re.search(r"-\s*([A-Za-z0-9_@]+)$", stem)
    if m:
        found.add(m.group(1))
    out: set[str] = set()
    for f in found:
        for part in [f, *re.split(r"[.\s]+", f)]:
            part = part.strip().lower()
            if part:
                out.add(part)
                if re.search(r"\d+$", part) and re.sub(r"\d+$", "", part):
                    out.add(re.sub(r"\d+$", "", part))
    words = {w.lower() for w in re.split(r"[.\s_\-\[\](){}]+", name) if w}
    out |= words & BANNED_ANYWHERE
    return out


def banned_list(cfg: dict[str, Any], extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    """lower-cased name -> display name + reason"""
    merged = {k.lower(): f"{k} ({v})" for k, v in ZNTH_BANNED_GROUPS.items()}
    for g in cfg["scan"]["banned_groups"]:
        merged.setdefault(str(g).lower(), str(g))
    for k, v in (extra or {}).items():
        merged.setdefault(k.lower(), v)
    return merged


def apply_banned(items: list["Item"], banned: dict[str, str]) -> int:
    hits = 0
    for it in items:
        if it.verdict == SKIPPED:
            continue
        match = sorted(t for t in it.tags if t in banned)
        if match:
            it.verdict, it.reason = SKIPPED, f"banned group {banned[match[0]]}"
            hits += 1
    return hits


def scan_directories(cfg: dict[str, Any], verbose: bool = False) -> list[Item]:
    scan = cfg["scan"]
    exts = {"." + e.lower().lstrip(".") for e in scan["extensions"]}
    min_size = int(scan["min_size_mb"]) * 1024 * 1024
    min_age = float(scan["min_age_minutes"]) * 60
    now = time.time()
    too_new = 0
    anime_hints = [h.lower() for h in scan["anime_folder_hints"]]
    items: list[Item] = []

    for entry in cfg["directories"]:
        root = Path(entry["path"]).expanduser()
        dtype = entry.get("type", "auto").lower()
        if not root.is_dir():
            print(f"! Directory not found, skipping: {root}")
            continue
        forced = {"movie": "movie", "tv": "episode", "anime": "episode", "anime_movie": "movie"}.get(dtype)
        print(f"Scanning {root} ({dtype})")
        episodes: dict[tuple[str, str, Optional[int]], list[tuple[Path, dict[str, Any]]]] = {}
        folder_videos: dict[str, int] = {}   # big-enough video files per folder, for the season-pack check

        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            rel_dir = d.relative_to(root)
            if _is_ignored(rel_dir, scan["ignore"]):
                dirnames[:] = []
                continue
            # Full discs: the folder holding BDMV / VIDEO_TS is one item.
            if DISC_MARKERS & set(dirnames) or d.name.upper() in DISC_MARKERS:
                disc_dir = d if d.name.upper() not in DISC_MARKERS else d.parent
                dirnames[:] = []
                if d.name.upper() in DISC_MARKERS:
                    continue
                disc_files = [f for f in disc_dir.rglob("*") if f.is_file()]
                if disc_files and now - max(f.stat().st_mtime for f in disc_files) < min_age:
                    too_new += 1
                    continue
                g = _guess(disc_dir.relative_to(root) if disc_dir != root else Path(disc_dir.name), forced)
                size = sum(f.stat().st_size for f in disc_files)
                items.append(Item(
                    kind="tv" if g.get("type") == "episode" else "movie",
                    title=str(g.get("title") or disc_dir.name), year=g.get("year"),
                    season=g.get("season") if g.get("type") == "episode" else None,
                    path=str(disc_dir), root=str(root),
                    anime=dtype.startswith("anime") or _has_hint(disc_dir, root, anime_hints),
                    resolution=resolution_from_guess(g), release_type="FULL DISC",
                    group=g.get("release_group"), size_bytes=size,
                    tags=sorted(group_tags(disc_dir.name, g.get("release_group"))),
                ))
                continue

            for name in filenames:
                p = d / name
                if p.suffix.lower() not in exts:
                    continue
                if _is_ignored(Path(name), scan["ignore"]):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                size = st.st_size
                if size < min_size:
                    continue
                folder_videos[str(d)] = folder_videos.get(str(d), 0) + 1
                if now - st.st_mtime < min_age:
                    too_new += 1          # probably still downloading / being copied
                    continue
                g = _guess(p.relative_to(root), forced)
                if g.get("type") == "episode" or dtype in ("tv", "anime"):
                    season = g.get("season")
                    if isinstance(season, list):
                        season = season[0]
                    key = (str(d), str(g.get("title") or d.name).lower(), season)
                    episodes.setdefault(key, []).append((p, g))
                else:
                    items.append(Item(
                        kind="movie", title=str(g.get("title") or p.stem), year=g.get("year"),
                        path=str(p), root=str(root),
                        anime=dtype.startswith("anime") or _has_hint(p, root, anime_hints),
                        resolution=resolution_from_guess(g),
                        release_type=release_type_from_guess(g, str(p)),
                        group=g.get("release_group"), size_bytes=size,
                        tags=sorted(group_tags(p.name, g.get("release_group"))),
                    ))

        # Group episodes into season packs (one folder = one season) or keep singles.
        groups_per_folder: dict[str, int] = {}
        for (folder, _, _) in episodes:
            groups_per_folder[folder] = groups_per_folder.get(folder, 0) + 1
        for (folder, _, season), eps in episodes.items():
            first_path, g = eps[0]
            ep_nums: list[int] = []
            for _, eg in eps:
                e = eg.get("episode")
                ep_nums.extend(e if isinstance(e, list) else [e] if e is not None else [])
            anime = dtype.startswith("anime") or _has_hint(first_path, root, anime_hints)
            common = dict(
                kind="tv", title=str(g.get("title") or Path(folder).name), year=g.get("year"),
                season=season if season is not None else (None if anime else 1),
                root=str(root), anime=anime,
                resolution=resolution_from_guess(g),
                release_type=release_type_from_guess(g, str(first_path)),
                group=g.get("release_group"),
            )
            # Only a folder holding exactly one show + season (and nothing else) is a clean pack;
            # uploading the folder would otherwise carry the other season / files along with it.
            clean_folder = groups_per_folder[folder] == 1
            if scan["tv_mode"] == "season" and len(eps) > 1 and clean_folder:
                tags = group_tags(Path(folder).name)    # a banned group on any episode bans the pack
                for p, eg in eps:
                    tags |= group_tags(p.name, eg.get("release_group"))
                pack = Item(path=folder, episodes=sorted(set(ep_nums)),
                            size_bytes=sum(p.stat().st_size for p, _ in eps),
                            file_count=len(eps), tags=sorted(tags), **common)
                extra_files = folder_videos.get(folder, 0) - len(eps)
                if extra_files > 0:
                    pack.verdict = SKIPPED
                    pack.reason = (f"folder has {extra_files} other video file(s) that would be uploaded with the "
                                   f"pack (new or unrecognised); tidy it or upload by hand")
                items.append(pack)
            else:
                for p, eg in eps:
                    e = eg.get("episode")
                    items.append(Item(path=str(p), episodes=e if isinstance(e, list) else [e] if e is not None else [],
                                      size_bytes=p.stat().st_size,
                                      tags=sorted(group_tags(p.name, eg.get("release_group"))), **common))

    if too_new:
        print(f"  skipped {too_new} file(s) changed in the last {scan['min_age_minutes']} min (still downloading?)")
    apply_banned(items, banned_list(cfg))
    if verbose:
        for it in items:
            print(f"  found {it.kind:5} {'anime ' if it.anime else ''}{it.label} [{it.resolution} {it.release_type}] -> {it.path}")
    print(f"Found {len(items)} items "
          f"({sum(i.kind == 'movie' for i in items)} movies, {sum(i.kind == 'tv' for i in items)} TV/anime)")
    return items


def _has_hint(path: Path, root: Path, hints: list[str]) -> bool:
    parts = [p.lower() for p in Path(path).relative_to(root).parts] + [root.name.lower()]
    return any(h in part for h in hints for part in parts)


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #

class Cache:
    """Tiny JSON cache so re-runs don't hammer TMDB or the tracker."""

    def __init__(self, path: Path, ttl_hours: float):
        self.path, self.ttl = path, ttl_hours * 3600
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key: str) -> Any:
        hit = self.data.get(key)
        if hit and (self.ttl <= 0 or time.time() - hit["t"] < self.ttl):
            return hit["v"]
        return None

    def set(self, key: str, value: Any) -> None:
        self.data[key] = {"t": time.time(), "v": value}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


class TMDB:
    def __init__(self, key: str, session: requests.Session, cache: Cache):
        self.key, self.s, self.cache = key, session, cache

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {}
        params = dict(params)
        if len(self.key) > 40:          # v4 read access token
            headers["Authorization"] = f"Bearer {self.key}"
        else:
            params["api_key"] = self.key
        r = self.s.get(f"{TMDB_API}{path}", params=params, headers=headers, timeout=20)
        if r.status_code == 401:
            raise RuntimeError("TMDB rejected the API key (HTTP 401)")
        r.raise_for_status()
        return r.json()

    def find(self, item: Item) -> Optional[dict[str, Any]]:
        kind = "movie" if item.kind == "movie" else "tv"
        ck = f"tmdb2:{kind}:{_norm(item.title)}:{item.year or ''}"
        cached = self.cache.get(ck)
        if cached is not None:
            return cached or None
        params: dict[str, Any] = {"query": item.title, "include_adult": "false"}
        if item.year:
            params["year" if kind == "movie" else "first_air_date_year"] = item.year
        results = self._get(f"/search/{kind}", params).get("results", [])
        if not results and item.year:      # year in filename is sometimes off by one
            params.pop("year", None)
            params.pop("first_air_date_year", None)
            results = self._get(f"/search/{kind}", params).get("results", [])
        best, best_score = None, 0.0
        for r in results[:10]:
            name = r.get("title") or r.get("name") or ""
            orig = r.get("original_title") or r.get("original_name") or ""
            title_score = max(difflib.SequenceMatcher(None, _norm(item.title), _norm(n)).ratio() for n in (name, orig))
            score = title_score
            date = r.get("release_date") or r.get("first_air_date") or ""
            ryear = int(date[:4]) if date[:4].isdigit() else None
            if item.year and ryear:
                score += 0.15 if abs(ryear - item.year) <= 1 else -0.2
            if score > best_score:
                best, best_score = {
                    "id": r["id"], "title": name, "year": ryear, "score": round(title_score, 3),
                    "anime": 16 in (r.get("genre_ids") or []) and r.get("original_language") == "ja",
                }, score
        found = best if best_score >= 0.8 else None
        self.cache.set(ck, found or {})
        return found

    def aired_episodes(self, tmdb_id: int, season: int) -> Optional[tuple[int, int]]:
        """(episodes aired so far, episodes listed) for a TV season, or None if TMDB doesn't know it."""
        ck = f"tmdbseason:{tmdb_id}:{season}"
        cached = self.cache.get(ck)
        if cached is None:
            try:
                eps = self._get(f"/tv/{tmdb_id}/season/{season}", {}).get("episodes", [])
            except requests.HTTPError:
                eps = None
            today = datetime.now().strftime("%Y-%m-%d")
            cached = [] if eps is None else [sum(1 for e in eps if (e.get("air_date") or "9999") <= today), len(eps)]
            self.cache.set(ck, cached)
        return tuple(cached) if cached else None


class Unit3D:
    def __init__(self, cfg: dict[str, Any], session: requests.Session, cache: Cache):
        t = cfg["tracker"]
        self.base, self.key, self.delay = t["url"], t["api_key"], float(t["request_delay"])
        self.cats = t["categories"]
        self.types, self.resolutions = t["types"], t["resolutions"]
        self.s, self.cache = session, cache
        self._last = 0.0

    def _get(self, params: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(5):
            self._last = time.time()
            # Token in a header, not the URL, so it never shows up in logs or error messages.
            r = self.s.get(f"{self.base}/api/torrents/filter", params=params, timeout=30,
                           headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"})
            if r.status_code == 429:
                try:
                    backoff = int(r.headers.get("Retry-After", 0) or 0)
                except ValueError:
                    backoff = 0
                backoff = backoff or 15 * (attempt + 1)
                print(f"  rate limited, waiting {backoff}s")
                time.sleep(backoff)
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"{self.base} rejected the API key (HTTP {r.status_code})")
            if r.status_code >= 400:
                raise RuntimeError(f"{self.base} returned HTTP {r.status_code}")
            return r.json().get("data", [])
        raise RuntimeError("still rate limited after 5 tries")

    def banned_groups(self) -> Optional[dict[str, str]]:
        """The site's banned-group list, if it publishes one through the API (newer UNIT3D does,
        at /api/blacklists/releasegroups). None if it doesn't or the call fails."""
        cached = self.cache.get("znth:banned")
        if cached is not None:
            return cached or None
        out: dict[str, str] = {}
        params: dict[str, Any] = {"per_page": 100}
        try:
            for _ in range(20):
                r = self.s.get(f"{self.base}/api/blacklists/releasegroups", params=params, timeout=30,
                               headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"})
                if r.status_code != 200:
                    break
                body = r.json()
                rows = body if isinstance(body, list) else body.get("data", [])
                for row in rows:
                    attrs = row.get("attributes", row) if isinstance(row, dict) else {}
                    name = str(attrs.get("name") or "").strip()
                    if name:
                        reason = str(attrs.get("reason") or "").strip()
                        out[name.lower()] = f"{name} ({reason})" if reason else name
                nxt = (body.get("meta") or {}).get("next_cursor") if isinstance(body, dict) else None
                if not nxt:
                    break
                params = {"per_page": 100, "cursor": nxt}
        except (requests.RequestException, ValueError, AttributeError):
            pass
        self.cache.set("znth:banned", out)
        return out or None

    def categories_for(self, item: Item) -> list[int]:
        if item.anime:
            ids = self.cats["anime_movie" if item.kind == "movie" else "anime_tv"]
            if ids:
                # anime may be filed under the normal category too, search both
                return list(dict.fromkeys(list(ids) + list(self.cats[item.kind])))
        return list(self.cats[item.kind])

    def search(self, item: Item, fresh: bool = False) -> list[dict[str, Any]]:
        params: list[tuple[str, Any]] = [("perPage", 100)]
        params += [("categories[]", c) for c in self.categories_for(item)]
        if item.tmdb_id:
            params.append(("tmdbId", item.tmdb_id))
        else:
            params.append(("name", item.title))
        if item.kind == "tv" and item.season is not None:
            params.append(("seasonNumber", item.season))
        ck = "znth2:" + "&".join(f"{k}={v}" for k, v in params)
        cached = None if fresh else self.cache.get(ck)
        if cached is not None:
            return cached
        rows: list[dict[str, Any]] = []
        for page in range(1, 11):     # a busy show can have well over 100 torrents in one season
            batch = self._get(params + [("page", page)])
            rows += batch
            if len(batch) < 100:
                break
        data = [{"name": d.get("attributes", {}).get("name", ""), **d.get("attributes", {})} for d in rows]
        slim = [{k: d.get(k) for k in ("name", "resolution", "type", "category", "category_id",
                                       "type_id", "resolution_id",
                                       "season_number", "episode_number", "tmdb_id")} for d in data]
        self.cache.set(ck, slim)
        return slim


# --------------------------------------------------------------------------- #
# Deciding
# --------------------------------------------------------------------------- #

def _type_key(t: Optional[str]) -> str:
    return re.sub(r"[^A-Z]", "", str(t or "").upper())


def _is_pack(result: dict[str, Any]) -> bool:
    ep = result.get("episode_number")
    if ep is not None:
        return int(ep or 0) == 0
    return not re.search(r"S\d{1,3}E\d{1,4}", result.get("name") or "", re.I)


def _same_season(result: dict[str, Any], season: int) -> bool:
    sn = result.get("season_number")
    if sn is not None:
        return int(sn) == season
    m = re.search(r"S(\d{1,3})", result.get("name") or "", re.I)
    return not m or int(m.group(1)) == season


def unsupported(cfg: dict[str, Any], item: Item) -> Optional[str]:
    """Why the site can't take this release, or None if it can."""
    types, resolutions = cfg["tracker"]["types"], cfg["tracker"]["resolutions"]
    if types and item.release_type not in (None, "OTHER") and item.release_type not in types:
        return f"site has no {item.release_type} type"
    if resolutions and item.resolution and item.resolution.lower() not in resolutions:
        return f"site has no {item.resolution} resolution"
    return None


def _same(result: dict[str, Any], field: str, local: Optional[str], ids: dict[str, int]) -> bool:
    """Compare a site result to the local value, by id when both sides have one, else by name."""
    rid = result.get(f"{field}_id")
    key = (local or "").upper() if field == "type" else (local or "").lower()
    if rid is not None and key in ids:
        return int(rid) == ids[key]
    if field == "type":
        return _type_key(result.get("type")) == _type_key(local)
    return str(result.get("resolution") or "").lower() == key


def decide(item: Item, results: list[dict[str, Any]],
           types: Optional[dict[str, int]] = None, resolutions: Optional[dict[str, int]] = None) -> None:
    types, resolutions = types or {}, resolutions or {}
    if item.kind == "tv" and item.season is not None:
        results = [r for r in results if _same_season(r, item.season)]
        if len(item.episodes) == 1 and item.file_count == 1:
            want = item.episodes[0]
            results = [r for r in results if _is_pack(r) or
                       int(r.get("episode_number") or -1) == want or
                       re.search(rf"E0*{want}\b", r.get("name") or "", re.I)]
        else:
            # A season folder is only a dupe of another full pack.
            packs = [r for r in results if _is_pack(r)]
            if not packs and results:
                item.verdict, item.reason = MISSING, f"only single episodes on site ({len(results)}), no season pack"
                item.site_matches = [r["name"] for r in results[:5]]
                return
            results = packs

    item.site_matches = [r["name"] for r in results[:10]]
    if not results:
        item.verdict, item.reason = MISSING, "not on site"
        return

    same_res = [r for r in results if not item.resolution or
                _same(r, "resolution", item.resolution, resolutions)]
    # An unknown local type (no source in the file name) can't prove it's different, so treat it as a match.
    same_both = [r for r in same_res if item.release_type in (None, "OTHER") or
                 _same(r, "type", item.release_type, types)]
    if same_both:
        item.verdict, item.reason = DUPE, f"{len(same_both)} on site already at {item.resolution} {item.release_type}"
    elif same_res:
        have = sorted({str(r.get("type")) for r in same_res})
        item.verdict, item.reason = NEW_QUALITY, f"{item.resolution} exists as {', '.join(have)} but not {item.release_type}"
    else:
        have = sorted({str(r.get("resolution")) for r in results})
        item.verdict, item.reason = NEW_QUALITY, f"on site at {', '.join(have)}, not {item.resolution}"


def check_items(cfg: dict[str, Any], items: list[Item], verbose: bool = False) -> None:
    if not cfg["tracker"]["api_key"]:
        sys.exit("No znth.cx API key. Set ZNTH_API_KEY or tracker.api_key in config.toml.")
    session = requests.Session()
    session.headers["User-Agent"] = "zenith-exists-checker/1.0"
    cache = Cache(STATE_DIR / "cache.json", float(cfg.get("cache_hours", 24)))
    tmdb = TMDB(cfg["tmdb"]["api_key"], session, cache) if cfg["tmdb"]["api_key"] else None
    if not tmdb:
        print("! No TMDB key: falling back to name search on the site, expect more UNMATCHED / false MISSING.")
    site = Unit3D(cfg, session, cache)

    banned = banned_list(cfg)
    if cfg["tracker"]["fetch_banned_groups"]:
        live = site.banned_groups()
        if live:
            new = len(set(live) - set(banned))
            banned = banned_list(cfg, live)
            print(f"Banned groups: {len(banned)} ({len(live)} from the site's live list, {new} not in the built-in list)")
        else:
            print(f"Banned groups: {len(banned)} built-in (the site doesn't publish its list through the API)")
    apply_banned(items, banned)
    hits = sum(i.reason.startswith("banned group") for i in items)
    if hits:
        print(f"Skipping {hits} item(s) from banned groups")

    todo = [i for i in items if not i.verdict]
    for n, item in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {item.label} ... ", end="", flush=True)
        try:
            if tmdb:
                hit = tmdb.find(item)
                if not hit:
                    item.verdict, item.reason = UNMATCHED, "no confident TMDB match"
                    print(item.verdict)
                    continue
                item.tmdb_id, item.tmdb_title, item.tmdb_year = hit["id"], hit["title"], hit["year"]
                item.tmdb_score = float(hit.get("score", 0))
                item.anime = item.anime or hit["anime"]
            decide(item, site.search(item), site.types, site.resolutions)
            if tmdb and item.kind == "tv" and item.file_count > 1 and item.season is not None \
                    and item.verdict in (MISSING, NEW_QUALITY):
                counts = tmdb.aired_episodes(item.tmdb_id, item.season)
                have = len(set(item.episodes))
                if counts:
                    aired, listed = counts
                    if have < aired:
                        item.verdict, item.reason = SKIPPED, f"incomplete season pack: {have} of {aired} aired episodes (would be {item.verdict})"
                    elif aired < listed:
                        item.verdict, item.reason = SKIPPED, f"season still airing ({aired} of {listed} out) (would be {item.verdict})"
            why = unsupported(cfg, item)
            if why and item.verdict in (MISSING, NEW_QUALITY):
                item.verdict, item.reason = SKIPPED, f"{why} (would be {item.verdict})"
            if item.tmdb_year and item.year and abs(item.tmdb_year - item.year) > 1:
                item.reason += f" (warning: year {item.year} vs TMDB {item.tmdb_year})"
        except (requests.RequestException, RuntimeError, ValueError) as e:
            item.verdict, item.reason = ERROR, _redact(str(e), cfg)
            if "rejected the API key" in str(e):
                print(item.verdict)
                cache.save()
                sys.exit(item.reason)
        print(f"{item.verdict}  {item.reason if verbose or item.verdict != DUPE else ''}")
        if n % 25 == 0:
            cache.save()
    cache.save()


def _redact(msg: str, cfg: dict[str, Any]) -> str:
    for k in (cfg["tracker"]["api_key"], cfg["tmdb"]["api_key"]):
        if k:
            msg = msg.replace(k, "***")
    return re.sub(r"(api_token|api_key)=[^&\s]+", r"\1=***", msg)


# --------------------------------------------------------------------------- #
# Reports and uploading
# --------------------------------------------------------------------------- #

def ua_command(cfg: dict[str, Any], item: Item) -> list[str]:
    ua = cfg["upload_assistant"]
    python = ua["python"]
    venv_py = ua_python(Path(ua["path"]).expanduser())
    if python in ("python3", "python", sys.executable) and venv_py.exists():
        python = str(venv_py)
    cmd = [python, str(Path(ua["path"]).expanduser() / "upload.py"), item.path,
           "--trackers", ua["tracker"]]
    if item.tmdb_id:
        cmd += ["--tmdb", f"{'movie' if item.kind == 'movie' else 'tv'}/{item.tmdb_id}"]
    return cmd + list(ua["extra_args"])


def write_report(cfg: dict[str, Any], items: list[Item]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    items.sort(key=lambda i: (VERDICT_ORDER.index(i.verdict) if i.verdict in VERDICT_ORDER else 99, i.kind, i.label.lower()))
    jpath = REPORT_DIR / f"report-{stamp}.json"
    jpath.write_text(json.dumps([asdict(i) for i in items], indent=2))
    (REPORT_DIR / "latest.json").write_text(jpath.read_text())

    cpath = REPORT_DIR / f"report-{stamp}.csv"
    with open(cpath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["verdict", "kind", "anime", "title", "year", "season", "resolution", "type",
                    "group", "size_gb", "tmdb_id", "tmdb_title", "reason", "path", "on_site"])
        for i in items:
            w.writerow([i.verdict, i.kind, i.anime, i.title, i.year or "", "" if i.season is None else i.season,
                        i.resolution or "", i.release_type or "", i.group or "", f"{i.size_bytes / 1e9:.2f}",
                        i.tmdb_id or "", i.tmdb_title or "", i.reason, i.path, " | ".join(i.site_matches[:3])])

    spath = REPORT_DIR / f"upload-commands-{stamp}.sh"
    with open(spath, "w") as f:
        f.write("#!/usr/bin/env bash\n# Upload-Assistant commands for MISSING items. Review before running.\n\n")
        for i in items:
            if i.verdict == MISSING:
                f.write(f"# {i.label} [{i.resolution} {i.release_type}]\n{shlex.join(ua_command(cfg, i))}\n\n")
        f.write("# --- NEW_QUALITY (title exists, different resolution/type) ---\n")
        for i in items:
            if i.verdict == NEW_QUALITY:
                f.write(f"# {i.label}: {i.reason}\n# {shlex.join(ua_command(cfg, i))}\n\n")

    counts = {v: sum(i.verdict == v for i in items) for v in VERDICT_ORDER}
    print("\nSummary: " + ", ".join(f"{v} {c}" for v, c in counts.items() if c))
    print(f"Report:   {cpath}\nJSON:     {jpath}\nCommands: {spath}")
    return jpath


VERDICT_CHOICES = {"missing": MISSING, "new_quality": NEW_QUALITY}

# What --unattended adds to every Upload-Assistant call:
#   --unattended         accept the IDs it finds through tracker APIs / TMDB without asking
#   --skip-dupe-asking   if it finds a dupe on znth.cx, treat it as a dupe and skip, never ask
UNATTENDED_ARGS = ["--unattended", "--skip-dupe-asking"]
# --onlyID: take only the ids from other trackers, not their descriptions or screenshots,
# so Upload-Assistant makes its own screenshots from your file.
OWN_SCREENS_ARGS = ["--onlyID"]


def parse_verdicts(value: Optional[str], cfg: dict[str, Any]) -> set[str]:
    raw = value.split(",") if value else cfg["upload_assistant"]["verdicts"]
    out = set()
    for v in raw:
        key = v.strip().lower().replace("-", "_")
        if key not in VERDICT_CHOICES:
            sys.exit(f"Unknown verdict {v!r}. Use missing, new_quality, or missing,new_quality.")
        out.add(VERDICT_CHOICES[key])
    return out


MAX_REPORT_AGE_HOURS = 24
DEBUG_FLAGS = {"--debug", "-debug"}


def load_report(report: Path) -> list[Item]:
    known = set(Item.__dataclass_fields__)
    return [Item(**{k: v for k, v in d.items() if k in known}) for d in json.loads(report.read_text())]


def _batch_key(item: Item) -> str:
    """Two items with this same key are the same upload as far as the site is concerned."""
    eps = ",".join(map(str, item.episodes)) if item.kind == "tv" and item.file_count == 1 else "pack"
    return "|".join(map(str, (item.kind, item.tmdb_id or item.title.lower(), item.season, eps,
                              item.resolution, item.release_type)))


def preflight_ua(ua_dir: Path, tracker: str, unattended: bool, own_screenshots: bool) -> list[str]:
    """Problems that would make Upload-Assistant skip or misbehave without saying so loudly."""
    problems = []
    setup = ua_dir / "src" / "trackersetup.py"
    try:
        if f"'{tracker}': {tracker}" not in setup.read_text() and f'"{tracker}": {tracker}' not in setup.read_text():
            problems.append(f"{tracker} isn't registered in Upload-Assistant. Run `setup` once.")
    except OSError:
        problems.append(f"Can't read {setup}.")
    cfg_py = ua_dir / "data" / "config.py"
    try:
        text = cfg_py.read_text()
    except OSError:
        return problems + [f"No Upload-Assistant config at {cfg_py}. Run `setup` once."]
    block = re.search(rf'"{tracker}"\s*:\s*\{{(.*?)\n\s*\}}', text, re.S)
    if not block:
        problems.append(f"No {tracker} section in {cfg_py}. Run `setup` once.")
    elif re.search(r'"api_key"\s*:\s*(""|\'\')', block.group(1)):
        problems.append(f"The {tracker} api_key in {cfg_py} is empty, so Upload-Assistant would skip the site.")
    if unattended and own_screenshots and keep_images_on(text):
        problems.append(f'{cfg_py} has "keep_images" on, so unattended uploads would reuse other trackers\' '
                        f"screenshots instead of making their own. Run `setup` once to switch it off.")
    return problems


def _ask(prompt: str, on_eof: str = "") -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:      # no terminal (cron, pipe)
        print()
        return on_eof


class UploadLock:
    """Stops two runs (say, an overlapping cron job) from uploading the same things at once."""

    def __init__(self, path: Path):
        self.path, self.fh = path, None

    def __enter__(self) -> "UploadLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            pass
        except OSError:
            sys.exit("Another upload run is already going (data/upload.lock). Wait for it to finish.")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.fh:
            self.fh.close()


def upload(cfg: dict[str, Any], report: Path, verdicts: set[str], yes: bool,
           limit: Optional[int], dry_run: bool, unattended: bool = False) -> None:
    with UploadLock(STATE_DIR / "upload.lock"):
        _upload(cfg, report, verdicts, yes, limit, dry_run, unattended)


def _upload(cfg: dict[str, Any], report: Path, verdicts: set[str], yes: bool,
            limit: Optional[int], dry_run: bool, unattended: bool) -> None:
    ua = cfg["upload_assistant"]
    ua_dir = Path(ua["path"]).expanduser()
    script = ua_dir / "upload.py"
    if not dry_run and not script.exists():
        sys.exit(f"Upload-Assistant not found at {script}. Set upload_assistant.path in config.toml.")

    age_h = (time.time() - report.stat().st_mtime) / 3600
    if age_h > MAX_REPORT_AGE_HOURS:
        msg = f"{report.name} is {age_h:.0f} hours old; the site may have changed since. Run `check` again."
        if unattended and not dry_run:
            sys.exit(msg)
        print("! " + msg)

    items = load_report(report)
    done_path = STATE_DIR / "uploaded.json"           # paths already sent
    keys_path = STATE_DIR / "uploaded_keys.json"     # what they were (title + quality), to catch copies
    done, done_keys = _load_set(done_path), _load_set(keys_path)

    queue, held = [], []
    seen: set[str] = set(done_keys) | {_batch_key(i) for i in items if i.path in done}
    for i in items:
        if i.verdict not in verdicts or i.path in done:
            continue
        if unattended and not i.confident:
            held.append((i, "TMDB match not certain enough to upload unattended"
                            + (f" (matched {i.tmdb_title!r} {i.tmdb_year or ''})" if i.tmdb_id else "")))
            continue
        key = _batch_key(i)
        if key in seen:
            held.append((i, "same title + quality as another item already uploaded or in this batch"))
            continue
        seen.add(key)
        queue.append(i)
    limit = limit if limit is not None else (int(ua["unattended_limit"]) if unattended else 0)
    if limit:
        queue = queue[:limit]

    if held:
        print(f"Held back {len(held)} item(s), upload these by hand if they're right:")
        for i, why in held:
            print(f"   - {i.label} [{i.resolution} {i.release_type}]: {why}")
        print()
    if not queue:
        print(f"Nothing to upload ({' + '.join(sorted(verdicts))}).")
        return

    if not dry_run:
        problems = preflight_ua(ua_dir, ua["tracker"], unattended, bool(ua["own_screenshots"]))
        if problems:
            sys.exit("Not uploading:\n  - " + "\n  - ".join(problems))
    elif unattended and ua["own_screenshots"] and ua_keeps_foreign_images(ua_dir):
        print('! Upload-Assistant has "keep_images" on; a real unattended run would refuse to start. '
              "Run `setup` once.\n")

    extra: list[str] = []
    if unattended:
        extra += UNATTENDED_ARGS
        if ua["own_screenshots"]:
            extra += OWN_SCREENS_ARGS
    rehearsal = bool(DEBUG_FLAGS & set(ua["extra_args"]))
    mode = "UNATTENDED" if unattended else "interactive"
    print(f"{len(queue)} item(s) to upload to {ua['tracker']} ({mode}, {' + '.join(sorted(verdicts))}) "
          f"from {report.name}{' (dry run, nothing will be uploaded)' if dry_run else ''}"
          f"{' (--debug rehearsal: Upload-Assistant will not post)' if rehearsal else ''}\n")
    for n, item in enumerate(queue, 1):
        print(f"  {n:>3}. {item.verdict:<11} {item.label} [{item.resolution} {item.release_type}]")
    print()

    if unattended and not dry_run and not yes:
        # One deliberate confirmation for the whole batch; --yes skips it (for cron).
        ans = _ask(f"Upload all {len(queue)} without asking again? Type 'upload' to start: ")
        if ans != "upload":
            print("Cancelled, nothing uploaded." + ("" if sys.stdin.isatty() else " (No terminal: use --yes for cron.)"))
            return

    session = requests.Session()
    site = Unit3D(cfg, session, Cache(STATE_DIR / "cache.json", 0)) if cfg["tracker"]["api_key"] else None
    log_path = REPORT_DIR / f"upload-log-{datetime.now():%Y%m%d-%H%M%S}.csv"
    failures_in_a_row = 0
    try:
        for n, item in enumerate(queue, 1):
            cmd = ua_command(cfg, item) + [a for a in extra if a not in ua["extra_args"]]
            print(f"[{n}/{len(queue)}] {item.label} [{item.resolution} {item.release_type}] {item.verdict}")
            if item.tmdb_id:
                print(f"   TMDB: {item.tmdb_title} ({item.tmdb_year or '?'}) id {item.tmdb_id}")
            print("   " + shlex.join(cmd))
            if dry_run:
                continue
            if not unattended and not yes:
                ans = _ask("   Upload? [y]es / [n]o / [q]uit: ", on_eof="q")
                if ans.startswith("q"):
                    break
                if not ans.startswith("y"):
                    continue
            # Unattended: no stdin, so Upload-Assistant can never sit waiting on a question.
            rc = subprocess.call(cmd, cwd=str(ua_dir), stdin=subprocess.DEVNULL if unattended else None)
            seen_on_site = None
            if rc == 0 and not rehearsal and site:
                time.sleep(5)
                seen_on_site = _on_site_now(site, item)
            _log_upload(log_path, item, rc, rehearsal, seen_on_site)
            if rc == 0:
                failures_in_a_row = 0
                if rehearsal:
                    continue     # nothing was posted, so don't mark it done
                # Recorded even if not visible yet (mod queue, or skipped as a dupe):
                # better to never upload twice than to retry something that's pending.
                done.add(item.path)
                done_keys.add(_batch_key(item))
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                done_path.write_text(json.dumps(sorted(done), indent=1))
                keys_path.write_text(json.dumps(sorted(done_keys), indent=1))
                if seen_on_site is False:
                    print("   ! Upload-Assistant finished, but it isn't visible on the site yet "
                          "(moderation queue, or it was skipped as a dupe). Check by hand.")
            else:
                failures_in_a_row += 1
                print(f"   Upload-Assistant exited with {rc}")
                if unattended and failures_in_a_row >= 3:
                    print("Stopping: 3 failures in a row, something is probably wrong (config, site, network).")
                    break
    except KeyboardInterrupt:
        print("\nStopped by you. Items already uploaded are recorded.")
    if not dry_run and log_path.exists():
        print(f"\nLog: {log_path}")


def _load_set(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text()))
    except (OSError, ValueError):
        return set()


def _on_site_now(site: "Unit3D", item: Item) -> Optional[bool]:
    probe_item = Item(**{k: getattr(item, k) for k in Item.__dataclass_fields__})
    try:
        decide(probe_item, site.search(probe_item, fresh=True), site.types, site.resolutions)
    except (requests.RequestException, RuntimeError, ValueError):
        return None
    return probe_item.verdict == DUPE


def _log_upload(path: Path, item: Item, rc: int, rehearsal: bool = False,
                seen_on_site: Optional[bool] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    if rc != 0:
        result = f"failed (exit {rc})"
    elif rehearsal:
        result = "debug rehearsal, not posted"
    elif seen_on_site:
        result = "uploaded, confirmed on site"
    elif seen_on_site is False:
        result = "finished, not visible on site yet"
    else:
        result = "finished"
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "result", "verdict", "title", "resolution", "type", "tmdb_id", "path"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), result, item.verdict, item.label,
                    item.resolution or "", item.release_type or "", item.tmdb_id or "", item.path])


def probe(cfg: dict[str, Any]) -> None:
    """List category / type / resolution names seen on the site, to fill in config."""
    if not cfg["tracker"]["api_key"]:
        sys.exit("No znth.cx API key. Set ZNTH_API_KEY or tracker.api_key in config.toml.")
    s = requests.Session()
    site = Unit3D(cfg, s, Cache(STATE_DIR / "probe.json", 0.0001))
    seen: dict[str, set] = {"category": set(), "type": set(), "resolution": set()}
    for page in (1, 2, 3):
        rows = site._get([("perPage", 100), ("page", page)])
        for d in rows:
            a = d.get("attributes", {})
            seen["category"].add(f"{a.get('category_id', '?')} = {a.get('category')}")
            seen["type"].add(f"{a.get('type_id', '?')} = {a.get('type')}")
            seen["resolution"].add(f"{a.get('resolution_id', '?')} = {a.get('resolution')}")
        if len(rows) < 100:
            break
    for k, vals in seen.items():
        print(f"{k}:")
        for v in sorted(vals):
            print(f"   {v}")
    print("\nPut these ids into [tracker.categories], [tracker.types] and [tracker.resolutions] in config.toml.")



# --------------------------------------------------------------------------- #
# Installing Upload-Assistant
# --------------------------------------------------------------------------- #

UA_REPO = "https://github.com/Audionut/Upload-Assistant.git"
ZNTH_MODULE = HERE / "ua_tracker" / "ZNTH.py"
ZNTH_UA_CONFIG = """        "ZNTH": {
            "link_dir_name": "",
            "useAPI": False,
            "api_key": %s,
            "anon": False,
            "modq": False,
        },
"""


def ua_python(ua_dir: Path) -> Path:
    return ua_dir / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")


def _run(cmd: list[str], **kw: Any) -> None:
    print("   $ " + shlex.join(cmd))
    try:
        subprocess.run(cmd, check=True, **kw)
    except subprocess.CalledProcessError as e:
        shown = [c for c in cmd if c != "-C" and not c.startswith("/")][:3]
        sys.exit(f"\nStopped: `{' '.join(shown)}` failed (exit {e.returncode}). See the message above.")
    except OSError as e:
        sys.exit(f"\nStopped: couldn't run {cmd[0]}: {e}")


def _check_writable(target: Path) -> None:
    """Fail early, in plain words, if we can't create or write the target folder."""
    probe_dir = target
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    if not os.access(probe_dir, os.W_OK):
        user = os.environ.get("USER") or "$USER"
        sys.exit(
            f"Can't write to {probe_dir} as {user}, so Upload-Assistant can't go in {target}.\n"
            f"Either install it somewhere you own:\n"
            f"    python3 {Path(sys.argv[0]).name} setup --path ~/Upload-Assistant\n"
            f"or create the folder once with sudo and hand it to yourself:\n"
            f"    sudo mkdir -p {shlex.quote(str(target))} && sudo chown {user}: {shlex.quote(str(target))}\n"
            f"Then set upload_assistant.path in config.toml to match.")


ZNTH_IMPORT = "from src.trackers.ZNTH import ZNTH\n"
ZNTH_CLASS_ENTRY = "\n    'ZNTH': ZNTH,"
ZNTH_API_ENTRY = "\n    'ZNTH',"


def unregister_znth(text: str) -> str:
    """Remove exactly the lines register_znth adds, leaving any other local edits alone."""
    return text.replace(ZNTH_IMPORT, "").replace(ZNTH_CLASS_ENTRY, "").replace(ZNTH_API_ENTRY, "")


def register_znth(ua_dir: Path) -> None:
    """Copy ZNTH.py into Upload-Assistant and register it. Safe to run again."""
    trackers = ua_dir / "src" / "trackers"
    if (ua_dir / ".git").exists() and ua_ships_znth(ua_dir):
        print("   Upload-Assistant ships its own ZNTH module now, using that one")
        return
    if not (trackers / "UNIT3D.py").exists():
        sys.exit("This Upload-Assistant is too old for the ZNTH module (no src/trackers/UNIT3D.py).\n"
                 "Run setup --update to bring it up to date first.")
    shutil.copy2(ZNTH_MODULE, trackers / "ZNTH.py")
    setup = ua_dir / "src" / "trackersetup.py"
    text = setup.read_text()
    if "'ZNTH': ZNTH" not in text:
        imports = list(re.finditer(r"^from src\.trackers\.\w+ import \w+\n", text, re.M))
        class_map = re.search(r"^tracker_class_map[^=\n]*=\s*\{", text, re.M)
        api = re.search(r"^api_trackers[^=\n]*=\s*[\[{]", text, re.M)
        if not (imports and class_map and api):
            sys.exit(f"Couldn't find where to register ZNTH in {setup} (Upload-Assistant layout changed?).\n"
                     f"ZNTH.py was copied; add ZNTH to tracker_class_map and api_trackers by hand.")
        # Insert from the end backwards so earlier offsets stay valid.
        for pos, add in sorted([(imports[-1].end(), ZNTH_IMPORT), (class_map.end(), ZNTH_CLASS_ENTRY),
                                (api.end(), ZNTH_API_ENTRY)], reverse=True):
            text = text[:pos] + add + text[pos:]
        setup.write_text(text)
    print(f"   ZNTH tracker module installed and registered in {setup.name}")


KEEP_IMAGES_RE = re.compile(r'^(\s*)"keep_images"\s*:\s*(True|False)\b', re.M)


def keep_images_on(config_text: str) -> bool:
    """Upload-Assistant's keep_images, as it would read it. A missing key means True (its default)."""
    live = [m for m in KEEP_IMAGES_RE.finditer(config_text)]
    return not live or live[-1].group(2) == "True"


def ua_keeps_foreign_images(ua_dir: Path) -> bool:
    """True if Upload-Assistant is set to reuse screenshots found on other trackers.
    In unattended mode it does that without asking, even with --onlyID, unless keep_images is False."""
    try:
        return keep_images_on((ua_dir / "data" / "config.py").read_text())
    except OSError:
        return False


def ua_ships_znth(ua_dir: Path) -> bool:
    """Upload-Assistant's own repo has a ZNTH module now; ours should step aside."""
    return _git(ua_dir, "ls-files", "--error-unmatch", "src/trackers/ZNTH.py").returncode == 0


def write_ua_config(ua_dir: Path, own_screenshots: bool = True) -> None:
    """Create data/config.py from the example (if needed) and add a ZNTH block."""
    cfg_py = ua_dir / "data" / "config.py"
    existed = cfg_py.exists()
    original = cfg_py.read_text() if existed else (ua_dir / "data" / "example-config.py").read_text()
    if not existed:
        print(f"   creating {cfg_py} from example-config.py")
    text = original
    tmdb = os.environ.get("TMDB_API_KEY")
    if tmdb and '"tmdb_api": ""' in text:
        text = text.replace('"tmdb_api": ""', f'"tmdb_api": {json.dumps(tmdb)}', 1)
        print("   filled tmdb_api from TMDB_API_KEY")
    if own_screenshots and keep_images_on(text):
        if KEEP_IMAGES_RE.search(text):
            text = KEEP_IMAGES_RE.sub(r'\1"keep_images": False', text)
        else:
            default = re.search(r'\n(\s*)"DEFAULT"\s*:\s*\{[^\n]*\n', text)
            if default:
                indent = default.group(1) + "    "
                text = text[:default.end()] + f'{indent}"keep_images": False,\n' + text[default.end():]
        if keep_images_on(text):
            print(f'   ! couldn\'t switch off keep_images in {cfg_py}; set "keep_images": False by hand')
        else:
            print("   set keep_images to False, so Upload-Assistant makes its own screenshots")
    if '"ZNTH"' not in text:
        anchor = re.search(r'\n(\s*)"default_trackers":[^\n]*\n', text)
        if not anchor:
            print(f"   ! couldn't find the TRACKERS section in {cfg_py}; add a ZNTH block by hand (see README)")
        else:
            block = ZNTH_UA_CONFIG % json.dumps(os.environ.get("ZNTH_API_KEY", ""))
            text = text[:anchor.end()] + "\n" + block + text[anchor.end():]
            print("   added a ZNTH block" + (" with ZNTH_API_KEY" if os.environ.get("ZNTH_API_KEY") else
                                              " (api_key empty, fill it in)"))
    try:
        compile(text, str(cfg_py), "exec")
    except SyntaxError as e:
        sys.exit(f"   ! my edits would break {cfg_py} (line {e.lineno}), so I left it alone.\n"
                 f"   Add the ZNTH block and \"keep_images\": False by hand (see README).")
    if not existed:
        cfg_py.write_text(text)
    elif text != original:
        backup = cfg_py.with_name(f"config.py.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(cfg_py, backup)
        print(f"   backed up your old config to {backup.name}")
        cfg_py.write_text(text)
    else:
        print(f"   {cfg_py.name} already set up, left as is")
    try:
        cfg_py.chmod(0o600)
    except OSError:
        pass


def _check_owned(ua_dir: Path) -> None:
    """git pull needs to write all over the checkout; files left behind by an earlier sudo break it."""
    if os.name == "nt" or not hasattr(os, "geteuid"):
        return
    me = os.geteuid()
    for p in [ua_dir, ua_dir / ".git", *(ua_dir / ".git").glob("*"), *ua_dir.glob("src/**/*")]:
        try:
            if p.stat().st_uid != me and not os.access(p, os.W_OK):
                user = os.environ.get("USER") or "$USER"
                q = shlex.quote(str(ua_dir))
                sys.exit(f"Some files in {ua_dir} belong to another user (e.g. {p}), probably from an earlier\n"
                         f"install or update run with sudo, so git can't update it. Hand them back to yourself:\n"
                         f"    sudo chown -R {user}: {q}\n"
                         f"then run setup --update again (without sudo).")
        except OSError:
            continue


def _git(ua_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ua_dir), *args], capture_output=True, text=True)


def _ensure_on_branch(ua_dir: Path) -> None:
    """A copy checked out at a release tag isn't on a branch, so `git pull` has nothing to follow.
    Move it onto the default branch, but only if that loses nothing (HEAD is already in its history)."""
    if _git(ua_dir, "symbolic-ref", "-q", "HEAD").returncode == 0:
        return
    print("   this copy is pinned to a release instead of following a branch")
    _run(["git", "-C", str(ua_dir), "fetch", "--quiet", "origin"])
    ref = _git(ua_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if not ref:
        _git(ua_dir, "remote", "set-head", "origin", "--auto")
        ref = _git(ua_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() or "origin/master"
    branch = ref.split("/", 1)[1]
    if _git(ua_dir, "merge-base", "--is-ancestor", "HEAD", ref).returncode != 0:
        sys.exit(f"{ua_dir} has commits that aren't on {ref}, so I won't move it.\n"
                 f"Save them on a branch first: `git -C {shlex.quote(str(ua_dir))} switch -c my-changes`.")
    local = f"refs/heads/{branch}"
    if _git(ua_dir, "rev-parse", "--verify", "-q", local).returncode == 0 and \
            _git(ua_dir, "merge-base", "--is-ancestor", local, ref).returncode != 0:
        sys.exit(f"Your local {branch} branch has commits that aren't on {ref}, so I won't reset it.\n"
                 f"Switch to it yourself (`git -C {shlex.quote(str(ua_dir))} switch {branch}`) and run --update again.")
    _run(["git", "-C", str(ua_dir), "checkout", "--quiet", "-B", branch, ref])
    _git(ua_dir, "branch", "--quiet", f"--set-upstream-to={ref}")
    print(f"   switched to the {branch} branch")


def update_ua(ua_dir: Path) -> None:
    """git pull an existing install without touching config or the user's own edits."""
    _check_owned(ua_dir)
    if not (ua_dir / ".git").exists():
        sys.exit(f"{ua_dir} wasn't installed with git, so it can't be updated in place.\n"
                 f"Move it aside (keep data/config.py) and run setup again for a fresh copy.")
    # Take out only our ZNTH lines so they don't count as a local change; re-added after the pull.
    setup = ua_dir / "src" / "trackersetup.py"
    setup.write_text(unregister_znth(setup.read_text()))
    changed = subprocess.run(["git", "-C", str(ua_dir), "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True).stdout.strip()
    if changed:
        # Leave ZNTH unregistered here, so a `git stash` now only picks up the user's own edits.
        q = shlex.quote(str(ua_dir))
        sys.exit(f"{ua_dir} has your own changes to these files, so I didn't update it:\n{changed}\n\n"
                 f"To keep them: `git -C {q} stash`, run setup --update again, then `git -C {q} stash pop`.\n"
                 f"To drop them: `git -C {q} checkout -- <file>`.\n"
                 f"(ZNTH is unregistered until setup runs again.)")
    before = subprocess.run(["git", "-C", str(ua_dir), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    _ensure_on_branch(ua_dir)
    ours = ua_dir / "src" / "trackers" / "ZNTH.py"
    if ours.exists() and not ua_ships_znth(ua_dir):
        ours.unlink()        # re-copied after the pull; an untracked copy would block an upstream one
    try:
        _run(["git", "-C", str(ua_dir), "pull", "--ff-only"])
    except SystemExit:
        register_znth(ua_dir)    # leave it working as it was
        raise
    after = subprocess.run(["git", "-C", str(ua_dir), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    print(f"   updated {before} -> {after}" if before != after else "   already up to date")


def install_ua(cfg: dict[str, Any], target: Optional[Path], update: bool, skip_deps: bool) -> None:
    ua_dir = (target or Path(cfg["upload_assistant"]["path"] or "")).expanduser().resolve()
    if not str(ua_dir) or ua_dir == Path("/").resolve():
        sys.exit("Give a target folder with --path, or set upload_assistant.path in config.toml.")
    if not shutil.which("git"):
        sys.exit("git is not installed.")
    print(f"Installing Upload-Assistant into {ua_dir}")

    if (ua_dir / "upload.py").exists():
        _check_writable(ua_dir / "src" / "trackers")
        if update:
            update_ua(ua_dir)
        else:
            print("   already installed (use --update to pull the latest version)")
    elif ua_dir.exists() and any(ua_dir.iterdir()):
        sys.exit(f"{ua_dir} exists and isn't empty. Pick an empty or new folder.")
    else:
        _check_writable(ua_dir)
        ua_dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", UA_REPO, str(ua_dir)])

    py = ua_python(ua_dir)
    if not skip_deps:
        if not py.exists():
            _run([sys.executable, "-m", "venv", str(ua_dir / "venv")])
        _run([str(py), "-m", "pip", "install", "-q", "-U", "pip"])
        _run([str(py), "-m", "pip", "install", "-q", "-r", str(ua_dir / "requirements.txt")])

    register_znth(ua_dir)
    write_ua_config(ua_dir, bool(cfg["upload_assistant"]["own_screenshots"]))

    missing = [t for t in ("ffmpeg", "mediainfo") if not shutil.which(t)]
    print("\nDone.")
    if missing:
        print(f"! Upload-Assistant also needs {' and '.join(missing)} on your system, "
              f"e.g. `sudo apt install {' '.join(missing)}`.")
    print(f"Point config.toml at it:\n\n  [upload_assistant]\n  path = \"{ua_dir}\"\n  python = \"{py}\"\n")
    print(f"Then finish Upload-Assistant's own settings (torrent client, etc.) in {ua_dir / 'data' / 'config.py'}"
          f" or with `{py} {ua_dir / 'config-generator.py'}`.")

# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    head, rest = _split_config_arg(argv)
    if "-h" not in head and "--help" not in head and (not rest or rest[0] not in COMMANDS):
        cfg_path = DEFAULT_CONFIG
        if "-c" in head or "--config" in head:
            i = head.index("-c") if "-c" in head else head.index("--config")
            cfg_path = Path(head[i + 1])
        cfg = load_config(cfg_path)
        if not rest:
            if not sys.stdin.isatty():
                rest = ["check"]          # bare under cron etc.: the safe default
            else:
                chosen = menu(cfg)
                if chosen is None:
                    return
                rest = chosen
        elif rest[0] in cfg["presets"]:
            rest = preset_argv(cfg["presets"][rest[0]], rest[1:])
        else:
            sys.exit(f"Unknown command or preset {rest[0]!r}. Commands: {', '.join(sorted(COMMANDS - {'run', 'install-ua'}))}; "
                     f"presets: {', '.join(cfg['presets'])}")
        argv = head + rest

    ap = argparse.ArgumentParser(
        description="Check local media against znth.cx and upload what's missing.",
        epilog="Run with no arguments for a menu, or give a preset name from config.toml "
               "(e.g. `zenith_check.py nightly`). Commands: setup | check | upload | auto [--all] [-l N] [-n]")
    ap.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    sub = ap.add_subparsers(dest="cmd", metavar="command")
    hide = argparse.SUPPRESS

    def which(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group()
        g.add_argument("--missing", dest="pick", action="store_const", const="missing",
                       help="only titles the site doesn't have")
        g.add_argument("--new", dest="pick", action="store_const", const="new_quality",
                       help="only new resolutions/types of titles it has")
        g.add_argument("--all", dest="pick", action="store_const", const="missing,new_quality",
                       help="both")
        g.add_argument("--only", dest="pick", help=hide)                       # older spelling
        g.add_argument("--include-new-quality", dest="pick", action="store_const",
                       const="missing,new_quality", help=hide)                 # older spelling
        p.add_argument("-l", "--limit", type=int, help="upload at most N this run")
        p.add_argument("-n", "--dry-run", action="store_true", help="show what would be uploaded, upload nothing")

    s_ = sub.add_parser("setup", aliases=["install-ua"], help="install or update Upload-Assistant with the ZNTH module")
    s_.add_argument("--path", type=Path, help="folder (default: upload_assistant.path in config.toml)")
    s_.add_argument("--update", action="store_true", help="pull the latest Upload-Assistant first")
    s_.add_argument("--skip-deps", action="store_true", help=hide)

    c = sub.add_parser("check", help="scan your folders and check the site; writes a report, never uploads")
    c.add_argument("--local", "--scan-only", dest="local", action="store_true", help="only list what's found, no network")
    c.add_argument("-v", "--verbose", action="store_true")

    u = sub.add_parser("upload", help="upload from the last report, asking before each one")
    which(u)
    u.add_argument("--unattended", action="store_true", help="no per-item questions (see `auto`)")
    u.add_argument("--yes", action="store_true", help="with --unattended: skip the one confirmation")
    u.add_argument("--report", type=Path, default=REPORT_DIR / "latest.json", help=hide)

    a = sub.add_parser("auto", help="check, then upload unattended (asks once at a terminal; no questions under cron)")
    which(a)
    a.add_argument("-v", "--verbose", action="store_true")
    a.add_argument("--yes", action="store_true", help=hide)

    r = sub.add_parser("run")                   # older: check + upload in one go, kept working but unlisted
    sub._choices_actions = [x for x in sub._choices_actions if x.dest != "run"]
    which(r)
    r.add_argument("-v", "--verbose", action="store_true")
    r.add_argument("--unattended", action="store_true")
    r.add_argument("--yes", action="store_true")

    sub.add_parser("probe", help="show the site's category / type / resolution ids")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cmd = {"install-ua": "setup"}.get(args.cmd or "check", args.cmd or "check")
    if cmd == "probe":
        probe(cfg)
    elif cmd == "setup":
        install_ua(cfg, args.path, args.update, args.skip_deps)
    elif cmd == "upload":
        if not args.report.exists():
            sys.exit(f"No report at {args.report}. Run `check` first.")
        upload(cfg, args.report, parse_verdicts(args.pick, cfg), args.yes, args.limit, args.dry_run, args.unattended)
    else:
        if not cfg["directories"]:
            sys.exit("No [[directories]] in config.toml.")
        local = getattr(args, "local", False)
        verdicts = parse_verdicts(args.pick, cfg) if cmd in ("auto", "run") else set()
        items = scan_directories(cfg, verbose=getattr(args, "verbose", False) or local)
        if local:
            return
        check_items(cfg, items, verbose=getattr(args, "verbose", False))
        report = write_report(cfg, items)
        if cmd == "auto":
            # Typing `auto` is the opt-in. At a terminal it still lists the batch and asks once;
            # under cron (no terminal) it goes straight ahead.
            print()
            upload(cfg, report, verdicts, args.yes or not sys.stdin.isatty(), args.limit, args.dry_run, True)
        elif cmd == "run":
            print()
            upload(cfg, report, verdicts, args.yes, args.limit, args.dry_run, args.unattended)


if __name__ == "__main__":
    main()
