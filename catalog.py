#!/usr/bin/env python3
"""Read-only music library catalog and update engine."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "library.db"
STATIC = ROOT / "static"
DEFAULT_LIBRARY = Path("/mnt/TheBackup/Music")
AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif"}
USER_AGENT = "MusicLibraryInsights/0.1 (personal local catalog)"
UPDATE_STATE = {"running": False, "step": "idle", "current": 0, "total": 0, "message": "", "error": None}
ARTIST_ALIASES = {
    "Blues Travelers": "Blues Traveler",
    "Matchbox 20": "Matchbox Twenty",
    "Toad The West Sprocket": "Toad the Wet Sprocket",
}
EXCLUDED_DISCOGRAPHY_ARTISTS = {"Soundtracks"}


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS scans (
          id INTEGER PRIMARY KEY, scanned_at TEXT NOT NULL, root TEXT NOT NULL,
          artists INTEGER NOT NULL, albums INTEGER NOT NULL, tracks INTEGER NOT NULL,
          bytes INTEGER NOT NULL, newest_mtime TEXT
        );
        CREATE TABLE IF NOT EXISTS artists (
          name TEXT PRIMARY KEY, normalized TEXT NOT NULL, album_count INTEGER NOT NULL,
          track_count INTEGER NOT NULL, bytes INTEGER NOT NULL, first_mtime TEXT, last_mtime TEXT
        );
        CREATE TABLE IF NOT EXISTS albums (
          artist TEXT NOT NULL, title TEXT NOT NULL, normalized TEXT NOT NULL,
          year INTEGER, track_count INTEGER NOT NULL, bytes INTEGER NOT NULL,
          first_mtime TEXT, last_mtime TEXT, PRIMARY KEY (artist, title)
        );
        CREATE TABLE IF NOT EXISTS tracks (
          path TEXT PRIMARY KEY, artist TEXT NOT NULL, album TEXT NOT NULL, title TEXT NOT NULL,
          extension TEXT NOT NULL, bytes INTEGER NOT NULL, duration REAL, modified_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artist_matches (
          artist TEXT PRIMARY KEY, mbid TEXT, matched_name TEXT, country TEXT,
          score INTEGER, status TEXT NOT NULL, checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS release_groups (
          artist TEXT NOT NULL, mbid TEXT NOT NULL, title TEXT NOT NULL, first_release_date TEXT,
          secondary_types TEXT NOT NULL DEFAULT '', PRIMARY KEY (artist, mbid)
        );
    """)
    return db


def normalize(value: str) -> str:
    value = re.sub(r"^\s*\d{4}\s*[-–—]\s*", "", value)
    value = re.sub(r"\s*\([^)]*(deluxe|expanded|remaster|anniversary)[^)]*\)\s*$", "", value, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def iso_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def wav_duration(path: Path) -> float | None:
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, OSError, EOFError):
        return None


def scan_library(root: Path) -> dict:
    root = root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Music root not found: {root}")
    rows = []
    skipped = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            skipped += 1
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        artist = parts[0] if len(parts) >= 2 else "Unknown Artist"
        album = parts[1] if len(parts) >= 3 else "Loose Tracks"
        title = re.sub(r"^\s*\d+[ ._-]+", "", path.stem).strip() or path.stem
        rows.append((str(relative), artist, album, title, path.suffix.lower()[1:], stat.st_size,
                     wav_duration(path), iso_mtime(stat.st_mtime)))

    db = connect()
    with db:
        db.execute("DELETE FROM tracks")
        db.execute("DELETE FROM albums")
        db.execute("DELETE FROM artists")
        db.executemany("INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?)", rows)
        db.execute("""INSERT INTO albums
          SELECT artist, album, '', NULL, COUNT(*), SUM(bytes), MIN(modified_at), MAX(modified_at)
          FROM tracks GROUP BY artist, album""")
        for row in db.execute("SELECT artist,title FROM albums").fetchall():
            match = re.match(r"^\s*(\d{4})\s*[-–—]", row["title"])
            db.execute("UPDATE albums SET normalized=?, year=? WHERE artist=? AND title=?",
                       (normalize(row["title"]), int(match.group(1)) if match else None, row["artist"], row["title"]))
        db.execute("""INSERT INTO artists
          SELECT artist, '', COUNT(DISTINCT album), COUNT(*), SUM(bytes), MIN(modified_at), MAX(modified_at)
          FROM tracks GROUP BY artist""")
        for row in db.execute("SELECT name FROM artists").fetchall():
            db.execute("UPDATE artists SET normalized=? WHERE name=?", (normalize(row["name"]), row["name"]))
        totals = db.execute("SELECT COUNT(*) tracks, COUNT(DISTINCT artist) artists, COUNT(DISTINCT artist||char(0)||album) albums, COALESCE(SUM(bytes),0) bytes, MAX(modified_at) newest FROM tracks").fetchone()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.execute("INSERT INTO scans(scanned_at,root,artists,albums,tracks,bytes,newest_mtime) VALUES (?,?,?,?,?,?,?)",
                   (now, str(root), totals["artists"], totals["albums"], totals["tracks"], totals["bytes"], totals["newest"]))
    result = dict(totals)
    result.update(scanned_at=now, skipped=skipped)
    return result


def mb_request(path: str, params: dict) -> dict:
    url = "https://musicbrainz.org/ws/2/" + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def sync_artist(artist: str) -> dict:
    db = connect()
    local = db.execute("SELECT name FROM artists WHERE name=? COLLATE NOCASE", (artist,)).fetchone()
    if not local:
        raise ValueError(f"Local artist not found: {artist}")
    artist = local["name"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if artist in EXCLUDED_DISCOGRAPHY_ARTISTS:
        with db:
            db.execute("INSERT OR REPLACE INTO artist_matches VALUES (?,?,?,?,?,?,?)",
                       (artist, None, None, None, 100, "excluded", now))
            db.execute("DELETE FROM release_groups WHERE artist=?", (artist,))
        return {"artist": artist, "status": "excluded", "releases": 0}
    search_name = ARTIST_ALIASES.get(artist, artist)
    result = mb_request("artist", {"query": f'artist:"{search_name}"', "limit": 5})
    candidates = result.get("artists", [])
    exact = [x for x in candidates if normalize(x.get("name", "")) == normalize(search_name)]
    chosen = (exact or candidates or [None])[0]
    if not chosen:
        with db:
            db.execute("INSERT OR REPLACE INTO artist_matches VALUES (?,?,?,?,?,?,?)", (artist, None, None, None, 0, "not_found", now))
        return {"artist": artist, "status": "not_found", "releases": 0}
    mbid = chosen["id"]
    releases = []
    offset = 0
    while True:
        page = mb_request("release-group", {"artist": mbid, "type": "album", "limit": 100, "offset": offset})
        groups = page.get("release-groups", [])
        releases.extend(groups)
        offset += len(groups)
        if not groups or offset >= page.get("release-group-count", 0):
            break
        time.sleep(1.05)
    with db:
        db.execute("INSERT OR REPLACE INTO artist_matches VALUES (?,?,?,?,?,?,?)",
                   (artist, mbid, chosen.get("name"), chosen.get("country"), chosen.get("score", 0), "matched", now))
        db.execute("DELETE FROM release_groups WHERE artist=?", (artist,))
        db.executemany("INSERT INTO release_groups VALUES (?,?,?,?,?)", [
            (artist, x["id"], x["title"], x.get("first-release-date"), ", ".join(x.get("secondary-types", []))) for x in releases
        ])
    return {"artist": artist, "matched_name": chosen.get("name"), "country": chosen.get("country"), "score": chosen.get("score"), "releases": len(releases)}


def dashboard_data() -> dict:
    db = connect()
    latest = db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    artists = [dict(x) for x in db.execute("""SELECT a.*, m.matched_name, m.country, m.score, m.status,
      (SELECT COUNT(*) FROM release_groups r WHERE r.artist=a.name AND r.secondary_types='') known_releases
      FROM artists a LEFT JOIN artist_matches m ON m.artist=a.name ORDER BY a.name COLLATE NOCASE""")]
    scans = [dict(x) for x in db.execute("SELECT * FROM scans ORDER BY id")]
    formats = [dict(x) for x in db.execute("SELECT extension,COUNT(*) tracks,SUM(bytes) bytes FROM tracks GROUP BY extension ORDER BY tracks DESC")]
    recent = [dict(x) for x in db.execute("SELECT artist,album,title,modified_at FROM tracks ORDER BY modified_at DESC LIMIT 12")]
    growth = [dict(x) for x in db.execute("""WITH daily AS (
      SELECT substr(modified_at,1,10) added_on, COUNT(*) tracks_added,
             SUM(bytes) bytes_added
      FROM tracks GROUP BY substr(modified_at,1,10)
    ) SELECT added_on, tracks_added, bytes_added,
      SUM(tracks_added) OVER (ORDER BY added_on) tracks,
      SUM(bytes_added) OVER (ORDER BY added_on) bytes
      FROM daily ORDER BY added_on""")]
    # Artist and album totals need first-seen dates rather than sums of daily distinct counts.
    for point in growth:
        point["artists"] = db.execute(
            "SELECT COUNT(*) FROM artists WHERE substr(first_mtime,1,10)<=?", (point["added_on"],)).fetchone()[0]
        point["albums"] = db.execute(
            "SELECT COUNT(*) FROM albums WHERE substr(first_mtime,1,10)<=?", (point["added_on"],)).fetchone()[0]
    return {"latest": dict(latest) if latest else None, "artists": artists, "scans": scans,
            "growth": growth, "formats": formats, "recent": recent}


def artist_data(name: str) -> dict:
    db = connect()
    artist = db.execute("SELECT * FROM artists WHERE name=?", (name,)).fetchone()
    if not artist:
        raise KeyError(name)
    albums = [dict(x) for x in db.execute("SELECT * FROM albums WHERE artist=? ORDER BY COALESCE(year,9999),title COLLATE NOCASE", (name,))]
    match = db.execute("SELECT * FROM artist_matches WHERE artist=?", (name,)).fetchone()
    releases = [dict(x) for x in db.execute("SELECT * FROM release_groups WHERE artist=? AND secondary_types='' ORDER BY first_release_date,title", (name,))]
    owned = {x["normalized"] for x in albums}
    for release in releases:
        release["owned"] = normalize(release["title"]) in owned
    return {"artist": dict(artist), "albums": albums, "match": dict(match) if match else None, "discography": releases}


def export_static() -> Path:
    """Write a metadata-only GitHub Pages build; never copy audio or file paths."""
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    payload = dashboard_data()
    payload["artist_details"] = {artist["name"]: artist_data(artist["name"]) for artist in payload["artists"]}
    (docs / "data.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    shutil.copy2(STATIC / "index.html", docs / "index.html")
    (docs / ".nojekyll").touch()
    return docs / "data.json"


def git_publish() -> str:
    if not (ROOT / ".git").exists():
        return "Catalog exported; Git repository is not configured yet"
    subprocess.run(["git", "add", "docs/data.json"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
    if changed:
        subprocess.run(["git", "commit", "-m", "Update music catalog"], cwd=ROOT, check=True)
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    return "Catalog committed and pushed" if changed else "Catalog unchanged; Git push checked"


def git_publish_changes(message: str) -> str:
    """Commit and push existing project changes without scanning or exporting."""
    if not (ROOT / ".git").exists():
        raise RuntimeError("This app is not connected to a Git repository")
    subprocess.run(["git", "add", "--all"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
    if not changed:
        return "No unpublished changes"
    subprocess.run(["git", "commit", "-m", message], cwd=ROOT, check=True)
    subprocess.run(["git", "push", "origin", "HEAD"], cwd=ROOT, check=True)
    return "Changes committed and pushed to GitHub"


def _artist_snapshot(db: sqlite3.Connection) -> dict[str, dict]:
    return {row["name"]: dict(row) for row in db.execute(
        "SELECT name,album_count,track_count,last_mtime FROM artists"
    )}


def _smart_update_candidates(db: sqlite3.Connection, before: dict[str, dict], stale_days: int) -> list[tuple[str, str]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    candidates = []
    rows = db.execute("""SELECT a.name,a.album_count,a.track_count,a.last_mtime,
      m.status,m.checked_at FROM artists a LEFT JOIN artist_matches m ON m.artist=a.name
      ORDER BY a.name COLLATE NOCASE""")
    for row in rows:
        previous = before.get(row["name"])
        if not previous:
            reason = "new artist"
        elif any(previous.get(field) != row[field] for field in ("album_count", "track_count", "last_mtime")):
            reason = "local collection changed"
        elif not row["checked_at"]:
            reason = "never checked"
        elif row["status"] not in {"matched", "excluded"}:
            reason = f"previous status: {row['status'] or 'unknown'}"
        else:
            try:
                checked = datetime.fromisoformat(row["checked_at"])
                reason = f"last checked over {stale_days} days ago" if checked < cutoff else ""
            except (TypeError, ValueError):
                reason = "invalid last-checked date"
        if reason:
            candidates.append((row["name"], reason))
    return candidates


def _run_update(publish: bool, progress, smart: bool, stale_days: int = 30) -> None:
    def report(percent: int, message: str) -> None:
        if progress:
            progress(percent, message)

    if UPDATE_STATE["running"]:
        return
    UPDATE_STATE.update(running=True, step="scan", current=0, total=0, message="Scanning local library", error=None)
    report(0, "Scanning the local music library")
    try:
        before = _artist_snapshot(connect()) if smart else {}
        scan_library(DEFAULT_LIBRARY)
        report(5, "Library scan complete")
        db = connect()
        selected = (_smart_update_candidates(db, before, stale_days) if smart else
                    [(x[0], "complete refresh") for x in db.execute("SELECT name FROM artists ORDER BY name COLLATE NOCASE")])
        names = [name for name, _ in selected]
        reasons = dict(selected)
        UPDATE_STATE.update(step="discographies", total=len(names), message="Checking artist discographies")
        failures = []
        for number, name in enumerate(names, 1):
            UPDATE_STATE.update(current=number, message=f"Checking {name}")
            percent = 5 + round(85 * (number - 1) / max(len(names), 1))
            report(percent, f"Checking {name} · {reasons[name]} · artist {number} of {len(names)}")
            try:
                sync_artist(name)
            except Exception as error:
                failures.append(f"{name}: {error}")
            time.sleep(1.05)  # MusicBrainz asks clients to stay at or below one request/second.
        UPDATE_STATE.update(step="export", message="Building hosted catalog")
        report(92, "Building the hosted catalog")
        export_static()
        if smart and not names:
            message = "Library scanned; all artist catalogs are current"
        else:
            message = f"Updated {len(names) - len(failures)} of {len(names)} selected artist catalogs"
        if publish:
            UPDATE_STATE.update(step="publish", message="Publishing to GitHub")
            report(97, "Publishing the refreshed catalog")
            message += ". " + git_publish()
        if failures:
            message += f"; {len(failures)} artist checks will retry next time"
        UPDATE_STATE.update(step="complete", message=message)
        report(100, message)
    except Exception as error:
        UPDATE_STATE.update(step="failed", error=str(error), message="Update failed")
    finally:
        UPDATE_STATE["running"] = False


def update_smart(publish: bool = True, progress=None, stale_days: int = 30) -> None:
    _run_update(publish, progress, smart=True, stale_days=stale_days)


def update_everything(publish: bool = True, progress=None) -> None:
    _run_update(publish, progress, smart=False)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def json_response(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/dashboard":
                return self.json_response(dashboard_data())
            if parsed.path == "/api/artist":
                name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
                return self.json_response(artist_data(name))
            if parsed.path == "/api/update-status":
                return self.json_response(UPDATE_STATE)
        except KeyError as error:
            return self.json_response({"error": str(error)}, 404)
        return super().do_GET()

    def do_POST(self):
        try:
            if self.path == "/api/scan":
                return self.json_response(scan_library(DEFAULT_LIBRARY))
            if self.path.startswith("/api/sync"):
                name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", [""])[0]
                return self.json_response(sync_artist(name))
            if self.path == "/api/update-all":
                if not UPDATE_STATE["running"]:
                    threading.Thread(target=update_everything, daemon=True).start()
                return self.json_response(UPDATE_STATE, 202)
        except Exception as error:
            return self.json_response({"error": str(error)}, 500)
        self.json_response({"error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--root", type=Path, default=DEFAULT_LIBRARY)
    sync = sub.add_parser("sync")
    sync.add_argument("artist")
    update = sub.add_parser("update-all")
    update.add_argument("--no-push", action="store_true")
    sub.add_parser("export")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "scan":
        print(json.dumps(scan_library(args.root), indent=2))
    elif args.command == "sync":
        print(json.dumps(sync_artist(args.artist), indent=2))
    elif args.command == "update-all":
        update_everything(not args.no_push)
        print(json.dumps(UPDATE_STATE, indent=2))
        if UPDATE_STATE["error"]:
            raise SystemExit(1)
    elif args.command == "export":
        print(export_static())
    else:
        connect().close()
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"Music Library Insights: http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
