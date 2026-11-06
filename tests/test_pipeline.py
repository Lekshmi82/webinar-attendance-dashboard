"""Run: python -m tests.test_pipeline <registration_file> [attendance_file]"""
import sys
from pathlib import Path
from wid import pipeline as pl, insights, classify as clf

def run(reg_path, att_path=None, webinar=None):
    reg = pl.prepare_registrations(Path(reg_path).name, Path(reg_path).read_bytes())
    assert not reg.errors, reg.errors
    opts = pl.webinar_options(reg)
    print(opts.to_string())
    webinar = webinar or opts.webinar.iloc[0]
    att, info = None, None
    if att_path:
        att, info = pl.load_attendance(Path(att_path).name, Path(att_path).read_bytes(), reg, webinar)
        print("ATT:", info)
    p = pl.build_people(reg, webinar, att)
    st = pl.attendance_status(p); print(st)
    ok = st["state"] != "Unavailable"
    src = pl.segment(p, "source", clf.SOURCE_ORDER, ok); print(src.to_string())
    exp = pl.segment(p, "experience", pl.EXP_ORDER, ok); print(exp.to_string())
    n, r = pl.cross(p, "source", "experience", ok, pl.EXP_ORDER)
    i, rc = insights.generate(st, src, exp, pl.segment(p, "role", None, ok), pl.segment(p, "audience", None, ok),
                              pl.segment(p, "career_stage", pl.STAGE_ORDER, ok), r, n)
    print("\n".join(i)); print("REC:", "\n".join(rc))
    print(pl.data_quality(reg, p, webinar, info).to_string())
    assert not any(c for c in p.columns if "mail" in c.lower() or "phone" in c.lower())
    return st

if __name__ == "__main__":
    run(*sys.argv[1:])
