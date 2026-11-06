# 100xEngineers — Webinar Attendance Intelligence Dashboard

Streamlit dashboard that turns weekly registration + attendance exports into attendance analytics
(by acquisition source, experience, role, audience), insights, data-quality checks and an aggregated Excel report.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Open http://localhost:8501.

## Weekly workflow
1. Upload the registration/source file (CSV/XLSX — e.g. `c09_webNN` sheet or the master sheet).
2. Optionally upload the attendance file (Zoom attendee report or a sheet with `Attended` / `Time in Session`).
3. Pick the webinar (or "All webinars"), click **Generate Analytics**.
4. Download the Excel report from the sidebar. **Reset** clears everything.

## Rules
- **Attendance %** = unique attendees ÷ unique registrations × 100.
- Status per webinar: **Final** (no unknowns), **Provisional** (some blank statuses — shows a range),
  **Unavailable** (0 confirmed attendees → attendance not merged; % is hidden).
- Blank attendance is never counted as attended.
- De-duplication within a webinar: `UniqueLink` → `Submission ID` → `Record ID`.
- Attendance merge uses the same non-personal IDs; hashed email only with the consent checkbox.
  If < 50% of attendance rows match, the merge is rejected (flagged, not guessed).
- Source classification: ordered, transparent rules in `wid/classify.py` (also shown in the app).
  Blank UTMs stay **Unknown** — never assumed Organic.

## Privacy
Name, email and phone columns are dropped immediately on upload, in memory only. They are never shown,
logged or exported. Raw data files are git-ignored. Do not commit lead data.

## Layout
- `app.py` — Streamlit UI
- `wid/loader.py` — file reading, header detection, column aliases, PII scrubbing, validation
- `wid/classify.py` — acquisition-source rules
- `wid/pipeline.py` — de-duplication, attendance merge, status, segment/cross tables, data quality
- `wid/insights.py` — evidence-based insights and recommendations
- `wid/report.py` — aggregated Excel export
- `tests/test_pipeline.py` — `python -m tests.test_pipeline <registration_file> [attendance_file] [webinar]`
