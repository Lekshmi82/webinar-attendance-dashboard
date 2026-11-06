"""100xEngineers — Webinar Attendance Intelligence Dashboard.  Run: streamlit run app.py"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from wid import classify as clf
from wid import insights as ins_mod
from wid import pipeline as pl
from wid import report

st.set_page_config(page_title="Webinar Attendance Intelligence", page_icon="📊", layout="wide")

SRC_COLORS = {"Ads": "#2563EB", "Organic": "#16A34A", "Retargeting": "#9333EA", "Others": "#F59E0B",
              "Unknown": "#9CA3AF"}
STATUS_COLORS = {pl.ATTENDED: "#16A34A", pl.NOSHOW: "#DC2626", pl.UNKNOWN: "#9CA3AF"}
ALL = "__ALL__"

st.markdown("""
<style>
div[data-testid="stMetric"] {background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:12px 16px;color:#0F172A;}
.block-container {padding-top:1.5rem;}
</style>""", unsafe_allow_html=True)

if "nonce" not in st.session_state:
    st.session_state.nonce = 0
ss = st.session_state


@st.cache_data(show_spinner=False, max_entries=4)
def _prep(name, data, consent):
    return pl.prepare_registrations(name, data, consent)


def reset():
    n = ss.nonce + 1
    ss.clear()
    st.cache_data.clear()
    ss.nonce = n


# ---------------- Sidebar: weekly workflow ----------------
with st.sidebar:
    st.header("Weekly workflow")
    reg_file = st.file_uploader("1 · Registration / source file", type=["csv", "xlsx"], key=f"reg{ss.nonce}")
    att_file = st.file_uploader("2 · Attendance file (optional)", type=["csv", "xlsx"], key=f"att{ss.nonce}",
                                help="Zoom attendee/registrant report or a sheet with an 'Attended' column. "
                                     "Matched on UniqueLink / Submission ID / Record ID.")
    consent = st.checkbox("Allow matching on hashed email", value=False, key=f"consent{ss.nonce}",
                          help="Off by default. If on, emails are SHA-256 hashed in memory and only the hash is "
                               "used for matching. Raw emails are never displayed, stored or exported.")
    prep = None
    if reg_file is not None:
        try:
            prep = _prep(reg_file.name, reg_file.getvalue(), consent)
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not read registration file: {e}")
        if prep is not None and prep.errors:
            for e in prep.errors:
                st.error(e)
            prep = None
    webinar = None
    if prep is not None:
        opts = pl.webinar_options(prep)
        labels = {ALL: f"All webinars in file ({len(opts)})"}
        labels.update({r.webinar: f"{r.webinar} — {r.title} ({r.records:,})" for r in opts.itertuples()})
        keys = list(opts.webinar) + ([ALL] if len(opts) > 1 else [])
        webinar = st.selectbox("3 · Webinar", keys, format_func=labels.get, key=f"web{ss.nonce}")
    go_btn = st.button("4 · Generate Analytics", type="primary", use_container_width=True,
                       disabled=prep is None)
    st.button("Reset", on_click=reset, use_container_width=True)
    st.caption("Uploads are processed in memory only. Personal columns (name, email, phone) are dropped on load.")

st.title("Webinar Attendance Intelligence")
st.caption("100xEngineers · goal: increase actual webinar attendees")

if go_btn and prep is not None:
    att, info = None, None
    if att_file is not None:
        try:
            att, info = pl.load_attendance(att_file.name, att_file.getvalue(), prep, webinar, consent)
        except Exception as e:  # noqa: BLE001
            info = pl.AttendanceInfo(provided=True, note=f"Could not read attendance file: {e}")
    ss.result = {"webinar": webinar, "att": att, "info": info, "reg_name": reg_file.name,
                 "att_name": att_file.name if att_file else None}
    ss.result_sig = (reg_file.name, reg_file.size, consent)

sig = (reg_file.name, reg_file.size, consent) if reg_file is not None else None
if "result" not in ss or prep is None or ss.get("result_sig") != sig:
    if prep is not None:
        for w in prep.warnings:
            st.warning(w)
    st.info("Upload the registration file, optionally the attendance file, choose the webinar and click "
            "**Generate Analytics**.")
    with st.expander("Source classification rules"):
        st.dataframe(clf.rules_table(), hide_index=True, use_container_width=True)
    st.stop()

R = ss.result
webinar, info = R["webinar"], R["info"]
people_all = pl.build_people(prep, webinar, R["att"])

# Multi-webinar view: webinars without verified attendance are excluded from rate calculations.
per_web = None
excluded = []
if webinar == ALL:
    rows = []
    for w, g in people_all.groupby("webinar"):
        s = pl.attendance_status(g)
        rows.append({"Webinar": w, "Title": g["webinar_title"].dropna().iloc[0] if g["webinar_title"].notna().any()
                     else "", "Registrations": s["registrations"], "Attendees": s["attendees"],
                     "No-shows": s["no_shows"], "Unknown": s["unknown"],
                     "Attendance %": round(s["rate"], 1) if s["rate"] is not None else None, "Status": s["state"]})
    per_web = pd.DataFrame(rows)
    excluded = per_web.loc[per_web["Status"] == "Unavailable", "Webinar"].tolist()

# Source filter
with st.sidebar:
    st.divider()
    present = [s for s in clf.SOURCE_ORDER if s in set(people_all["source"])]
    src_filter = st.multiselect("Filter: acquisition source", present, default=present, key=f"src{ss.nonce}")
people = people_all[people_all["source"].isin(src_filter)]
if excluded:
    people = people[~people["webinar"].isin(excluded)]

status = pl.attendance_status(people)
ok = status["state"] != "Unavailable"

# ---------------- Status banner + KPIs ----------------
title = (prep.df.loc[prep.df["webinar"] == webinar, "webinar_title"].dropna().mode().iat[0]
         if webinar != ALL and prep.df.loc[prep.df["webinar"] == webinar, "webinar_title"].notna().any()
         else ("All webinars" if webinar == ALL else webinar))
st.subheader(f"{webinar if webinar != ALL else ''} {title}".strip())
if status["state"] == "Final":
    st.success("Attendance status: **Final**. Every registrant has a confirmed Attended / No-show status.")
elif status["state"] == "Provisional":
    st.warning(f"Attendance status: **Provisional**. {status['unknown']:,} registrants have unknown status. "
               f"The true rate is between **{status['rate']:.1f}%** and **{status['rate_upper']:.1f}%**.")
else:
    st.error(f"Attendance status: **Unavailable**. 0 confirmed attendees among {status['registrations']:,} "
             f"registrants ({status['no_shows']:,} recorded 'No', {status['unknown']:,} blank). "
             "Attendance has not been verified, so attendance % is not shown. Registration analytics are still valid.")
if info and info.provided:
    (st.info if info.reliable else st.warning)(f"Attendance file: {info.note}")
if excluded:
    st.warning(f"Excluded from combined rates (attendance unavailable): {', '.join(excluded)}")

k = st.columns(5)
k[0].metric("Unique registrations", f"{status['registrations']:,}")
k[1].metric("Unique attendees", f"{status['attendees']:,}" if ok else "Unverified")
k[2].metric("No-shows" if ok else "Recorded 'No'", f"{status['no_shows']:,}",
            help="Registrants confirmed as not attending")
k[3].metric("Unknown status", f"{status['unknown']:,}")
k[4].metric("Attendance %", f"{status['rate']:.1f}%" if ok else "N/A",
            delta=("provisional" if status["state"] == "Provisional" else None), delta_color="off",
            help="Unique attendees ÷ unique registrations × 100")

# ---------------- Tables ----------------
src = pl.segment(people, "source", clf.SOURCE_ORDER, ok)
exp = pl.segment(people, "experience", pl.EXP_ORDER, ok)
stage = pl.segment(people, "career_stage", pl.STAGE_ORDER, ok)
role = pl.segment(people, "role", None, ok).sort_values("Registrations", ascending=False)
aud = pl.segment(people, "audience", ["Working professional", "Student", "Unknown"], ok)
intl = pl.segment(people, "international", None, ok) if "international" in people else pd.DataFrame()
city = pl.segment(people, "city", None, ok) if "city" in people else pd.DataFrame()
detail = pl.segment(people, "source_detail", None, ok).sort_values("Registrations", ascending=False)
detail = detail.merge(people.groupby("source_detail")["source"].agg(lambda s: s.mode().iat[0]).reset_index(),
                      on="source_detail")
cn_exp, cr_exp = pl.cross(people, "source", "experience", ok, pl.EXP_ORDER)
cn_stage, cr_stage = pl.cross(people, "source", "career_stage", ok, pl.STAGE_ORDER)
cn_role, cr_role = pl.cross(people, "source", "role", ok)
cn_aud, cr_aud = pl.cross(people, "source", "audience", ok)
insights, recs = ins_mod.generate(status, src, exp, role, aud, stage, cr_exp, cn_exp)
dq = pl.data_quality(prep, people_all, webinar, info)


def fmt(df):
    return df.style.format({c: "{:.1f}" for c in df.columns if "%" in c}, na_rep="n/a")


def bar_compare(df, col, title_, color_map=None, horizontal=False):
    """Registrations vs attendees grouped bar + attendance % line."""
    if df.empty:
        return None
    fig = go.Figure()
    fig.add_bar(x=df[col], y=df["Registrations"], name="Registrations", marker_color="#93C5FD")
    if ok:
        fig.add_bar(x=df[col], y=df["Attendees"], name="Attendees", marker_color="#1D4ED8")
        fig.add_scatter(x=df[col], y=df["Attendance %"], name="Attendance %", yaxis="y2", mode="lines+markers+text",
                        text=[f"{v:.1f}%" for v in df["Attendance %"]], textposition="top center",
                        line=dict(color="#DC2626"))
    fig.update_layout(title=title_, barmode="group", height=380, margin=dict(t=50, b=10, l=10, r=10),
                      yaxis=dict(title="People"), legend=dict(orientation="h", y=-0.2),
                      yaxis2=dict(title="Attendance %", overlaying="y", side="right", rangemode="tozero",
                                  showgrid=False))
    return fig


def heatmap(n, r, title_):
    if n.empty:
        return None
    z = r if ok else n
    masked = z.where(n >= 20) if ok else z
    text = [[(f"{z.iloc[i, j]:.0f}%<br>n={n.iloc[i, j]}" if ok and n.iloc[i, j] >= 20 else
              (f"n={n.iloc[i, j]}" if not ok else f"n={n.iloc[i, j]} (small)"))
             for j in range(n.shape[1])] for i in range(n.shape[0])]
    fig = go.Figure(go.Heatmap(z=masked.values, x=list(n.columns), y=list(n.index), text=text,
                               texttemplate="%{text}", colorscale="Blues", hoverongaps=False,
                               colorbar=dict(title="Attend %" if ok else "Regs")))
    fig.update_layout(title=title_ + ("" if ok else " — registrations (attendance unavailable)"),
                      height=360, margin=dict(t=50, b=10, l=10, r=10))
    return fig


tabs = st.tabs(["Attendee report", "Overview", "Acquisition", "Demographics", "Work experience", "Deep analysis",
                "Insights", "Data quality"])
_rep_src = [x for x in ("Ads", "Organic", "Retargeting", "Others") if x in set(people["source"])]
rep_exp = pl.source_breakdown(people, "experience", _rep_src, pl.EXP_ORDER, ok)
rep_stage = pl.source_breakdown(people, "career_stage", _rep_src, pl.STAGE_ORDER, ok)
rep_aud = pl.source_breakdown(people, "audience", _rep_src, ["Working professional", "Student", "Unknown"], ok)
rep_role = pl.source_breakdown(people, "role", _rep_src, list(role["role"]), ok)
rep_intl = pl.source_breakdown(people, "international", _rep_src, None, ok) if "international" in people else pd.DataFrame()

REPORT_SOURCES = ["Ads", "Organic", "Others"]
REP_COLORS = {"Ads": "#2563EB", "Organic": "#16A34A", "Others": "#F59E0B", "Retargeting": "#9333EA"}


def three_way(df, split_retarget):
    d = df[df["source"] != "Unknown"].copy()
    if not split_retarget:
        d["source"] = d["source"].replace({"Retargeting": "Others"})
    return d


def by_source(d, order):
    g = d.groupby("source").agg(Registered=("status", "size"),
                                Attended=("status", lambda s: int((s == pl.ATTENDED).sum())))
    g = g.reindex([o for o in order if o in g.index])
    g["Attendance %"] = (g["Attended"] / g["Registered"] * 100).round(1)
    return g.reset_index()


def by_group(d, dim, order, min_n=10):
    g = d.groupby([dim, "source"]).agg(Registered=("status", "size"),
                                       Attended=("status", lambda s: int((s == pl.ATTENDED).sum()))).reset_index()
    g = g[(g[dim] != "Unknown") & (g["Registered"] >= min_n)]
    g["Attendance %"] = (g["Attended"] / g["Registered"] * 100).round(1)
    if order:
        g[dim] = pd.Categorical(g[dim], [o for o in order if o in set(g[dim])], ordered=True)
        g = g.sort_values(dim)
    return g


with tabs[0]:
    split = st.toggle("Show Retargeting separately (otherwise it is counted in Others)", value=False)
    order = ["Ads", "Organic", "Retargeting", "Others"] if split else REPORT_SOURCES
    d = three_way(people, split)
    t = by_source(d, order)
    if not ok:
        st.error("Attendance is not verified for this webinar, so only registrations are shown.")

    # 1. Headline numbers
    c = st.columns(len(t))
    for col, r in zip(c, t.itertuples()):
        col.metric(f"{r.source} attendance", f"{r._4:.1f}%" if ok else "N/A",
                   f"{r.Attended:,} of {r.Registered:,} attended" if ok else f"{r.Registered:,} registered",
                   delta_color="off")

    # 2. One chart: registered vs attended per source
    fig = go.Figure()
    fig.add_bar(x=t["source"], y=t["Registered"], name="Registered", marker_color="#CBD5E1",
                text=t["Registered"].map("{:,}".format), textposition="outside")
    if ok:
        fig.add_bar(x=t["source"], y=t["Attended"], name="Attended",
                    marker_color=[REP_COLORS[s] for s in t["source"]],
                    text=[f"{a:,} ({p:.0f}%)" for a, p in zip(t["Attended"], t["Attendance %"])],
                    textposition="outside")
    fig.update_layout(title="Registered vs attended by source", barmode="group", height=380,
                      margin=dict(t=50, b=10, l=10, r=10), yaxis_title="People", legend_title="",
                      legend=dict(orientation="h", y=1.12, x=1, xanchor="right"))
    st.plotly_chart(fig, use_container_width=True)

    # 3. One chart: attendance % by demographic, per source
    dims = {"Work experience": ("career_stage", pl.STAGE_ORDER),
            "Student vs working professional": ("audience", ["Student", "Working professional"]),
            "Current role": ("role", None)}
    pick = st.radio("Break down by", list(dims), horizontal=True)
    dim, dorder = dims[pick]
    g = by_group(d, dim, dorder)
    ycol = "Attendance %" if ok else "Registered"
    fig = px.bar(g, x=dim, y=ycol, color="source", barmode="group", color_discrete_map=REP_COLORS,
                 category_orders={"source": order}, text=ycol,
                 hover_data={"Registered": ":,", "Attended": ":,"})
    fig.update_traces(texttemplate="%{text:.0f}%" if ok else "%{text:,}", textposition="outside")
    fig.update_layout(title=f"{'Attendance %' if ok else 'Registrations'} by {pick.lower()}", height=420,
                      margin=dict(t=50, b=10, l=10, r=10), xaxis_title="", legend_title="",
                      legend=dict(orientation="h", y=1.12, x=1, xanchor="right"))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Groups with fewer than 10 registrants are hidden. Hover a bar to see registered and attended counts.")

    with st.expander("See table"):
        wide = g.pivot_table(index=dim, columns="source", values=["Registered", "Attended", "Attendance %"],
                             observed=True)
        wide.columns = [f"{s} · {m}" for m, s in wide.columns]
        wide = wide[[f"{s} · {m}" for s in order for m in ("Registered", "Attended", "Attendance %")
                     if f"{s} · {m}" in wide.columns]]
        st.dataframe(t, hide_index=True, use_container_width=True)
        st.dataframe(wide, use_container_width=True)


with tabs[1]:
    c1, c2 = st.columns([1, 1])
    sc = people["status"].value_counts().reindex([pl.ATTENDED, pl.NOSHOW, pl.UNKNOWN]).fillna(0)
    fig = px.pie(values=sc.values, names=sc.index, hole=0.55, color=sc.index, color_discrete_map=STATUS_COLORS,
                 title="Attendance status of unique registrants")
    fig.update_layout(height=360, margin=dict(t=50, b=10))
    c1.plotly_chart(fig, use_container_width=True)
    fig = bar_compare(src, "source", "Registrations vs attendees by source" if ok else "Registrations by source")
    c2.plotly_chart(fig, use_container_width=True)
    if insights:
        st.markdown("**Key findings**")
        for i in insights[:4]:
            st.markdown(f"- {i}")
    if per_web is not None:
        st.markdown("**Per-webinar summary**")
        st.dataframe(per_web, hide_index=True, use_container_width=True)
        pw = per_web.dropna(subset=["Attendance %"])
        if len(pw) > 1:
            fig = px.bar(pw, x="Webinar", y="Attendance %", text="Attendance %", title="Attendance % by webinar",
                         hover_data=["Title", "Registrations"])
            fig.update_layout(height=360)
            st.plotly_chart(fig, use_container_width=True)

with tabs[2]:
    st.dataframe(fmt(src), hide_index=True, use_container_width=True)
    c1, c2 = st.columns(2)
    if ok and src["Attendees"].sum():
        share = src.melt(id_vars="source", value_vars=["Registration share %", "Attendee share %"])
        fig = px.bar(share, x="source", y="value", color="variable", barmode="group", text="value",
                     title="Registration share vs attendee share",
                     color_discrete_sequence=["#93C5FD", "#1D4ED8"])
        fig.update_layout(height=380, yaxis_title="%", legend_title="")
        c1.plotly_chart(fig, use_container_width=True)
        fig = px.bar(src, x="source", y="Attendance %", color="source", color_discrete_map=SRC_COLORS,
                     text="Attendance %", title="Attendance % by source")
        fig.add_hline(y=status["rate"], line_dash="dash", annotation_text=f"overall {status['rate']:.1f}%")
        fig.update_layout(height=380, showlegend=False)
        c2.plotly_chart(fig, use_container_width=True)
    else:
        fig = px.pie(src, values="Registrations", names="source", color="source", color_discrete_map=SRC_COLORS,
                     title="Registration share by source", hole=0.5)
        c1.plotly_chart(fig, use_container_width=True)
    st.markdown("**Raw utm_source breakdown** (to audit the classification)")
    st.dataframe(fmt(detail[["source_detail", "source"] + [c for c in detail.columns
                                                            if c not in ("source_detail", "source")]]),
                 hide_index=True, use_container_width=True)
    with st.expander("Classification rules (applied in order, first match wins)"):
        st.dataframe(clf.rules_table(), hide_index=True, use_container_width=True)

with tabs[3]:
    c1, c2 = st.columns(2)
    c1.plotly_chart(bar_compare(aud, "audience", "Student vs working professional"), use_container_width=True)
    c2.plotly_chart(bar_compare(role, "role", "By current role"), use_container_width=True)
    st.dataframe(fmt(aud), hide_index=True, use_container_width=True)
    st.dataframe(fmt(role), hide_index=True, use_container_width=True)
    if not intl.empty:
        st.markdown("**International vs India leads**")
        st.dataframe(fmt(intl), hide_index=True, use_container_width=True)
    if not city.empty:
        st.plotly_chart(bar_compare(city.head(15), "city", "Top cities"), use_container_width=True)
    else:
        st.caption("City data is not available in this file, so city-wise analysis is skipped.")

with tabs[4]:
    c1, c2 = st.columns(2)
    c1.plotly_chart(bar_compare(exp, "experience", "By work experience (as recorded)"), use_container_width=True)
    c2.plotly_chart(bar_compare(stage, "career_stage", "By career stage (harmonised)"), use_container_width=True)
    st.dataframe(fmt(exp), hide_index=True, use_container_width=True)
    st.dataframe(fmt(stage), hide_index=True, use_container_width=True)
    st.caption("Form options changed over time ('0-2 years' vs '<1 years' + '1-2 years'). The career-stage view "
               "groups them so webinars can be compared.")

with tabs[5]:
    c1, c2 = st.columns(2)
    c1.plotly_chart(heatmap(cn_exp, cr_exp, "Source × experience"), use_container_width=True)
    c2.plotly_chart(heatmap(cn_aud, cr_aud, "Source × audience"), use_container_width=True)
    st.plotly_chart(heatmap(cn_role, cr_role, "Source × role"), use_container_width=True)
    st.caption("Cells with fewer than 20 registrants are blanked so small samples don't mislead.")
    if ok:
        c1, c2 = st.columns(2)
        top = src.sort_values("Attendees", ascending=False).iloc[0]
        c1.metric("Source with most actual attendees", top["source"], f"{int(top['Attendees']):,} attendees",
                  delta_color="off")
        groups = []
        for t, col in ((src, "source"), (exp, "experience"), (role, "role"), (aud, "audience"),
                       (stage, "career_stage")):
            b = t[(t["Registrations"] >= pl.MIN_SEGMENT) & (t[col] != "Unknown")]
            groups += [(r["No-show %"], f"{r[col]} ({col})", r["Registrations"]) for _, r in b.iterrows()]
        if groups:
            ns, g, n = max(groups)
            c2.metric("Highest no-show rate", g, f"{ns:.1f}% of {int(n):,}", delta_color="off")
        ns_tbl = pd.DataFrame(sorted(groups, reverse=True), columns=["No-show %", "Group", "Registrations"])
        st.markdown("**No-show ranking (groups with ≥30 registrants)**")
        st.dataframe(ns_tbl.head(15), hide_index=True, use_container_width=True)

with tabs[6]:
    st.markdown("### Insights")
    for i in insights:
        st.markdown(f"- {i}")
    st.markdown("### Recommendations to increase attendance")
    for r in recs:
        st.markdown(f"- {r}")
    st.caption(f"Insights only use segments with ≥{pl.MIN_SEGMENT} registrants. Every figure comes from the uploaded data.")

with tabs[7]:
    st.dataframe(dq, hide_index=True, use_container_width=True)
    st.markdown("**Matching strategy**: registrants are de-duplicated within each webinar by UniqueLink "
                "(then Submission ID, then Record ID). Attendance files are matched on the same non-personal IDs. "
                "Hashed email is used only if you tick the consent box. If fewer than 50% of attendance rows "
                "match, the merge is rejected rather than guessed.")
    for w in prep.warnings:
        st.warning(w)

# ---------------- Export ----------------
meta = {"Report generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "Registration file": R["reg_name"],
        "Attendance file": R["att_name"] or "none", "Webinar": webinar if webinar != ALL else "All webinars",
        "Title": title, "Source filter": ", ".join(src_filter), "Attendance status": status["state"],
        "Unique registrations": status["registrations"],
        "Unique attendees": status["attendees"] if ok else "Unverified",
        "No-shows": status["no_shows"], "Unknown status": status["unknown"],
        "Attendance %": round(status["rate"], 1) if ok else "Unavailable",
        "Attendance % upper bound (if all unknown attended)": round(status["rate_upper"], 1)
        if status["rate_upper"] is not None else "n/a",
        "Excluded webinars (no verified attendance)": ", ".join(excluded) or "none"}
sheets = {"Attendee report": src, "Experience x source": rep_exp, "Career stage x source": rep_stage,
          "Audience x source": rep_aud, "Role x source": rep_role, "International x source": rep_intl,
          "Acquisition": src, "UTM source detail": detail, "Experience": exp, "Career stage": stage,
          "Role": role, "Audience": aud, "International": intl, "City": city,
          "Src x Exp regs": cn_exp, "Src x Exp attend%": cr_exp if ok else None,
          "Src x Stage regs": cn_stage, "Src x Stage attend%": cr_stage if ok else None,
          "Src x Role regs": cn_role, "Src x Role attend%": cr_role if ok else None,
          "Per webinar": per_web, "Data quality": dq, "Classification rules": clf.rules_table()}
xlsx = report.build_excel(meta, sheets, insights, recs)
fname = f"attendance_report_{(webinar if webinar != ALL else 'all')}_{dt.date.today()}.xlsx"
st.sidebar.divider()
st.sidebar.download_button("⬇ Download Excel report", xlsx, file_name=fname, use_container_width=True,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
