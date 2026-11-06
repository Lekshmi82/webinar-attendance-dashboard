"""Core processing: registrations -> de-duplicated people per webinar -> attendance status -> metrics."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import classify as clf
from . import loader

ATTENDED, NOSHOW, UNKNOWN = "Attended", "No-show", "Unknown"
ID_PRIORITY = ["unique_link", "submission_id", "record_id"]
MATCH_KEYS = ["unique_link", "submission_id", "record_id", "_email_hash"]
RELIABLE_MATCH_SHARE = 0.5   # share of attendance rows that must match registrations
MIN_SEGMENT = 30             # minimum registrations before a segment is used in insights

EXP_ORDER = ["Student", "<1 years", "0-2 years", "1-2 years", "2-4 years", "4-8 years", "8+ years", "Unknown"]
STAGE_MAP = {"student": "Student / Fresher", "<1 years": "Early career (0–2 yrs)", "0-2 years": "Early career (0–2 yrs)",
             "1-2 years": "Early career (0–2 yrs)", "2-4 years": "Mid career (2–8 yrs)",
             "4-8 years": "Mid career (2–8 yrs)", "8+ years": "Senior (8+ yrs)"}
STAGE_ORDER = ["Student / Fresher", "Early career (0–2 yrs)", "Mid career (2–8 yrs)", "Senior (8+ yrs)", "Unknown"]
TEST_RE = re.compile(r"test lead|dummy data", re.I)


@dataclass
class Prepared:
    df: pd.DataFrame                  # one row per registration record (PII removed)
    cols: dict
    dropped_pii: list
    error_cells: int
    test_rows: int
    empty_rows: int
    untagged_rows: int
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _webinar_key(df, cols):
    tag = df[cols["tag"]] if "tag" in cols else pd.Series(pd.NA, index=df.index, dtype="string")
    title = df[cols["title"]] if "title" in cols else pd.Series(pd.NA, index=df.index, dtype="string")
    key = tag.copy()
    untagged = key.isna() & title.notna()
    key = key.where(~untagged, "untagged: " + title.astype("string"))
    key = key.fillna("(no webinar label)")
    return key, title, int(untagged.sum())


def prepare_registrations(name: str, data: bytes, allow_email_hash=False) -> Prepared:
    raw = loader.read_any(name, data)
    df, dropped, _ = loader.scrub(raw, allow_email_hash)
    df, err = loader.clean_values(df)
    cols = loader.map_columns(df)
    errors, warnings = loader.validate_registration(cols)
    if errors:
        return Prepared(df, cols, dropped, err, 0, 0, 0, warnings, errors)

    # Drop rows that carry no registration at all (blank spacer rows).
    flag_cols = [c for c in df.columns if re.fullmatch(r"c\d+_web\w*", c, re.I)]
    id_cols = [cols[k] for k in ID_PRIORITY if k in cols]
    if flag_cols:
        is_reg = df[flag_cols].apply(lambda s: s.str.lower().eq("y")).any(axis=1)
    else:
        is_reg = df[id_cols].notna().any(axis=1)
    empty_rows = int((~is_reg).sum())
    df = df[is_reg].copy()

    # Exclude explicit test/dummy records.
    is_test = df.apply(lambda s: s.str.contains(TEST_RE, na=False)).any(axis=1)
    test_rows = int(is_test.sum())
    df = df[~is_test].copy()

    df["webinar"], df["webinar_title"], untagged = _webinar_key(df, cols)
    df = clf.classify(df, cols)

    # Person identifier inside a webinar (non-personal IDs only, plus hashed email if consented).
    df["person_id"] = pd.NA
    df["id_basis"] = "none"
    for k in ID_PRIORITY + (["_email_hash"] if "_email_hash" in df.columns else []):
        c = cols.get(k, k)
        if c in df.columns:
            fill = df["person_id"].isna() & df[c].notna()
            df.loc[fill, "person_id"] = k + ":" + df.loc[fill, c].astype(str)
            df.loc[fill, "id_basis"] = k
    noid = df["person_id"].isna()
    df.loc[noid, "person_id"] = "row:" + df.index[noid].astype(str)

    # Attendance as recorded in the registration file.
    df["status_reg"] = UNKNOWN
    if "attended" in cols:
        a = df[cols["attended"]].str.lower()
        df.loc[a.isin(["yes", "y", "true", "1", "attended"]), "status_reg"] = ATTENDED
        df.loc[a.isin(["no", "n", "false", "0", "not attended"]), "status_reg"] = NOSHOW
    if "time_in_session" in cols:
        t = pd.to_numeric(df[cols["time_in_session"]], errors="coerce")
        df.loc[(df["status_reg"] == UNKNOWN) & (t > 0), "status_reg"] = ATTENDED

    exp = df[cols["experience"]] if "experience" in cols else pd.Series(pd.NA, index=df.index)
    df["experience"] = exp.fillna("Unknown")
    df["career_stage"] = exp.str.lower().map(STAGE_MAP).fillna("Unknown")
    role = df[cols["role"]] if "role" in cols else pd.Series(pd.NA, index=df.index)
    df["role"] = role.replace({"Other": "Others"}).fillna("Unknown")
    is_student = df["experience"].str.lower().eq("student") | df["role"].str.lower().eq("student")
    df["audience"] = np.where(is_student, "Student",
                              np.where(df["experience"].eq("Unknown") & df["role"].eq("Unknown"), "Unknown",
                                       "Working professional"))
    for k, label in (("international", "international"), ("city", "city"), ("country", "country")):
        if k in cols:
            df[label] = df[cols[k]].fillna("Unknown")
    if "submitted_at" in cols:
        df["submitted_at"] = pd.to_datetime(df[cols["submitted_at"]], errors="coerce", utc=True)
    return Prepared(df, cols, dropped, err, test_rows, empty_rows, untagged, warnings, errors)


def webinar_options(prep: Prepared) -> pd.DataFrame:
    g = prep.df.groupby("webinar").agg(title=("webinar_title", lambda s: s.dropna().mode().iat[0]
                                              if s.notna().any() else ""), records=("webinar", "size"))
    return g.reset_index().sort_values("webinar")


@dataclass
class AttendanceInfo:
    provided: bool = False
    rows: int = 0
    rows_for_webinar: int = 0
    key: str | None = None
    matched_rows: int = 0
    unmatched_rows: int = 0
    reliable: bool = False
    note: str = ""
    dropped_pii: list = field(default_factory=list)


def load_attendance(name, data, reg: Prepared, webinar: str, allow_email_hash=False):
    """Load an attendance file and return (per-key attendance status Series, AttendanceInfo)."""
    info = AttendanceInfo(provided=True)
    raw = loader.read_any(name, data)
    df, dropped, _ = loader.scrub(raw, allow_email_hash)
    info.dropped_pii = dropped
    df, _ = loader.clean_values(df)
    df = df.dropna(how="all")
    cols = loader.map_columns(df)
    info.rows = len(df)

    # If the attendance file has webinar labels, keep only the selected webinar's rows.
    if webinar != "__ALL__" and ("tag" in cols or "title" in cols):
        sel = reg.df[reg.df["webinar"] == webinar]
        titles = set(sel["webinar_title"].dropna().str.lower())
        mask = pd.Series(False, index=df.index)
        if "tag" in cols:
            mask |= df[cols["tag"]].str.lower().eq(webinar.lower())
        if "title" in cols and "tag" not in cols:
            mask |= df[cols["title"]].str.lower().isin(titles)
        df = df[mask]
    info.rows_for_webinar = len(df)
    if df.empty:
        info.note = "The attendance file contains no rows for the selected webinar, so attendance cannot be verified from it."
        return None, info

    # Status per attendance row.
    status = pd.Series(ATTENDED, index=df.index)  # attendee list: presence means attended
    if "attended" in cols:
        a = df[cols["attended"]].str.lower()
        status = np.where(a.isin(["yes", "y", "true", "1"]), ATTENDED,
                          np.where(a.isin(["no", "n", "false", "0"]), NOSHOW, UNKNOWN))
        status = pd.Series(status, index=df.index)
    if "time_in_session" in cols:
        t = pd.to_numeric(df[cols["time_in_session"]], errors="coerce")
        status = status.where(~(t > 0), ATTENDED)

    # Pick the shared key with the most matches.
    reg_df = reg.df if webinar == "__ALL__" else reg.df[reg.df["webinar"] == webinar]
    best = None
    for k in MATCH_KEYS:
        ac = cols.get(k, k if k in df.columns else None)
        rc = reg.cols.get(k, k if k in reg_df.columns else None)
        if ac is None or rc is None or ac not in df.columns or rc not in reg_df.columns:
            continue
        keys = df[ac].dropna()
        hits = int(keys.isin(set(reg_df[rc].dropna())).sum())
        if best is None or hits > best[1]:
            best = (k, hits, ac, rc)
    if best is None:
        info.note = ("No shared identifier between the files (UniqueLink, Submission ID, Record ID, or consented "
                     "hashed email). Attendance was NOT merged.")
        return None, info
    k, hits, ac, rc = best
    info.key, info.matched_rows = k, hits
    info.unmatched_rows = int(df[ac].notna().sum() - hits)
    share = hits / max(df[ac].notna().sum(), 1)
    info.reliable = hits > 0 and share >= RELIABLE_MATCH_SHARE
    if not info.reliable:
        info.note = (f"Only {hits} of {df[ac].notna().sum()} attendance rows matched on '{k}' "
                     f"({share:.0%}). Matching is unreliable, so attendance was NOT merged.")
        return None, info
    per_key = (pd.DataFrame({"key": df[ac].values, "s": status.values}).dropna(subset=["key"])
               .groupby("key")["s"].agg(lambda s: ATTENDED if (s == ATTENDED).any()
                                        else (NOSHOW if (s == NOSHOW).any() else UNKNOWN)))
    info.note = f"Matched {hits} attendance rows on '{k}' ({share:.0%})."
    return (k, rc, per_key), info


def build_people(reg: Prepared, webinar: str, att=None) -> pd.DataFrame:
    """One row per unique registrant per webinar with final attendance status."""
    df = reg.df if webinar == "__ALL__" else reg.df[reg.df["webinar"] == webinar]
    df = df.copy()
    df["status"] = df["status_reg"]
    if att is not None:
        k, rc, per_key = att
        m = df[rc].map(per_key)
        # Attendance file reliable: matched -> its status; unmatched registrants -> No-show
        # unless the registration file itself says Attended.
        df["status"] = np.where(m.notna(), m, np.where(df["status_reg"] == ATTENDED, ATTENDED, NOSHOW))
    if "submitted_at" in df.columns:
        df = df.sort_values("submitted_at")
    rank = {ATTENDED: 0, NOSHOW: 1, UNKNOWN: 2}
    df["_r"] = df["status"].map(rank)
    best_status = df.groupby(["webinar", "person_id"])["_r"].min().map({v: k for k, v in rank.items()})
    # Attributes from the first registration whose source is known (else first registration).
    df["_known"] = (df["source"] != "Unknown").astype(int)
    first = (df.sort_values("_known", ascending=False, kind="stable")
             .drop_duplicates(["webinar", "person_id"]).set_index(["webinar", "person_id"]))
    first["status"] = best_status
    first["dup_count"] = df.groupby(["webinar", "person_id"]).size()
    return first.reset_index().drop(columns=["_r", "_known"])


def attendance_status(p: pd.DataFrame) -> dict:
    n = len(p)
    a = int((p["status"] == ATTENDED).sum())
    ns = int((p["status"] == NOSHOW).sum())
    u = int((p["status"] == UNKNOWN).sum())
    if n == 0:
        state = "Unavailable"
    elif a == 0:
        state = "Unavailable"   # zero confirmed attendees => attendance data not merged
    elif u > 0:
        state = "Provisional"
    else:
        state = "Final"
    return {"registrations": n, "attendees": a, "no_shows": ns, "unknown": u, "state": state,
            "rate": a / n * 100 if n and state != "Unavailable" else None,
            "rate_upper": (a + u) / n * 100 if n else None}


def segment(p: pd.DataFrame, col: str, order=None, rate_ok=True) -> pd.DataFrame:
    if col not in p.columns:
        return pd.DataFrame()
    g = p.groupby(col).agg(Registrations=("status", "size"),
                           Attendees=("status", lambda s: int((s == ATTENDED).sum())),
                           **{"No-shows": ("status", lambda s: int((s == NOSHOW).sum())),
                              "Unknown status": ("status", lambda s: int((s == UNKNOWN).sum()))})
    if order:
        g = g.reindex([o for o in order if o in g.index] + [i for i in g.index if i not in order])
    tot_r, tot_a = g["Registrations"].sum(), g["Attendees"].sum()
    g["Attendance %"] = (g["Attendees"] / g["Registrations"] * 100).round(1) if rate_ok else np.nan
    g["No-show %"] = (g["No-shows"] / g["Registrations"] * 100).round(1) if rate_ok else np.nan
    g["Registration share %"] = (g["Registrations"] / tot_r * 100).round(1)
    g["Attendee share %"] = (g["Attendees"] / tot_a * 100).round(1) if rate_ok and tot_a else np.nan
    g.index.name = col
    return g.reset_index()


def cross(p: pd.DataFrame, a: str, b: str, rate_ok=True, order_b=None):
    """Returns (registrations pivot, attendance % pivot)."""
    if a not in p.columns or b not in p.columns:
        return pd.DataFrame(), pd.DataFrame()
    n = pd.crosstab(p[a], p[b])
    att = pd.crosstab(p[a], p[b], values=(p["status"] == ATTENDED).astype(int), aggfunc="sum").fillna(0)
    if order_b:
        cols = [c for c in order_b if c in n.columns] + [c for c in n.columns if c not in order_b]
        n, att = n[cols], att[cols]
    rows = [r for r in clf.SOURCE_ORDER if r in n.index] + [r for r in n.index if r not in clf.SOURCE_ORDER]
    n, att = n.reindex(rows), att.reindex(rows)
    rate = (att / n.replace(0, np.nan) * 100).round(1) if rate_ok else n * np.nan
    return n, rate


def data_quality(reg: Prepared, people: pd.DataFrame, webinar: str, att_info: AttendanceInfo | None) -> pd.DataFrame:
    df = reg.df if webinar == "__ALL__" else reg.df[reg.df["webinar"] == webinar]
    rows = []
    add = lambda m, v, note="": rows.append({"Check": m, "Value": v, "Note": note})
    add("Registration records (after removing blank rows)", len(df))
    add("Unique registrants", len(people), "De-duplicated within each webinar by " +
        ", ".join(sorted(df["id_basis"].unique())))
    add("Duplicate registration records", len(df) - len(people), "Same person registered more than once")
    add("Records with no identifier (cannot de-duplicate)", int((df["id_basis"] == "none").sum()))
    add("Blank spacer rows removed (whole file)", reg.empty_rows)
    add("Test / dummy records excluded (whole file)", reg.test_rows)
    add("Spreadsheet error cells (#ERROR!, #VALUE!…) treated as blank", reg.error_cells)
    add("Rows with webinar name but no tag (whole file)", reg.untagged_rows, "Grouped as 'untagged: <name>'")
    add("Registrants with Unknown source", int((people["source"] == "Unknown").sum()),
        f"{(people['source'] == 'Unknown').mean() * 100:.1f}% — no usable UTM values")
    add("Registrants with Unknown attendance status", int((people["status"] == UNKNOWN).sum()),
        "Blank attendance is never counted as attended")
    if "existing_class" in reg.cols and reg.cols["existing_class"] in df.columns:
        ex = people[reg.cols["existing_class"]].replace({"MetaAds": "Ads", "Retarget": "Retargeting"})
        known = ex.notna()
        agree = (ex[known] == people.loc[known, "source"]).mean() * 100 if known.any() else np.nan
        add("Agreement with the file's existing 'Classification' column", f"{agree:.1f}%",
            "Differences are mostly blank-UTM rows that the old sheet labelled 'Others'; here they stay 'Unknown'")
    if att_info and att_info.provided:
        add("Attendance file rows", att_info.rows)
        add("Attendance file rows for this webinar", att_info.rows_for_webinar)
        add("Matching key used", att_info.key or "none")
        add("Matched attendance rows", att_info.matched_rows)
        add("Unmatched attendance rows", att_info.unmatched_rows)
        base = att_info.rows_for_webinar or 0
        add("Data matching success rate", f"{att_info.matched_rows / base * 100:.1f}%" if base else "n/a",
            att_info.note)
        add("Attendance merged?", "Yes" if att_info.reliable else "No")
    else:
        add("Attendance file", "Not uploaded", "Attendance taken from the registration file's own column")
    add("Personal columns removed on upload", ", ".join(reg.dropped_pii) or "none",
        "Never displayed, logged or exported")
    return pd.DataFrame(rows).astype({"Value": str})
