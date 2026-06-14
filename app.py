"""
app.py — ממשק Streamlit למערכת שיבוץ כוח אדם.
"""
import tempfile
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="מערכת שיבוץ כוח אדם",
    page_icon="📋",
    layout="centered",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Assistant:wght@400;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Assistant', sans-serif; direction: rtl; }
    .main-header {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d6a9f 100%);
        color: white; padding: 2rem 2.5rem; border-radius: 16px;
        margin-bottom: 2rem; text-align: center;
    }
    .main-header h1 { font-size: 2rem; font-weight: 700; margin: 0; }
    .main-header p  { font-size: 1rem; opacity: 0.85; margin: 0.5rem 0 0; }
    .step-card {
        background: #f8fafc; border: 1px solid #e2e8f0;
        border-radius: 12px; padding: 1.5rem 2rem; margin-bottom: 1.5rem;
    }
    .step-card h3 { color: #1e3a5f; font-size: 1.15rem; font-weight: 700; margin: 0 0 0.75rem; }
    .metric-row { display: flex; gap: 1rem; margin: 1rem 0; flex-wrap: wrap; }
    .metric-box {
        flex: 1; min-width: 100px; background: white;
        border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 0.85rem 1rem; text-align: center;
    }
    .metric-box .num { font-size: 1.8rem; font-weight: 700; color: #1e3a5f; }
    .metric-box .lbl { font-size: 0.78rem; color: #64748b; margin-top: 0.2rem; }
    .metric-box.warn .num { color: #d97706; }
    .metric-box.err  .num { color: #dc2626; }
    .metric-box.ok   .num { color: #059669; }
    div.stButton > button {
        width: 100%;
        background: linear-gradient(135deg, #1e3a5f, #2d6a9f);
        color: white; border: none; border-radius: 10px;
        padding: 0.75rem 2rem; font-size: 1.05rem; font-weight: 600;
    }
    .divider { border: none; border-top: 1px solid #e2e8f0; margin: 1.5rem 0; }
    .violation-item { font-size: 0.85rem; color: #7f1d1d; padding: 0.2rem 0; }
    .issue-item     { font-size: 0.85rem; color: #92400e; padding: 0.2rem 0; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>📋 מערכת שיבוץ כוח אדם</h1>
    <p>העלאת קבצי Excel → הרצת שיבוץ אוטומטי → הורדת תוצאות</p>
</div>
""", unsafe_allow_html=True)

for key in ["stage","preview_bytes","forecast_bytes","positions_bytes","summary","violations","hard_issues"]:
    if key not in st.session_state:
        st.session_state[key] = None
if st.session_state.stage is None:
    st.session_state.stage = "upload"

# שלב 1
st.markdown('<div class="step-card"><h3 style="direction: rtl; unicode-bidi: bidi-override; text-align: right;">שלב 1 — העלאת קבצי הקלט 📁</h3>', unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
with col1:
    forecast_file    = st.file_uploader("קובץ צפי", type=["xlsx","xls"], key="f_forecast")
with col2:
    positions_file   = st.file_uploader("קובץ עמדות", type=["xlsx","xls"], key="f_positions")
with col3:
    constraints_file = st.file_uploader("קובץ אילוצים (אופציונלי)", type=["xlsx","xls"], key="f_constraints")
st.markdown('</div>', unsafe_allow_html=True)

# שלב 2
st.markdown('<div class="step-card"><h3 style="direction: rtl; unicode-bidi: bidi-override; text-align: right;">שלב 2 — הכנת צפי עם איקסים 🔍</h3>', unsafe_allow_html=True)
st.markdown('<p style="color:#64748b;font-size:0.9rem;">סימון X אפור בתאים שהעובד חסום בהם. הורד, בדוק ואשר לפני שיבוץ.</p>', unsafe_allow_html=True)

ready_step1 = forecast_file and positions_file
if st.button("⚙️ הכן קובץ צפי עם איקסים", disabled=not ready_step1, key="btn_step1"):
    with st.spinner("מעבד אילוצים…"):
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from data_loader import assign_night_limits, load_constraints, load_forecast, load_positions, merge_into_employees
            from output_writer import write_forecast_with_constraints
            import config as cfg

            with tempfile.TemporaryDirectory() as tmpdir:
                fp = Path(tmpdir) / "forecast.xlsx"
                pp = Path(tmpdir) / "positions.xlsx"
                fp.write_bytes(forecast_file.read())
                pp.write_bytes(positions_file.read())
                forecast_file.seek(0); positions_file.seek(0)
                cp = None
                if constraints_file:
                    cp = Path(tmpdir) / "constraints.xlsx"
                    cp.write_bytes(constraints_file.read())
                    constraints_file.seek(0)

                employees = load_forecast(str(fp))
                slots     = load_positions(str(pp))
                constraints = load_constraints(str(cp)) if cp else {}
                merge_into_employees(employees, constraints)
                assign_night_limits(employees)

                out_cfg = cfg.OUTPUT
                preview_path = Path(tmpdir) / "preview.xlsx"
                write_forecast_with_constraints(
                    employees, str(fp), str(preview_path),
                    name_col=out_cfg["forecast_name_col"],
                    shift_start_col=out_cfg["forecast_shift_start_col"],
                    header_row=out_cfg["forecast_header_row"],
                )
                st.session_state.preview_bytes = preview_path.read_bytes()
                with_c = sum(1 for e in employees if e.has_constraints)
                st.session_state.summary = {
                    "employees": len(employees),
                    "open": sum(1 for s in slots if s.raw_value == "1"),
                    "with_c": with_c,
                    "without": len(employees) - with_c,
                }
                st.session_state.stage = "preview"
                st.rerun()
        except Exception as exc:
            import traceback
            st.error(f"שגיאה: {exc}")
            st.code(traceback.format_exc())

if st.session_state.stage in ("preview","done") and st.session_state.preview_bytes:
    s = st.session_state.summary or {}
    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-box ok"><div class="num">{s.get('employees','-')}</div><div class="lbl">עובדים</div></div>
        <div class="metric-box"><div class="num">{s.get('open','-')}</div><div class="lbl">עמדות פתוחות</div></div>
        <div class="metric-box"><div class="num">{s.get('with_c','-')}</div><div class="lbl">עם אילוצים</div></div>
        <div class="metric-box warn"><div class="num">{s.get('without','-')}</div><div class="lbl">ללא אילוצים</div></div>
    </div>""", unsafe_allow_html=True)
    st.download_button("⬇️ הורד: צפי עם איקסים.xlsx",
        data=st.session_state.preview_bytes,
        file_name="צפי עם איקסים.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.markdown('</div>', unsafe_allow_html=True)

# הגדרות שיבוץ דינמיות
st.markdown('<div class="step-card"><h3 style="direction: rtl; text-align: right;">⚙️ הגדרות שיבוץ להרצה הזו</h3>', unsafe_allow_html=True)

import config as cfg

col_a, col_b, col_c = st.columns(3)

with col_a:
    top_no_night = st.number_input("כמה בכירים בלי לילות?", min_value=0, max_value=100, value=10)
    group2_size = st.number_input("גודל קבוצה שנייה", min_value=0, max_value=100, value=10)
    group3_size = st.number_input("גודל קבוצה שלישית", min_value=0, max_value=100, value=10)

with col_b:
    group2_nights = st.number_input("מקסימום לילות לקבוצה שנייה", min_value=0, max_value=7, value=1)
    group3_nights = st.number_input("מקסימום לילות לקבוצה שלישית", min_value=0, max_value=7, value=2)
    rest_nights = st.number_input("מקסימום לילות לכל השאר", min_value=0, max_value=7, value=3)

with col_c:
    max_streak = st.number_input("מקסימום רצף משמרות", min_value=1, max_value=10, value=4)
    max_shifts = st.number_input("מקסימום משמרות לעובד", min_value=1, max_value=14, value=7)

cfg.SCHEDULING["night_limit_groups"] = [
    [top_no_night, 0],
    [group2_size, group2_nights],
    [group3_size, group3_nights],
    [None, rest_nights],
]

cfg.SCHEDULING["max_shift_streak"] = max_streak
cfg.SCHEDULING["default_max_shifts"] = max_shifts

st.markdown('</div>', unsafe_allow_html=True)

# שלב 3
st.markdown('<div class="step-card"><h3 style="direction: rtl; unicode-bidi: bidi-override; text-align: right;">שלב 3 — הרצת שיבוץ 🚀</h3>', unsafe_allow_html=True)
st.markdown('<p style="color:#64748b;font-size:0.9rem;">העלה את קובץ הצפי המאושר והרץ את מנוע השיבוץ.</p>', unsafe_allow_html=True)

approved_file = st.file_uploader("קובץ צפי מאושר", type=["xlsx","xls"], key="f_approved")
ready_step2 = approved_file and positions_file

if st.button("▶️ הרץ שיבוץ מלא", disabled=not ready_step2, key="btn_step2"):
    with st.spinner("מריץ מנוע שיבוץ… אנא המתן"):
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from data_loader import assign_night_limits, load_forecast, load_positions, merge_into_employees
            from constraints import validate_schedule
            from output_writer import write_positions_filled, write_forecast_filled
            from scheduler import Scheduler
            import config as cfg

            with tempfile.TemporaryDirectory() as tmpdir:
                ap = Path(tmpdir) / "approved.xlsx"
                pp = Path(tmpdir) / "positions.xlsx"
                ap.write_bytes(approved_file.read())
                positions_file.seek(0)
                pp.write_bytes(positions_file.read())
                approved_file.seek(0); positions_file.seek(0)

                approved_employees = load_forecast(str(ap))
                slots = load_positions(str(pp))
                merge_into_employees(approved_employees, {})
                for emp in approved_employees:
                    if any(v.upper() == "X" for v in emp.forecast_cells.values()):
                        emp.has_constraints = True
                assign_night_limits(approved_employees)

                scheduler = Scheduler(approved_employees, slots)
                scheduler.run()

                total_open  = sum(1 for s in slots if s.raw_value == "1")
                filled      = sum(1 for s in slots if not s.locked and s.assigned_employee)
                issues      = validate_schedule(list(scheduler.employees.values()), slots)
                hard_issues = [i for i in issues if "Unfilled" not in i]

                out_cfg = cfg.OUTPUT
                pos_out  = Path(tmpdir) / "positions_out.xlsx"
                fore_out = Path(tmpdir) / "forecast_out.xlsx"
                write_positions_filled(slots, str(pp), str(pos_out),
                    pos_col=out_cfg["positions_pos_col"],
                    shift_col=out_cfg["positions_shift_col"],
                    day_start_col=out_cfg["positions_day_start"],
                    header_row=out_cfg["positions_header_row"])
                write_forecast_filled(list(scheduler.employees.values()),
                    str(ap), str(fore_out),
                    name_col=out_cfg["forecast_name_col"],
                    shift_start_col=out_cfg["forecast_shift_start_col"],
                    header_row=out_cfg["forecast_header_row"])

                st.session_state.positions_bytes = pos_out.read_bytes()
                st.session_state.forecast_bytes  = fore_out.read_bytes()
                st.session_state.summary = {
                    **(st.session_state.summary or {}),
                    "total_open": total_open, "filled": filled,
                    "unfilled": total_open - filled,
                    "violations": len(scheduler.violations),
                    "hard": len(hard_issues),
                }
                st.session_state.violations  = scheduler.violations[:20]
                st.session_state.hard_issues = hard_issues[:10]
                st.session_state.stage = "done"
                st.rerun()
        except Exception as exc:
            import traceback
            st.error(f"שגיאה: {exc}")
            st.code(traceback.format_exc())
st.markdown('</div>', unsafe_allow_html=True)

# שלב 4
if st.session_state.stage == "done":
    s          = st.session_state.summary or {}
    unfilled   = s.get("unfilled", 0)
    violations = s.get("violations", 0)
    hard       = s.get("hard", 0)

    st.markdown('<div class="step-card"><h3 style="direction: rtl; unicode-bidi: bidi-override; text-align: right;">שלב 4 — תוצאות השיבוץ ✅</h3>', unsafe_allow_html=True)
    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-box ok"><div class="num">{s.get('filled','-')}</div><div class="lbl">שובצו</div></div>
        <div class="metric-box {'err' if unfilled else 'ok'}"><div class="num">{unfilled}</div><div class="lbl">לא שובצו</div></div>
        <div class="metric-box {'warn' if violations else 'ok'}"><div class="num">{violations}</div><div class="lbl">הפרות רכות</div></div>
        <div class="metric-box {'err' if hard else 'ok'}"><div class="num">{hard}</div><div class="lbl">הפרות קשות</div></div>
    </div>""", unsafe_allow_html=True)

    if st.session_state.violations:
        with st.expander(f"⚠️ הפרות רכות ({len(st.session_state.violations)})"):
            from models import DAYS
            for emp_name, day, shift in st.session_state.violations:
                st.markdown(f'<div class="violation-item">• {emp_name} — {DAYS[day]} — {shift.value}</div>', unsafe_allow_html=True)

    if st.session_state.hard_issues:
        with st.expander(f"🚨 הפרות קשות ({len(st.session_state.hard_issues)})"):
            for issue in st.session_state.hard_issues:
                st.markdown(f'<div class="issue-item">• {issue}</div>', unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.session_state.forecast_bytes:
            st.download_button("⬇️ צפי מלא.xlsx",
                data=st.session_state.forecast_bytes, file_name="צפי מלא.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)
    with col_b:
        if st.session_state.positions_bytes:
            st.download_button("⬇️ עמדות מלא.xlsx",
                data=st.session_state.positions_bytes, file_name="עמדות מלא.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)
    if st.button("🔄 התחל שיבוץ חדש"):
        for key in ["stage","preview_bytes","forecast_bytes","positions_bytes","summary","violations","hard_issues"]:
            st.session_state[key] = None
        st.session_state.stage = "upload"
        st.rerun()
