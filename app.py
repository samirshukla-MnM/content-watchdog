"""
Competitor Content Watchdog — Streamlit control panel.
"""
import io
import json
import os

import pandas as pd
import streamlit as st

from core import db, importer, monitor
from core.differ import render_diff_html
from core.notifier import CSS as DIFF_CSS

st.set_page_config(
    page_title="Content Watchdog",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init()

st.markdown("""
<style>
  .block-container{padding-top:2.2rem;max-width:1250px;}
  h1,h2,h3{letter-spacing:-.4px;}
  .metric-row{display:flex;gap:14px;margin:6px 0 22px;}
  .mcard{flex:1;background:#fff;border:1px solid #e4e9f0;border-radius:9px;
         padding:15px 18px;}
  .mcard .v{font-size:26px;font-weight:650;color:#0f2b46;line-height:1.15;}
  .mcard .l{font-size:11px;color:#7b8794;text-transform:uppercase;
            letter-spacing:.9px;margin-top:2px;}
  .pill{display:inline-block;font-size:10px;font-weight:700;padding:2px 9px;
        border-radius:11px;text-transform:uppercase;letter-spacing:.5px;}
  .p-own{background:#dcf3e4;color:#12683a;}
  .p-comp{background:#fde9d9;color:#9a4b12;}
  .stTabs [data-baseweb="tab"]{font-size:14px;}
</style>
""", unsafe_allow_html=True)


def cfg(key, env, default=""):
    return os.environ.get(env) or st.secrets.get(env, "") or db.get_setting(key, default)


# ─────────────────────────── Sidebar ───────────────────────────
with st.sidebar:
    st.markdown("### ◉ Content Watchdog")
    st.caption("Competitive report-page monitoring")
    st.divider()

    st.markdown("**Email alerts**")
    recips = st.text_area(
        "Recipients (comma separated)",
        value=cfg("recipients", "ALERT_RECIPIENTS", ""),
        height=68,
        placeholder="you@company.com, boss@company.com",
    )
    with st.expander("SMTP settings"):
        smtp_host = st.text_input("Host", value=cfg("smtp_host", "SMTP_HOST", "smtp.gmail.com"))
        smtp_port = st.number_input("Port", value=int(cfg("smtp_port", "SMTP_PORT", "587") or 587))
        smtp_user = st.text_input("Username", value=cfg("smtp_user", "SMTP_USER", ""))
        smtp_pass = st.text_input("App password", type="password",
                                  value=os.environ.get("SMTP_PASSWORD", ""))
        st.caption("Gmail requires a 16-character App Password, not your login password.")

    st.markdown("**Sensitivity**")
    threshold = st.slider("Ignore changes below (%)", 0.0, 10.0, 0.0, 0.1,
                          help="0 = alert on every single character change.")

    if st.button("Save settings", use_container_width=True):
        db.set_setting("recipients", recips)
        db.set_setting("smtp_host", smtp_host)
        db.set_setting("smtp_port", str(smtp_port))
        db.set_setting("smtp_user", smtp_user)
        db.set_setting("threshold", threshold)
        st.success("Saved")

    # Push into env so monitor/notifier pick them up in this process
    os.environ.setdefault("SMTP_HOST", smtp_host or "")
    os.environ.setdefault("SMTP_PORT", str(smtp_port))
    if smtp_user:
        os.environ["SMTP_USER"] = smtp_user
    if smtp_pass:
        os.environ["SMTP_PASSWORD"] = smtp_pass
    if recips:
        os.environ["ALERT_RECIPIENTS"] = recips

    st.divider()
    if st.button("▶  Run check now", type="primary", use_container_width=True):
        with st.spinner("Fetching pages… JS-heavy and protected sites take longer."):
            st.session_state.last_run = monitor.run(
                schedule_label="manual",
                send_mail=bool(recips and smtp_user),
                threshold=threshold,
                recipients=[r.strip() for r in recips.split(",") if r.strip()],
            )
        st.rerun()

    if "last_run" in st.session_state:
        r = st.session_state.last_run
        st.success(f"{r['checked']} checked · {r['changed']} changed"
                   + (" · emailed" if r["emailed"] else ""))
        if r["errors"]:
            st.warning(f"{r['errors']} fetch error(s)")


# ─────────────────────────── Header ───────────────────────────
pages = db.get_pages(active_only=False)
changes = db.recent_changes(200)
runs = db.recent_runs(10)

st.title("Content Watchdog")
st.caption("Tracks your report pages and their competitors. "
           "Emails you the exact wording that changed.")

groups = len({p["group_name"] for p in pages})
last_run = runs[0]["finished_at"] if runs and runs[0].get("finished_at") else "never"
st.markdown(f"""
<div class="metric-row">
  <div class="mcard"><div class="v">{len(pages)}</div><div class="l">URLs tracked</div></div>
  <div class="mcard"><div class="v">{groups}</div><div class="l">Reports</div></div>
  <div class="mcard"><div class="v">{len(changes)}</div><div class="l">Changes logged</div></div>
  <div class="mcard"><div class="v" style="font-size:15px;padding-top:7px;">{last_run}</div>
       <div class="l">Last run (UTC)</div></div>
</div>
""", unsafe_allow_html=True)

tab_imp, tab_urls, tab_chg, tab_sched, tab_logs = st.tabs(
    ["Import", "Tracked URLs", "Changes", "Schedule", "Run history"]
)

# ─────────────────────────── Import ───────────────────────────
with tab_imp:
    st.subheader("Import your Excel sheet")
    st.write("One row per report. Columns for your URL and each competitor URL. "
             "Column names are detected automatically.")

    c1, c2 = st.columns([2, 1])
    with c2:
        tpl = importer.sample_template()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            tpl.to_excel(w, index=False, sheet_name="URLs")
        st.download_button("⬇ Download template", buf.getvalue(),
                           "watchdog_template.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           use_container_width=True)
        st.dataframe(tpl.head(2), use_container_width=True, hide_index=True)

    with c1:
        up = st.file_uploader("Excel or CSV", type=["xlsx", "xls", "csv"])
        if up:
            try:
                df = importer.read_excel(up)
                st.dataframe(df.head(8), use_container_width=True)
                name_g, own_g, comps_g = importer.analyze(df)
                cols = list(df.columns)

                st.markdown("**Confirm column mapping**")
                m1, m2 = st.columns(2)
                with m1:
                    name_col = st.selectbox("Report name", cols,
                                            index=cols.index(name_g) if name_g in cols else 0)
                    own_col = st.selectbox("Our URL", cols,
                                           index=cols.index(own_g) if own_g in cols else 0)
                with m2:
                    comp_cols = st.multiselect("Competitor URLs", cols, default=comps_g)

                replace = st.checkbox("Replace existing list (clears current URLs)")
                if st.button("Import URLs", type="primary"):
                    n, sk = importer.import_df(df, name_col, own_col, comp_cols, replace)
                    st.success(f"Imported {n} URLs" + (f" · skipped {sk} invalid" if sk else ""))
                    st.info("Run a check now to capture baselines. "
                            "The first run never emails — it has nothing to compare to yet.")
            except Exception as e:
                st.error(f"Could not read file: {e}")

    st.divider()
    st.markdown("**Or add a single URL**")
    a1, a2, a3, a4 = st.columns([2, 3, 1.4, 1])
    with a1:
        g = st.text_input("Report name", key="man_g")
    with a2:
        u = st.text_input("URL", key="man_u")
    with a3:
        role = st.selectbox("Type", ["competitor", "own"], key="man_r")
    with a4:
        st.write("")
        if st.button("Add", use_container_width=True) and g and u:
            db.upsert_page(g, role, "Our page" if role == "own" else "Competitor", u)
            st.rerun()

# ─────────────────────────── Tracked URLs ───────────────────────────
with tab_urls:
    if not pages:
        st.info("Nothing tracked yet. Import a sheet on the Import tab.")
    else:
        for grp in sorted({p["group_name"] for p in pages}):
            rows = [p for p in pages if p["group_name"] == grp]
            with st.expander(f"{grp}  ·  {len(rows)} URLs", expanded=False):
                for p in sorted(rows, key=lambda x: (x["role"] != "own", x["label"] or "")):
                    c1, c2, c3, c4 = st.columns([1.3, 5.2, 1.2, 0.8])
                    cls = "p-own" if p["role"] == "own" else "p-comp"
                    with c1:
                        st.markdown(
                            f'<span class="pill {cls}">{p["label"] or p["role"]}</span>',
                            unsafe_allow_html=True)
                    with c2:
                        snap = db.latest_snapshot(p["id"])
                        meta = f" · {snap['word_count']} words · via {snap['meta'].get('tier','?')}" \
                            if snap else " · no baseline yet"
                        st.markdown(f"[{p['url'][:78]}]({p['url']})  \n"
                                    f"<span style='color:#7b8794;font-size:11px'>{meta}</span>",
                                    unsafe_allow_html=True)
                    with c3:
                        act = st.toggle("Active", value=bool(p["active"]), key=f"t{p['id']}")
                        if act != bool(p["active"]):
                            db.set_page_active(p["id"], act)
                            st.rerun()
                    with c4:
                        if st.button("✕", key=f"d{p['id']}"):
                            db.delete_page(p["id"])
                            st.rerun()

# ─────────────────────────── Changes ───────────────────────────
with tab_chg:
    if not changes:
        st.info("No changes detected yet.")
    else:
        f1, f2 = st.columns([2, 1])
        with f1:
            gsel = st.selectbox("Filter by report",
                                ["All"] + sorted({c["group_name"] for c in changes}))
        with f2:
            rsel = st.selectbox("Filter by type", ["All", "own", "competitor"])

        shown = [c for c in changes
                 if (gsel == "All" or c["group_name"] == gsel)
                 and (rsel == "All" or c["role"] == rsel)]

        for c in shown[:60]:
            head = (f"{c['detected_at']}  ·  {c['group_name']}  ·  "
                    f"{c['label']}  ·  {c['change_pct']}% changed")
            with st.expander(head):
                st.markdown(f"[{c['url']}]({c['url']})")
                st.caption(f"+{c['added']} added · −{c['removed']} removed · "
                           f"~{c['modified']} modified")
                full = db.change_detail(c["id"])
                d = full["detail"]
                view = {
                    "changed": True,
                    "added": d.get("added", []),
                    "removed": d.get("removed", []),
                    "modified": [tuple(m) for m in d.get("modified", [])],
                    "change_pct": c["change_pct"],
                }
                st.markdown(f"<style>{DIFF_CSS}</style>" + render_diff_html(view, 40),
                            unsafe_allow_html=True)
                with st.expander("Raw unified diff"):
                    st.code(d.get("unified", "")[:20000], language="diff")

# ─────────────────────────── Schedule ───────────────────────────
with tab_sched:
    st.subheader("Automated schedule")
    st.write("Checks run on GitHub Actions — free, and independent of this browser tab.")

    freq = st.radio("Frequency", ["Daily", "Twice daily", "Weekly", "Hourly", "Custom"],
                    horizontal=True)
    hour = st.slider("Hour to run (UTC)", 0, 23, 3)
    st.caption(f"IST is UTC+5:30 — {hour}:00 UTC is "
               f"{(hour + 5) % 24}:{'30'} IST.")

    cron = {
        "Daily": f"{0} {hour} * * *",
        "Twice daily": f"0 {hour},{(hour + 12) % 24} * * *",
        "Weekly": f"0 {hour} * * 1",
        "Hourly": "0 * * * *",
    }.get(freq)
    if freq == "Custom":
        cron = st.text_input("Cron expression", "0 3 * * *")

    st.code(f"""# .github/workflows/monitor.yml
on:
  schedule:
    - cron: '{cron}'
  workflow_dispatch:""", language="yaml")
    st.info("Edit `.github/workflows/monitor.yml` in your repo with the cron above, "
            "then commit. Actions picks it up automatically.")

# ─────────────────────────── Run history ───────────────────────────
with tab_logs:
    if not runs:
        st.info("No runs yet.")
    for r in runs:
        with st.expander(f"{r['started_at']} · {r.get('checked') or 0} checked · "
                         f"{r.get('changed') or 0} changed · {r.get('errors') or 0} errors"):
            st.code(r.get("log") or "(no log)")
