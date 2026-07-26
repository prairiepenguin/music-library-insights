#!/usr/bin/env python3
"""Streamlit UI for Music Library Insights."""
from __future__ import annotations
import json
import html
from pathlib import Path
from urllib.parse import quote
import streamlit as st
import catalog

ROOT = Path(__file__).resolve().parent

@st.cache_data(ttl=30)
def load_data():
    if catalog.DB.exists(): return catalog.dashboard_data(), True
    export = ROOT / "docs" / "data.json"
    if export.exists(): return json.loads(export.read_text(encoding="utf-8")), False
    return {"latest":None,"artists":[],"growth":[],"formats":[],"recent":[]}, False

def human_size(value):
    value=float(value)
    for unit in ("B","KB","MB","GB","TB"):
        if abs(value)<1024: return f"{value:.1f} {unit}"
        value/=1024
    return f"{value:.1f} PB"

def detail(name,data,live):
    return catalog.artist_data(name) if live else data.get("artist_details",{}).get(name,{"artist":{},"albums":[],"discography":[],"match":None})

def cover_url(mbid):
    """Return a display-only Cover Art Archive URL for a release group."""
    return f"https://coverartarchive.org/release-group/{quote(str(mbid))}/front-500" if mbid else None

def album_card(album, *, owned):
    title=html.escape(str(album.get("title") or "Untitled album"))
    year=html.escape(str(album.get("first_release_date") or album.get("year") or "Year unknown")[:4])
    badge="Owned" if owned else "Missing"
    badge_class="owned" if owned else "missing"
    image=cover_url(album.get("mbid"))
    art=(f'<img src="{image}" alt="Cover art for {title}" loading="lazy">' if image
         else '<div class="album-placeholder"><span>♪</span></div>')
    st.markdown(
        f'''<div class="album-card">{art}<div class="album-copy">
        <div class="album-title">{title}</div>
        <div class="album-meta">{year}<span class="album-badge {badge_class}">{badge}</span></div>
        </div></div>''', unsafe_allow_html=True)

st.set_page_config(page_title="Music Library Insights",page_icon="💿",layout="wide")
st.markdown("""<style>
.block-container{padding-top:1.6rem;padding-bottom:3rem;max-width:1500px}
[data-testid="stMetric"]{background:linear-gradient(145deg,rgba(35,42,58,.78),rgba(19,23,33,.78));border:1px solid rgba(255,255,255,.10);padding:1rem 1.1rem;border-radius:16px}
[data-testid="stMetricValue"]{font-weight:750}
.hero{padding:.3rem 0 1.1rem}.hero h1{margin:0;font-size:2.35rem;letter-spacing:-.04em}.hero p{margin:.35rem 0 0;color:rgba(255,255,255,.66);font-size:1.05rem}
.album-card{overflow:hidden;border:1px solid rgba(255,255,255,.11);border-radius:15px;background:rgba(22,27,39,.78);box-shadow:0 8px 26px rgba(0,0,0,.16);margin-bottom:1rem;transition:transform .16s ease,border-color .16s ease}
.album-card:hover{transform:translateY(-2px);border-color:rgba(242,184,75,.55)}
.album-card img,.album-placeholder{display:block;width:100%;aspect-ratio:1/1;object-fit:cover;background:linear-gradient(145deg,#293247,#151a26)}
.album-placeholder{display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.3);font-size:3.4rem}
.album-copy{padding:.75rem .8rem .85rem;min-height:6.4rem}.album-title{font-weight:720;line-height:1.22;min-height:2.45rem}.album-meta{display:flex;align-items:center;justify-content:space-between;margin-top:.55rem;color:rgba(255,255,255,.58);font-size:.82rem}
.album-badge{padding:.16rem .48rem;border-radius:999px;font-size:.7rem;font-weight:750;letter-spacing:.02em}.album-badge.owned{color:#77e2ac;background:rgba(41,171,104,.16)}.album-badge.missing{color:#ffbd86;background:rgba(226,118,43,.16)}
@media(max-width:700px){
  .hero h1{font-size:1.9rem}.block-container{padding-top:1rem}
  .album-card{display:flex;align-items:stretch;margin-bottom:.55rem}
  .album-card img,.album-placeholder{flex:0 0 96px;width:96px;height:96px;aspect-ratio:auto}
  .album-placeholder{font-size:2rem}
  .album-copy{display:flex;flex:1;min-width:0;min-height:96px;padding:.65rem .75rem;flex-direction:column;justify-content:center}
  .album-title{min-height:0;font-size:.92rem}
  .album-meta{margin-top:.4rem}
}
</style>""",unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>Music Library Insights</h1><p>Know what you own. See what is missing. Watch the collection grow.</p></div>',unsafe_allow_html=True)
data,live=load_data(); latest=data.get("latest")

with st.sidebar:
    st.header("Library controls")
    if live and catalog.DEFAULT_LIBRARY.is_dir():
        smart_update=st.button("Smart update",type="primary",width="stretch",help="Scan everything, then refresh only new, changed, failed, never-checked, or stale artists.")
        complete_update=st.button("Complete update",width="stretch",help="Force a fresh MusicBrainz check for every artist.")
        if smart_update or complete_update:
            update_label="smart" if smart_update else "complete"
            with st.status(f"Running {update_label} update…",expanded=True) as status:
                st.write("This takes a few minutes because MusicBrainz requests are rate limited.")
                progress_bar=st.progress(0,text="Preparing update…")
                progress_message=st.empty()
                def show_progress(percent,message):
                    progress_bar.progress(percent,text=f"{percent}% complete")
                    progress_message.caption(message)
                updater=catalog.update_smart if smart_update else catalog.update_everything
                updater(publish=True,progress=show_progress); state=catalog.UPDATE_STATE
                status.update(label=(f"Update failed: {state['error']}" if state["error"] else state["message"]),state="error" if state["error"] else "complete")
                if not state["error"]: st.cache_data.clear(); st.rerun()
        st.caption("Live mode · Music drive and SQLite catalog available.")
    else:
        st.info("Hosted read-only catalog")
        st.caption("Run updates on Toothless, then push the refreshed catalog.")
    st.divider()
    st.subheader("GitHub")
    push_message=st.text_input("Commit message",value="Update music library insights")
    if st.button("Push changes only",width="stretch",help="Commit and push current project changes without scanning the music library."):
        try:
            with st.spinner("Publishing current changes…"):
                result=catalog.git_publish_changes(push_message.strip() or "Update music library insights")
            st.success(result)
        except Exception as error:
            st.error(f"GitHub push failed: {error}")
    st.caption("Does not scan, refresh MusicBrainz, or rebuild the catalog.")
    if latest: st.caption(f"Last catalog update: {latest['scanned_at'][:10]}")

if not latest:
    st.warning("No catalog exists yet. Run `python3 catalog.py scan` first."); st.stop()

overview,artists_tab,growth_tab=st.tabs(["Overview","Artists & missing albums","Growth"])
with overview:
    st.subheader("Search your library")
    landing_query=st.text_input(
        "Search your library",
        placeholder="Search artists, owned albums, or missing albums…",
        icon="🔎",
        label_visibility="collapsed",
        key="landing_search",
    ).strip()
    if landing_query:
        needle=landing_query.casefold()
        search_results=[]
        for summary in data.get("artists",[]):
            artist_name=summary["name"]
            artist_detail=detail(artist_name,data,live)
            matching_owned=[a for a in artist_detail.get("albums",[]) if needle in a["title"].casefold()]
            matching_missing=[r for r in artist_detail.get("discography",[]) if not r.get("owned") and needle in r["title"].casefold()]
            if needle in artist_name.casefold() or matching_owned or matching_missing:
                search_results.append((summary,artist_detail,matching_owned,matching_missing))
        if search_results:
            labels=[]; result_by_label={}
            for summary,artist_detail,matching_owned,matching_missing in search_results:
                match_count=len(matching_owned)+len(matching_missing)
                reason=(f"{match_count} matching album{'s' if match_count!=1 else ''}" if match_count else "artist match")
                label=f"{summary['name']} — {reason}"
                labels.append(label); result_by_label[label]=(summary,artist_detail,matching_owned,matching_missing)
            chosen_result=st.selectbox(f"{len(search_results)} artist result{'s' if len(search_results)!=1 else ''}",labels,key="landing_result")
            summary,found,matching_owned,matching_missing=result_by_label[chosen_result]
            all_missing=[r for r in found.get("discography",[]) if not r.get("owned")]
            known=len(found.get("discography",[])); owned_count=int(summary.get("album_count",0))
            coverage=100*min(owned_count,known)/known if known else 0
            st.markdown(f"### {summary['name']}")
            result_metrics=st.columns(4)
            result_metrics[0].metric("Owned albums",owned_count)
            result_metrics[1].metric("Missing albums",len(all_missing) if known else "—")
            result_metrics[2].metric("Collection",f"{coverage:.1f}%" if known else "—")
            result_metrics[3].metric("Tracks",f"{summary.get('track_count',0):,}")
            artist_match=needle in summary["name"].casefold()
            owned_to_show=found.get("albums",[]) if artist_match or not matching_owned else matching_owned
            missing_to_show=all_missing if artist_match or not matching_missing else matching_missing
            releases_by_title={catalog.normalize(r["title"]):r for r in found.get("discography",[]) if r.get("owned")}
            st.markdown(f"#### Owned · {len(owned_to_show)}")
            for start in range(0,min(len(owned_to_show),6),6):
                columns=st.columns(6)
                for column,album in zip(columns,owned_to_show[start:start+6]):
                    release=releases_by_title.get(catalog.normalize(album["title"]),{})
                    card={**album,"mbid":release.get("mbid"),"first_release_date":release.get("first_release_date")}
                    with column: album_card(card,owned=True)
            if not owned_to_show: st.caption("No owned albums match.")
            elif len(owned_to_show)>6: st.caption(f"Showing 6 of {len(owned_to_show)} owned albums.")
            st.markdown(f"#### Missing · {len(missing_to_show)}")
            for start in range(0,min(len(missing_to_show),6),6):
                columns=st.columns(6)
                for column,album in zip(columns,missing_to_show[start:start+6]):
                    with column: album_card(album,owned=False)
            if not missing_to_show: st.caption("No missing studio albums match.")
            elif len(missing_to_show)>6: st.caption(f"Showing 6 of {len(missing_to_show)} missing albums.")
        else:
            st.info(f'No artists, owned albums, or missing albums match “{landing_query}”.')
    st.divider()

    artist_rows=[]
    for artist in data.get("artists",[]):
        owned=int(artist.get("album_count",0)); known=int(artist.get("known_releases",0))
        if known:
            missing=max(known-owned,0); coverage=100*owned/known
            artist_rows.append({**artist,"missing":missing,"coverage":min(coverage,100)})
    total_missing=sum(x["missing"] for x in artist_rows)
    known_total=sum(int(x["known_releases"]) for x in artist_rows)
    owned_known=sum(min(int(x["album_count"]),int(x["known_releases"])) for x in artist_rows)
    overall=100*owned_known/known_total if known_total else 0
    cols=st.columns(5)
    for col,label,value in zip(cols,["Artists","Owned albums","Missing albums","Overall coverage","Lossless audio"],[latest["artists"],latest["albums"],f'{total_missing:,}',f'{overall:.1f}%',human_size(latest["bytes"])]): col.metric(label,value)
    st.caption("Coverage compares owned album folders with matched MusicBrainz studio-album release groups.")

    if artist_rows:
        progress_col,insight_col=st.columns([2,1],gap="large")
        with progress_col:
            st.subheader("Collection progress")
            chart_rows=sorted(artist_rows,key=lambda x:x["coverage"],reverse=True)
            st.bar_chart({x["name"]:x["coverage"] for x in chart_rows},horizontal=True,height=max(420,min(760,len(chart_rows)*24)),x_label="Collection complete (%)",y_label="Artist")
        with insight_col:
            st.subheader("Collection insights")
            incomplete=[x for x in artist_rows if x["missing"]]
            closest=sorted(incomplete,key=lambda x:(x["missing"],-x["coverage"]))[:5]
            opportunities=sorted(incomplete,key=lambda x:(-x["missing"],x["name"].casefold()))[:5]
            strongest=max(artist_rows,key=lambda x:(x["coverage"],x["album_count"]))
            st.success(f"Strongest collection: **{strongest['name']}** at **{strongest['coverage']:.1f}%**")
            st.markdown("**Closest to completing**")
            for artist in closest: st.write(f"{artist['name']} · {artist['missing']} album{'s' if artist['missing'] != 1 else ''} away")
            st.markdown("**Discovery opportunities**")
            for artist in opportunities: st.write(f"{artist['name']} · {artist['missing']} missing")

    st.divider()
    left,right=st.columns([2,1])
    with left:
        st.subheader("Recently added or modified")
        st.dataframe([{"Artist":x["artist"],"Album":x["album"],"Track":x["title"],"Date":x["modified_at"][:10]} for x in data.get("recent",[])],hide_index=True,width="stretch")
    with right:
        st.subheader("Formats")
        for x in data.get("formats",[]): st.metric(x["extension"].upper(),f'{x["tracks"]:,} tracks',human_size(x["bytes"]))

with artists_tab:
    query=st.text_input("Search artists",placeholder="Artist or band name",icon="🔎")
    names=[x["name"] for x in data.get("artists",[]) if query.casefold() in x["name"].casefold()]
    selected=st.selectbox("Artist",names,index=None,placeholder=f"Choose from {len(names)} artists")
    if selected:
        x=detail(selected,data,live); releases=x.get("discography",[]); missing=[r for r in releases if not r["owned"]]; match=x.get("match")
        cols=st.columns(4)
        for col,label,value in zip(cols,["Albums owned","Tracks","Studio albums listed","Missing albums"],[x["artist"].get("album_count",0),x["artist"].get("track_count",0),len(releases) or "—",len(missing) if releases else "—"]): col.metric(label,value)
        if match and match.get("status")=="matched": st.caption(f"MusicBrainz match: {match['matched_name']} · {match.get('country') or 'country unknown'} · confidence {match['score']}%")
        view=st.segmented_control("Albums to show",["Owned","Missing","All"],default="Owned",label_visibility="collapsed")
        owned_by_title={catalog.normalize(r["title"]):r for r in releases if r["owned"]}
        owned_cards=[]
        for album in x.get("albums",[]):
            release=owned_by_title.get(catalog.normalize(album["title"]),{})
            owned_cards.append({**album,"mbid":release.get("mbid"),"first_release_date":release.get("first_release_date")})
        cards=(owned_cards if view=="Owned" else missing if view=="Missing" else owned_cards+missing)
        if cards:
            for start in range(0,len(cards),6):
                columns=st.columns(6)
                for column,album in zip(columns,cards[start:start+6]):
                    with column: album_card(album,owned=album in owned_cards)
        elif view=="Missing" and not releases:
            st.info("Refresh this artist’s MusicBrainz catalog to see missing albums.")
        else:
            st.success("No albums in this view.")
        with st.expander("Album details"):
            owned_col,missing_col=st.columns(2)
            with owned_col:
                st.subheader("Owned"); st.dataframe([{"Album":a["title"],"Tracks":a["track_count"],"Size":human_size(a["bytes"])} for a in x.get("albums",[])],hide_index=True,width="stretch")
            with missing_col:
                st.subheader("Missing"); st.dataframe([{"Album":r["title"],"First released":r.get("first_release_date") or "—"} for r in missing],hide_index=True,width="stretch")
        st.caption("Your folders are the source of truth for ownership. Live albums and compilations are excluded.")
    else:
        st.dataframe([{"Artist":x["name"],"Albums owned":x["album_count"],"Tracks":x["track_count"],"Studio albums listed":x.get("known_releases",0)} for x in data.get("artists",[]) if x["name"] in names],hide_index=True,width="stretch")

with growth_tab:
    points=data.get("growth",[])
    if points:
        current=points[-1]; cols=st.columns(4)
        for col,label,value in zip(cols,["Growth rounds","Tracks accumulated","Albums accumulated","Audio accumulated"],[len(points),f'{current["tracks"]:,}',current["albums"],human_size(current["bytes"])]): col.metric(label,value)
        st.subheader("Cumulative tracks"); st.bar_chart({x["added_on"]:x["tracks"] for x in points},x_label="File date",y_label="Tracks")
        st.subheader("Growth rounds"); st.dataframe([{"Date":x["added_on"],"Tracks added":x["tracks_added"],"Total tracks":x["tracks"],"Total albums":x["albums"],"Total artists":x["artists"]} for x in points],hide_index=True,width="stretch")
        st.caption("The backup mount does not expose original NTFS creation time, so this uses retained file modification dates.")
