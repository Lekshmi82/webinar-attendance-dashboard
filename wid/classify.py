"""Transparent, ordered acquisition-source rules built from utm_source / medium / campaign / content / term.

Rules are applied top to bottom; the first match wins. Anything not matched stays 'Unknown'.
"""
from __future__ import annotations

import re

import pandas as pd

SOURCE_ORDER = ["Ads", "Organic", "Retargeting", "Others", "Unknown"]

RETARGET_RE = re.compile(r"retarget|rereg|wts|whatsapp|waitlist|^zoom$|email_reminder|newsletter", re.I)
PAID_MEDIUMS = {"paid_social", "lead_form", "cpc", "paid", "ppc", "display", "paid_search"}
PAID_SOURCES = {"meta ads", "meta", "facebook ads", "google ads", "linkedin ads"}
ORGANIC_SOURCES = {"website", "instagram", "youtube", "facebook", "linkedin", "twitter", "x", "threads"}
OTHER_SOURCE_RE = re.compile(r"^reps|partnership|preread|chatgpt|perplexity|gemini|referral|affiliate", re.I)

RULES = [
    ("R1", "Retargeting", "utm_content / utm_campaign / utm_source mentions retarget, rereg, wts (WhatsApp), "
                          "whatsapp, waitlist or is exactly 'zoom' (messages to already-known leads)"),
    ("R2", "Ads", "utm_medium is paid (paid_social, lead_form, cpc, …) OR utm_source is 'Meta Ads' / 'meta'"),
    ("R3", "Organic", "utm_source = ig AND utm_medium = social AND utm_content = link_in_bio "
                      "(Instagram's automatic profile-link tag)"),
    ("R4", "Organic", "utm_source is an owned channel (Website, Instagram, YouTube, Facebook, LinkedIn, X) "
                      "with no paid medium"),
    ("R5", "Others", "utm_source is reps*, Partnership, preRead*, chatgpt.com or another referral"),
    ("R6", "Others", "Any other non-empty utm_source not covered above (kept visible by raw value)"),
    ("R7", "Unknown", "All UTM fields blank, or only ambiguous values — never assumed Organic"),
]


def _l(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA else str(v).strip().lower()


def classify_row(src, med, camp, cont, term) -> tuple[str, str]:
    src, med, camp, cont, term = map(_l, (src, med, camp, cont, term))
    if any(RETARGET_RE.search(x) for x in (cont, camp, src) if x):
        return "Retargeting", "R1"
    if med in PAID_MEDIUMS or src in PAID_SOURCES:
        return "Ads", "R2"
    if src == "ig" and med == "social" and cont == "link_in_bio":
        return "Organic", "R3"
    if src in ORGANIC_SOURCES and med not in PAID_MEDIUMS:
        return "Organic", "R4"
    if src and OTHER_SOURCE_RE.search(src):
        return "Others", "R5"
    if src:
        return "Others", "R6"
    return "Unknown", "R7"


def classify(df: pd.DataFrame, cols: dict) -> pd.DataFrame:
    get = lambda k: df[cols[k]] if k in cols else pd.Series([None] * len(df), index=df.index)
    res = [classify_row(*vals) for vals in zip(get("utm_source"), get("utm_medium"), get("utm_campaign"),
                                               get("utm_content"), get("utm_term"))]
    out = df.copy()
    out["source"] = [r[0] for r in res]
    out["source_rule"] = [r[1] for r in res]
    out["source_detail"] = get("utm_source").fillna("(blank)").astype(str).values
    return out


def rules_table() -> pd.DataFrame:
    return pd.DataFrame(RULES, columns=["Rule", "Source", "Condition"])
