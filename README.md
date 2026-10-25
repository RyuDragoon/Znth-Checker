# Zenith exists checker

Scans your media folders for movies, TV and anime, asks **znth.cx** whether each one is
already on the site, and tells you what's missing. It can then hand the missing ones to
[Upload-Assistant](https://github.com/Audionut/Upload-Assistant). No Sonarr or Radarr needed.

Nothing is uploaded unless you ask for it. `upload` asks before each item. `auto` uploads a
whole batch unattended, but at a terminal it lists the batch and asks you once first.

## Quick start

```sh
pip install -r requirements.txt          # Python 3.9+
cp config.example.toml config.toml
chmod 600 config.toml
# edit config.toml: your folders and keys (see Setup below)
python3 zenith_check.py setup            # only needed for uploading: installs Upload-Assistant + ZNTH
python3 zenith_check.py                  # menu; pick 1 to see what's missing
```

## Setup

Edit `config.toml`:

1. Add your folders under `[[directories]]` (e.g. `/media/Storage9`). Use `type = "auto"`
   for mixed folders, or `movie` / `tv` / `anime` / `anime_movie` if a folder only holds one kind.
2. Put your keys in, or export them instead so they never touch disk:
   ```sh
   export ZNTH_API_KEY=...      # znth.cx: your profile, Settings, API key
   export TMDB_API_KEY=...      # free at themoviedb.org; the v3 key or v4 token both work
   ```
3. znth.cx's category, type and resolution ids are already filled in (`[tracker.categories]`,
   `[tracker.types]`, `[tracker.resolutions]`). If the site ever changes them, `python3 zenith_check.py probe`
   lists the current ones.
4. Optional: change the menu by editing the `[presets.*]` blocks at the bottom (see [Presets](#presets)).

An older `config.toml` works as it is. Anything it's missing, presets included, falls back to the defaults.

## Use

### The menu

Run it with no arguments and you get a numbered menu:

```
$ python3 zenith_check.py
Zenith checker. Pick one (edit the list in config.toml under [presets.*]):

   1. See what's missing on the site (no uploads)      report
   2. Upload what's missing, asking before each one    ask
   3. Dry run: what would an unattended upload send?   rehearsal
   4. Upload everything missing, unattended            missing
   5. Missing + new quality, unattended, at most 10    nightly
   6. Update Upload-Assistant and the ZNTH module      update
   q. Quit

Choice:
```

Type a number or a name. Enter or `q` quits. Before it runs, it prints the command your choice
stands for (e.g. `> zenith_check.py nightly   (auto --all --limit 10)`), so you can learn the
short form as you go. Run bare without a terminal (cron, a pipe), it never shows the menu and just
does a `check`, which never uploads.

### Presets

Each line of the menu is a preset from `config.toml`. You can also run any preset by name:

```sh
python3 zenith_check.py report
python3 zenith_check.py nightly
python3 zenith_check.py nightly -l 3     # flags after the name override the preset
python3 zenith_check.py nightly -n       # same, as a dry run
python3 zenith_check.py nightly --new    # same, but only NEW_QUALITY
```

The six that ship:

| Preset | Same as typing | What it does |
|---|---|---|
| `report` | `check` | scan, check the site, write a report. Never uploads |
| `ask` | `upload --missing` | upload the MISSING items from the last report, asking y/n/q before each |
| `rehearsal` | `auto --all -n` | fresh check, then show exactly what an unattended run would send. Uploads nothing |
| `missing` | `auto --missing` | fresh check, then upload every MISSING item unattended |
| `nightly` | `auto --all -l 10` | fresh check, then upload MISSING + NEW_QUALITY unattended, at most 10 |
| `update` | `setup --update` | update Upload-Assistant and re-apply the ZNTH module |

`ask` works from the last report, so run `report` first (it warns if the report is over a day old).

To change them, edit the `[presets.<name>]` blocks in `config.toml` (they're in `config.example.toml`
if your config predates them). The menu shows them in the order they're written, so you can add, remove,
rename and reorder freely. If your config has no presets at all, the six above are used. A preset has:

| Key | Values | Notes |
|---|---|---|
| `description` | any text | what the menu shows; the name is used if it's left out |
| `action` | `check`, `local`, `upload`, `auto`, `setup`, `update`, `probe` | `local` = list what's on disk, no network |
| `what` | `missing`, `new`, `all` | `upload` / `auto` only. Left out: `upload_assistant.verdicts` |
| `limit` | a number | `upload` / `auto` only. At most this many uploads per run |
| `dry_run` | `true` / `false` | `upload` / `auto` only. Show what would go, upload nothing |

For example, a slow drip of three missing titles a run:

```toml
[presets.drip]
description = "Upload 3 missing titles, unattended"
action = "auto"
what = "missing"
limit = 3
```

A preset can't be named after a command (`setup`, `check`, `upload`, `auto`, `probe`), and a typo in
`action` or `what` stops the script with a message saying which preset is wrong.

### Commands

Under the presets are five commands, which you can also type directly:

| Command | What it does |
|---|---|
| `setup` | once: install (or `--update`) Upload-Assistant with the ZNTH module |
| `check` | scan your folders, check the site, write a report. Never uploads. `--local` skips the site |
| `upload` | upload from the last report, asking y/n/q before each one |
| `auto` | `check`, then upload the whole batch unattended |
| `probe` | show the site's category / type / resolution ids |

`upload` and `auto` share three choices:

| Flag | Meaning |
|---|---|
| `--missing` / `--new` / `--all` | titles the site doesn't have / new resolutions or types of ones it has / both. Default: `upload_assistant.verdicts` in `config.toml` (MISSING) |
| `-l N` / `--limit N` | at most N uploads this run |
| `-n` / `--dry-run` | show exactly what would go, upload nothing |

`-v` on `check` and `auto` prints each lookup as it goes. `-c FILE` (before everything else) uses another
config file. The older spellings (`install-ua`, `check --scan-only`, `run --unattended --yes`, `--only ...`,
`--include-new-quality`) still work.

### What a check writes

`check` (and `auto`, and the `report` preset) writes to `reports/`:

| File | What's in it |
|---|---|
| `report-*.csv` | every item, its verdict, why, and what's already on the site (opens in Excel/Sheets) |
| `report-*.json` / `latest.json` | the same, used by `upload` |
| `upload-commands-*.sh` | ready-to-run Upload-Assistant commands for the MISSING items, NEW_QUALITY ones commented out |
| `upload-log-*.csv` | written by each upload run: what was sent, how it went, whether it's visible on the site yet |

### Verdicts

| Verdict | Meaning |
|---|---|
| `MISSING` | Not on the site (for TV: no season pack for that season). Upload candidate. |
| `NEW_QUALITY` | Title is there, but not at your resolution + type (e.g. you have a 2160p remux, site has 1080p WEB-DL). Check the site's rules before uploading; `--new` (or `--all`) sends these. |
| `DUPE` | Same resolution and type already on the site. |
| `UNMATCHED` | Couldn't confidently identify it on TMDB. Rename the folder or check by hand. |
| `SKIPPED` | A banned release group; a type/resolution the site doesn't take (HDTV, 1080i...); a season pack that's incomplete or still airing (checked against TMDB); or a pack folder holding other video files that would be uploaded with it. The reason column says which. |
| `ERROR` | Network/API problem; rerun later. |

## Unattended uploads (`auto`)

`auto` is a fresh `check` followed by an unattended upload of the batch. Typing it (or picking a preset
that uses it) is the opt-in: at a terminal it lists the batch and asks you to type `upload` once; under
cron (no terminal) it goes straight ahead. `upload --unattended` does the same from an existing report
(add `--yes` to skip the question).

Every Upload-Assistant call it makes gets:

- `--unattended`: accepts the TMDB/IMDb/etc. matches it finds through tracker APIs without asking,
- `--skip-dupe-asking`: if it finds a dupe on znth.cx it skips that item instead of asking,
- `--onlyID` (when `own_screenshots = true`): takes only the ids from other trackers, not their
  descriptions or screenshots, so Upload-Assistant makes fresh screenshots from your file and
  uploads them to the image host set in its `data/config.py` (`img_host_1`, ...). Upload-Assistant
  would still reuse other sites' screenshots in unattended mode while its `keep_images` setting is on,
  so `setup` turns it off (with a backup), and unattended runs refuse to start while it's still on.

Safety rails:

- **Before it starts:** it refuses if the report is more than 24 hours old, if ZNTH isn't registered
  in Upload-Assistant, if the ZNTH `api_key` there is empty (Upload-Assistant would silently skip the
  site), or if `keep_images` is on. Then it lists the batch (and asks once, at a terminal).
- **What it holds back** (listed, for you to do by hand): items whose TMDB match isn't near-exact or
  whose year doesn't agree, items with no TMDB id, and a second copy of something already uploaded
  or already in the batch (same title, season/episode, resolution and type).
- **While it runs:** Upload-Assistant gets no keyboard, so it can never hang on a question. It stops after
  3 failures in a row. Only one upload run can go at a time, so overlapping cron jobs can't double-upload.
  `-l`, a preset's `limit`, or `upload_assistant.unattended_limit` caps a run.
- **After each upload:** it checks the site again and logs whether the upload is visible
  (`reports/upload-log-*.csv`). Anything Upload-Assistant finished is recorded and never sent again,
  even if it isn't visible yet (moderation queue), because uploading twice is worse than waiting.
- **Rehearsals:** the `rehearsal` preset (`-n`) only lists what would go. For a deeper one, set
  `extra_args = ["--debug"]` under `[upload_assistant]`: Upload-Assistant then runs every step without
  posting, and nothing is recorded as uploaded, so the real run still sends everything.

Example cron line, running the `nightly` preset at 3am (at most 10 uploads, unless you change the preset).
Keep the keys in `config.toml` (chmod 600) rather than on the cron line, where other users of the machine
can see them:

```
0 3 * * * cd "$HOME/Znth Upload" && python3 zenith_check.py nightly >> run.log 2>&1
```

## How it decides

- File and folder names are parsed with `guessit` (title, year, season, resolution, source, group).
- A folder of episodes from one show and season is treated as one **season pack** (`tv_mode = "season"`);
  set `tv_mode = "episode"` to check each file. A folder mixing seasons or shows is checked episode by
  episode instead, because uploading the folder would carry everything in it. A folder with `BDMV` /
  `VIDEO_TS` is one full-disc item.
- **Banned groups**: znth.cx's banned list (45 groups, e.g. FGT, YIFY, RARBG, GalaxyRG, TGx) is built in,
  and `scan.banned_groups` in `config.toml` adds your own. On each `check` it also tries to pull the
  site's live list from its API (`/api/blacklists/releasegroups`, which newer UNIT3D versions offer)
  and adds anything new; if the site doesn't offer it, the built-in list is used. The group is looked for
  everywhere it tends to hide: `-GROUP` at the end, `[TGx]`-style tags, `[Group]` at the start of anime
  names, and with x265-style numbers dropped (`GalaxyRG265`). `BRrip` counts anywhere in the name. One banned
  episode bans its whole season pack. The ZNTH module carries the same list, so Upload-Assistant refuses them too
  (run `setup` once more to update the module).
- Files changed in the last `min_age_minutes` (default 60) are left out, so half-downloaded or
  half-copied files are never picked up.
- Anime is detected by the directory `type`, a folder named like "Anime", or TMDB (animation + Japanese).
- Each title is matched on TMDB, then the site is searched by TMDB id (plus season for TV) through
  the standard UNIT3D `/api/torrents/filter` endpoint. Without a TMDB key it falls back to a name
  search, which is less reliable.
- Lookups are cached in `data/cache.json` for `cache_hours`, and successful uploads are recorded in
  `data/uploaded.json` so nothing is uploaded twice. Requests are spaced by `request_delay`, back off on
  HTTP 429, and page through results so busy shows aren't cut off at 100 torrents. The API key goes in a
  request header, never in the URL.

## Installing Upload-Assistant

Upload-Assistant doesn't ship a znth.cx module, so this folder includes one (`ua_tracker/ZNTH.py`,
built from Upload-Assistant's UNIT3D template with znth.cx's ids). One command sets everything up:

```sh
python3 zenith_check.py setup                      # installs to ~/Upload-Assistant
```

It:

1. clones Upload-Assistant into that folder (it must be new or empty),
2. creates a `venv` there and installs its requirements,
3. copies `ZNTH.py` into `src/trackers/` and registers it in `src/trackersetup.py`,
4. creates `data/config.py` from the example if needed and adds a `ZNTH` block. If `ZNTH_API_KEY`
   and `TMDB_API_KEY` are exported, it fills them in; otherwise edit `data/config.py` yourself,
5. warns if `ffmpeg` or `mediainfo` are missing (`sudo apt install ffmpeg mediainfo`).

It installs to `upload_assistant.path` from `config.toml` (default `~/Upload-Assistant`); `--path` picks
another folder, which must be one you can write to (not `/opt` unless you `sudo chown` it first). Run it
again any time; `--update` (or the `update` preset) pulls the latest Upload-Assistant and re-applies the
ZNTH module. `upload` and `auto` use the install's `venv` automatically.

### If you already have Upload-Assistant

Point `--path` (or `upload_assistant.path`) at it. Without `--update` it doesn't change the
Upload-Assistant version: it only adds `ZNTH.py`, registers it, and adds a `ZNTH` block to your
existing `data/config.py`. A timestamped backup (`config.py.bak-...`) is made first, and nothing else
in your config is touched. With `--update` it also runs `git pull` to get the latest version.
`data/config.py` isn't tracked by git, so it's kept. If you've edited any of Upload-Assistant's own
files, it stops and tells you how to keep them (`git stash`) rather than overwriting anything. A very
old copy that predates the shared UNIT3D tracker code needs `--update` before ZNTH can work. A
copy that wasn't installed with git can't be updated in place.

You still need to finish Upload-Assistant's own settings (torrent client, screenshot host, etc.)
in `data/config.py`, or with its `config-generator.py`. Do a rehearsal first (see the Rehearsals
point under [Unattended uploads](#unattended-uploads-auto)).

The ZNTH module refuses releases the site has no slot for (HDTV, 4320p, interlaced resolutions)
so they never reach the upload form. Upload-Assistant runs its own dupe check too, as a second safety net.

## Tests

`python3 test_zenith.py` builds a fake library and fakes TMDB + the site API; no network or keys needed.
