# Music Library Insights

A read-only catalog and dashboard for `/mnt/TheBackup/Music`.

## Run

```bash
cd /home/jacob/music-library-insights
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python catalog.py scan
.venv/bin/streamlit run app.py
```

Open the URL Streamlit prints, normally `http://127.0.0.1:8501`.

The scanner never changes the music library. It stores its catalog, scan history,
and cached MusicBrainz results in `data/library.db`.

## Commands

```bash
python3 catalog.py scan                         # record a new snapshot
python3 catalog.py sync "Linkin Park"           # fetch one artist's album catalog
python3 catalog.py update-all                    # scan, refresh all, export, and push
python3 catalog.py export                        # rebuild docs/ without network access
```

MusicBrainz matching is intentionally per artist: review the match in the app,
then fetch the discography. This avoids silently assigning an ambiguous artist
name to the wrong band.

The **Update everything** button runs the complete workflow in the background.
The GitHub Pages build in `docs/` contains only catalog metadata, not audio or
absolute filesystem paths.
