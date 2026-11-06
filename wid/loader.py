"""File loading, header detection, column mapping, privacy scrubbing and validation."""
from __future__ import annotations

import hashlib
import io
import re

import pandas as pd

# Canonical field -> accepted column names (case-insensitive, whitespace-insensitive).
ALIASES = {
    "unique_link": ["UniqueLink", "Unique Link", "Join Link", "Join URL"],
    "submission_id": ["Submission ID"],
    "record_id": ["Record ID", "Lead ID", "CRM ID"],
    "respondent_id": ["Respondent ID"],
    "tag": ["Tags", "Webinar Tag", "Webinar Code"],
    "title": ["web_name", "Webinar Name", "Webinar Title", "Topic"],
    "attended": ["Attended Webinar", "Attended", "Attendance"],
    "time_in_session": ["Time in Session", "Time in Session (minutes)", "Duration (Minutes)", "Duration"],
    "utm_source": ["utm_source"],
    "utm_medium": ["utm_medium"],
    "utm_campaign": ["utm_campaign"],
    "utm_content": ["utm_content"],
    "utm_term": ["utm_term"],
    "experience": ["Work Experience", "Experience"],
    "role": ["What best describes your current role?", "Role", "Current Role"],
    "international": ["International Lead"],
    "city": ["City", "Location"],
    "country": ["Country/Region Name", "Country"],
    "submitted_at": ["Submitted at", "Registration Time"],
    "existing_class": ["Classification"],
}

# Personal columns: never displayed, never exported. Emails can only be used (hashed) with consent.
PII_PATTERNS = re.compile(
    r"(e-?mail|phone|mobile|whatsapp no|^name$|first name|last name|user name|countrycode|clean phone)",
    re.I,
)
EMAIL_PATTERN = re.compile(r"e-?mail", re.I)
ERROR_VALUES = {"#ERROR!", "#VALUE!", "#REF!", "#N/A", "#NAME?", "#DIV/0!"}
HEADER_HINTS = {"attended", "email", "user email", "time in session", "uniquelink", "submission id",
                "utm_source", "tags", "web_name", "join time", "record id", "work experience"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def read_any(uploaded_name: str, data: bytes) -> pd.DataFrame:
    """Read CSV/Excel bytes into a string DataFrame. Detects Zoom-style preamble lines above the header."""
    name = uploaded_name.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        raw = pd.read_excel(io.BytesIO(data), dtype=str, header=None, engine="openpyxl")
    elif name.endswith(".csv"):
        text = None
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("Could not decode the CSV file (unknown text encoding).")
        raw = pd.read_csv(io.StringIO(text), dtype=str, header=None, keep_default_na=True,
                          on_bad_lines="skip", engine="python")
    else:
        raise ValueError("Unsupported file type. Upload a .csv or .xlsx file.")
    if raw.empty:
        raise ValueError("The file is empty.")
    # Header row = first row (within top 40) that contains >=2 known column names.
    header_idx = 0
    for i in range(min(40, len(raw))):
        vals = {_norm(v) for v in raw.iloc[i].dropna().tolist()}
        if len(vals & HEADER_HINTS) >= 2:
            header_idx = i
            break
    cols = [str(c).strip() if pd.notna(c) else f"col_{j}" for j, c in enumerate(raw.iloc[header_idx])]
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = _dedupe_cols(cols)
    df = df.reset_index(drop=True)
    return df


def _dedupe_cols(cols):
    seen, out = {}, []
    for c in cols:
        if c in seen:
            seen[c] += 1
            out.append(f"{c}.{seen[c]}")
        else:
            seen[c] = 0
            out.append(c)
    return out


def map_columns(df: pd.DataFrame) -> dict:
    """Return {canonical: actual column name} for columns present in df."""
    lookup = {_norm(c): c for c in df.columns}
    found = {}
    for key, names in ALIASES.items():
        for n in names:
            if _norm(n) in lookup:
                found[key] = lookup[_norm(n)]
                break
    return found


def scrub(df: pd.DataFrame, allow_email_hash: bool = False) -> tuple[pd.DataFrame, list[str], bool]:
    """Drop PII columns. Optionally replace email with a salted-free SHA-256 hash (in memory only).

    Returns (clean_df, dropped_column_names, email_hash_available).
    """
    df = df.copy()
    has_hash = False
    if allow_email_hash:
        email_cols = [c for c in df.columns if EMAIL_PATTERN.search(c)]
        for c in email_cols:
            s = df[c].astype("string").str.strip().str.lower()
            if s.notna().any() and s.str.contains("@", na=False).any():
                df["_email_hash"] = s.where(s.str.contains("@", na=False)).map(
                    lambda v: hashlib.sha256(v.encode()).hexdigest() if isinstance(v, str) else None)
                has_hash = True
                break
    dropped = [c for c in df.columns if PII_PATTERNS.search(c) and c != "_email_hash"]
    df = df.drop(columns=dropped)
    return df, dropped, has_hash


def clean_values(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip whitespace, turn blanks and spreadsheet error values into NA. Returns (df, error_cell_count)."""
    df = df.apply(lambda s: s.astype("string").str.strip())
    err = int(df.isin(list(ERROR_VALUES)).sum().sum())
    df = df.mask(df.isin(list(ERROR_VALUES)) | (df == ""))
    return df, err


def validate_registration(cols: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for a registration file's mapped columns."""
    errors, warnings = [], []
    if not any(k in cols for k in ("unique_link", "submission_id", "record_id")):
        errors.append("No registration identifier column found (need UniqueLink, Submission ID or Record ID).")
    if not any(k in cols for k in ("tag", "title")):
        warnings.append("No webinar column (Tags / web_name) found — the whole file is treated as one webinar.")
    if not any(k.startswith("utm_") for k in cols):
        warnings.append("No UTM columns found — every registration will be 'Unknown' source.")
    if "experience" not in cols:
        warnings.append("No 'Work Experience' column — experience analysis unavailable.")
    if "attended" not in cols and "time_in_session" not in cols:
        warnings.append("No attendance column in the registration file — upload an attendance file.")
    return errors, warnings
