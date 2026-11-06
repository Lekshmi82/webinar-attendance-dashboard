"""Aggregated Excel report (no row-level or personal data)."""
from __future__ import annotations

import io
import re

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def _strip_md(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s)


def build_excel(meta: dict, sheets: dict[str, pd.DataFrame], insights: list, recs: list) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary = pd.DataFrame(list(meta.items()), columns=["Metric", "Value"])
        summary.to_excel(xw, sheet_name="Summary", index=False)
        pd.DataFrame({"Insights": [_strip_md(i) for i in insights] or ["—"]}).to_excel(
            xw, sheet_name="Insights", index=False)
        pd.DataFrame({"Recommendations": [_strip_md(r) for r in recs] or ["—"]}).to_excel(
            xw, sheet_name="Recommendations", index=False)
        for name, df in sheets.items():
            if df is None or df.empty:
                continue
            idx = not isinstance(df.index, pd.RangeIndex)
            df.to_excel(xw, sheet_name=name[:31], index=idx)
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", fgColor="1F3A5F")
            for i, col in enumerate(ws.columns, 1):
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[get_column_letter(i)].width = min(max(12, width + 2), 90)
    return buf.getvalue()
