# Changes from the 2026-10-25 review

## Bugs fixed

- **A folder holding more than one season** (or two shows) used to be treated as one season pack,
  so uploading it would have sent every season in it. It's now checked episode by episode.
- **Incomplete or still-airing season packs** were reported MISSING. They're now checked against
  TMDB's episode list and marked SKIPPED with the counts (e.g. "2 of 13 aired episodes").
- **Pack folders with other video files** (a stray movie, a new download) are SKIPPED, since the
  extra files would go up with the pack.
- **Files still downloading or copying** could be picked up. Files changed in the last
  `scan.min_age_minutes` (default 60) are now left out, discs included.
- **Busy shows with over 100 torrents** in a season were cut off at the first page of results,
  which could hide an existing pack and cause a false MISSING. Results are now paged.
- **`--debug` rehearsals were recorded as uploaded**, so the real run afterwards would have
  skipped everything. Rehearsals are no longer recorded.
- **Two copies of the same release** (same title, season/episode, resolution and type in two folders)
  would both be uploaded, one per run. The second copy is now held back, in the same run or a later one.
- **`keep_images` check**: a config without the key (Upload-Assistant then treats it as on), or with it
  as the last entry (no trailing comma), slipped past the check. Both are caught now, and `install-ua` fixes both.
- **Editing Upload-Assistant's `config.py`**: changes are now syntax-checked before saving. If an edit
  would break the file, it's left untouched. A freshly created config no longer gets a pointless backup.
- **`--update` on a pinned copy** could reset a local `master` branch that had your own commits on it.
  It now refuses instead. It also removes our untracked `ZNTH.py` before pulling (an upstream one
  would otherwise block the pull), restores it if the pull fails, and steps aside if Upload-Assistant
  ever ships its own ZNTH module.
- **A bad TMDB key** marked every item ERROR one by one. It now stops at once, the same as a bad site key.
- An odd `Retry-After` header from the site, or a report from an older version, no longer crashes it.
- Pressing Enter at the per-item prompt now means "no" (it used to be the same as any other answer).
  Running without a terminal is handled cleanly.

## New guardrails for unattended mode

- Refuses to start if the report is over 24 hours old, if ZNTH isn't registered in Upload-Assistant,
  if the ZNTH `api_key` there is empty (Upload-Assistant would silently skip the site), or if `keep_images` is on.
- Holds back (and lists) items whose TMDB match isn't near-exact or whose year doesn't agree,
  e.g. "Night Watch" matched to "The Night Watch". Interactive mode still offers them, with the match shown.
- Upload-Assistant gets no keyboard input, so it can never hang waiting on a question under cron.
- Only one upload run at a time (lock file), so overlapping cron jobs can't double-upload.
- After each upload it checks the site and logs whether the upload is visible yet.
- The site API key is sent in a request header instead of the URL.
- Config typos (`tv_mode`, a directory `type`, a missing `path`) give a plain error instead of a traceback.

## Banned groups (later on 2026-10-25)

- znth.cx's 45 banned groups are built in; `scan.banned_groups` now adds to them instead of being the whole list.
- `check` also pulls the site's live list from its API when it offers one (`tracker.fetch_banned_groups`).
- Group names are found in more places: trailing `-GROUP`, `[TGx]`/`{GROUP}` tags, a leading `[Group]`,
  and x265-style suffixes (`GalaxyRG265`). `BRrip` matches anywhere. A season pack is skipped if any episode is banned.
- The ZNTH module for Upload-Assistant has the same list, with the reason shown when it skips one.

## Shorter command line (later on 2026-10-25)

- Five commands: `setup`, `check`, `upload`, `auto`, `probe`. `auto` = fresh `check` + unattended upload;
  it asks once at a terminal and needs no `--yes` under cron.
- `--missing` / `--new` / `--all` replace `--only ...`; `-l N` = `--limit N`; `-n` = `--dry-run`;
  `check --local` = `check --scan-only`.
- Old spellings (`install-ua`, `run --unattended --yes`, `--only`, `--include-new-quality`, `--scan-only`) still work.

## Presets and a menu (later on 2026-10-25)

- `[presets.<name>]` in `config.toml`: `description`, `action` (check / local / upload / auto / setup /
  update / probe), `what` (missing / new / all), `limit`, `dry_run`. Run one by name: `zenith_check.py nightly`.
  Flags after the name override it.
- Running the script with no arguments at a terminal shows a numbered menu of your presets, in file order.
  Without a terminal it runs `check`.
- Six default presets ship in `config.example.toml` (and are used if your config has none).
