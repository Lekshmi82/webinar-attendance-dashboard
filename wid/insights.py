"""Evidence-based insights. Every statement cites the numbers it is based on."""
from __future__ import annotations

import pandas as pd

from .pipeline import MIN_SEGMENT


def _big(t: pd.DataFrame, min_n=MIN_SEGMENT):
    return t[(t["Registrations"] >= min_n)] if not t.empty else t


def generate(status: dict, src: pd.DataFrame, exp: pd.DataFrame, role: pd.DataFrame, aud: pd.DataFrame,
             stage: pd.DataFrame, cross_rate: pd.DataFrame, cross_n: pd.DataFrame) -> tuple[list, list]:
    """Returns (insights, recommendations) as lists of strings (markdown)."""
    ins, rec = [], []
    n = status["registrations"]
    if status["state"] == "Unavailable":
        ins.append(f"**Attendance cannot be verified.** {n:,} unique registrants, **0 confirmed attendees**, "
                   f"{status['no_shows']:,} recorded as 'No', {status['unknown']:,} with blank status. "
                   "A webinar with zero 'Yes' values almost always means the Zoom attendance report has not been "
                   "merged yet. The recorded 'No' values may be defaults, so treat them with caution.")
        rec.append("Upload the Zoom attendance/registrant report for this webinar. It needs a shared identifier: "
                   "UniqueLink / Submission ID / Record ID, or tick the hashed-email consent box. Then regenerate.")
        if not src.empty:
            top = src.sort_values("Registrations", ascending=False).iloc[0]
            ins.append(f"Registration mix: **{top['source']}** brought the most registrations "
                       f"({int(top['Registrations']):,}, {top['Registration share %']}%). " +
                       ", ".join(f"{r['source']} {r['Registration share %']}%" for _, r in src.iterrows()) + ".")
        if not exp.empty:
            e = exp.sort_values("Registrations", ascending=False).iloc[0]
            ins.append(f"Largest experience group: **{e['experience']}** ({int(e['Registrations']):,} registrants, "
                       f"{e['Registration share %']}%).")
        unk = src.loc[src["source"] == "Unknown", "Registrations"].sum() if not src.empty else 0
        if n and unk / n > 0.1:
            rec.append(f"{unk / n:.0%} of registrants have no UTM source. Tag every link (bio, WhatsApp, emails, "
                       "partners) so next week's channel analysis is complete.")
        return ins, rec

    overall = status["rate"]
    prov = " (provisional — some statuses unknown)" if status["state"] == "Provisional" else ""
    ins.append(f"Overall attendance: **{overall:.1f}%**{prov} — {status['attendees']:,} attendees of "
               f"{n:,} unique registrants.")

    s = src.set_index("source")
    bs = _big(src)
    if not src.empty and src["Attendees"].sum():
        most = src.sort_values("Attendees", ascending=False).iloc[0]
        ins.append(f"**{most['source']}** brings the most actual attendees: {int(most['Attendees']):,} "
                   f"({most['Attendee share %']}% of all attendees) from {most['Registration share %']}% "
                   "of registrations.")
    if {"Ads", "Organic"} <= set(s.index):
        a, o = s.loc["Ads"], s.loc["Organic"]
        ratio = o["Attendance %"] / a["Attendance %"] if a["Attendance %"] else float("nan")
        ins.append(f"Ads leads attend at **{a['Attendance %']}%** vs Organic **{o['Attendance %']}%** "
                   f"(Organic is {ratio:.1f}× Ads). Ads = {a['Registration share %']}% of registrations but "
                   f"{a['Attendee share %']}% of attendees.")
        if a["Attendance %"] < o["Attendance %"]:
            rec.append(f"Ads registrants show up less ({a['Attendance %']}% vs {o['Attendance %']}%). Add a "
                       "confirmation and reminder sequence for ads leads: WhatsApp at registration, 24h, 1h and "
                       "at start. Judge ad sets on attendees, not registrations.")
    if "Retargeting" in s.index:
        r = s.loc["Retargeting"]
        diff = r["Attendance %"] - overall
        ins.append(f"Retargeting attends at **{r['Attendance %']}%** ({'+' if diff >= 0 else ''}{diff:.1f} pts vs "
                   f"overall) from {int(r['Registrations']):,} registrants.")
        if diff > 0:
            rec.append(f"Retargeting (WhatsApp community / past leads) beats the average by {diff:.1f} pts. "
                       "Push re-registration messages to past registrants earlier and more often.")
    if len(bs) >= 2:
        lo = bs.sort_values("Attendance %").iloc[0]
        ins.append(f"Lowest-attending source (≥{MIN_SEGMENT} registrants): **{lo['source']}** at "
                   f"{lo['Attendance %']}% — {int(lo['No-shows']):,} no-shows.")

    for t, col, label in ((exp, "experience", "experience group"), (role, "role", "role"),
                          (stage, "career_stage", "career stage"), (aud, "audience", "audience")):
        bt = _big(t)
        bt = bt[bt[col] != "Unknown"] if not bt.empty else bt
        if len(bt) >= 2:
            hi, lo = bt.sort_values("Attendance %").iloc[-1], bt.sort_values("Attendance %").iloc[0]
            ins.append(f"By {label}: highest **{hi[col]}** {hi['Attendance %']}%, lowest **{lo[col]}** "
                       f"{lo['Attendance %']}% (n={int(lo['Registrations']):,}).")

    # Highest no-show group across all dimensions.
    cands = []
    for t, col in ((src, "source"), (exp, "experience"), (role, "role"), (aud, "audience")):
        bt = _big(t)
        if not bt.empty:
            bt = bt[bt[col] != "Unknown"]
            for r in bt.itertuples(index=False):
                cands.append((r[list(bt.columns).index("No-show %")], col, r[0], r[1]))
    if cands:
        ns, col, name, nreg = max(cands)
        ins.append(f"Highest no-show rate: **{name}** ({col}) — {ns}% of {int(nreg):,} registrants did not attend.")
        big_low = [c for c in cands if c[3] / n >= 0.15 and c[0] > (100 - overall)]
        for ns2, col2, name2, n2 in sorted(big_low, reverse=True)[:2]:
            rec.append(f"**{name2}** ({col2}) is {n2 / n:.0%} of registrations with a {ns2}% no-show rate. "
                       "Send them a reminder with the specific takeaway they'll get from the session, and "
                       "consider a slot that suits them better.")

    # Best source x experience cell.
    if not cross_rate.empty:
        stacked = cross_rate.stack().rename("rate").to_frame().join(cross_n.stack().rename("n"))
        stacked = stacked[(stacked["n"] >= MIN_SEGMENT)].drop(index="Unknown", level=0, errors="ignore")
        if not stacked.empty:
            (sname, ename), best = stacked["rate"].idxmax(), stacked["rate"].max()
            (wname, wename), worst = stacked["rate"].idxmin(), stacked["rate"].min()
            ins.append(f"Best source × experience combination: **{sname} · {ename}** at {best}% "
                       f"(n={int(stacked.loc[(sname, ename), 'n'])}). Weakest: **{wname} · {wename}** at {worst}% "
                       f"(n={int(stacked.loc[(wname, wename), 'n'])}).")
            rec.append(f"Shift targeting/creative toward the profile that attends: {sname} · {ename}. "
                       f"Review targeting for {wname} · {wename}.")

    unk = s["Registrations"].get("Unknown", 0)
    if n and unk / n > 0.1:
        rec.append(f"{unk / n:.0%} of registrants have no UTM source. Tag every shared link so the channel "
                   "comparison covers all registrants.")
    if status["state"] == "Provisional":
        rec.append(f"{status['unknown']:,} registrants have unknown status. Upload the full Zoom report "
                   f"to finalise. The true rate is between {overall:.1f}% and {status['rate_upper']:.1f}%.")
    return ins, rec
