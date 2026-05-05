"""
main.py — נקודת כניסה ל-CLI של מערכת שיבוץ כוח אדם.

תהליך דו-שלבי:
  שלב 1: טוען את שלושת קבצי הקלט, מסמן X אפור בתאי הצפי לפי האילוצים,
          שומר "צפי עם איקסים.xlsx" לבדיקת המנהל.
  שלב 2: מקבל את קובץ הצפי המאושר (לאחר תיקון ידני אפשרי),
          מריץ את המתזמן, ושומר "צפי מלא.xlsx" ו"עמדות מלא.xlsx".
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from tkinter import Tk, filedialog, messagebox

from data_loader import (
    assign_night_limits,
    load_constraints,
    load_forecast,
    load_positions,
    merge_into_employees,
)
from constraints import validate_schedule
from output_writer import (
    write_forecast_filled,
    write_forecast_with_constraints,
    write_positions_filled,
)
from scheduler import Scheduler
from models import DAYS


# ---------------------------------------------------------------------------
# פונקציות עזר לממשק גרפי
# ---------------------------------------------------------------------------

def _pick_file(title: str, *, optional: bool = False,
               initialdir: str = "", initialfile: str = "") -> str:
    """פותח חלון בחירת קובץ ומחזיר את הנתיב הנבחר (או "" אם דולג)."""
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    kwargs: dict = dict(
        title=title,
        filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
    )
    if initialdir:
        kwargs["initialdir"] = initialdir
    if initialfile:
        kwargs["initialfile"] = initialfile
    path = filedialog.askopenfilename(**kwargs)
    root.destroy()
    if not path and not optional:
        root2 = Tk()
        root2.withdraw()
        messagebox.showerror("קובץ חסר", f"חייב לבחור קובץ: {title}")
        root2.destroy()
        sys.exit(1)
    return path or ""


def _show_info(title: str, msg: str) -> None:
    root = Tk()
    root.withdraw()
    messagebox.showinfo(title, msg)
    root.destroy()


def _ask_ok_cancel(title: str, msg: str) -> bool:
    root = Tk()
    root.withdraw()
    result = messagebox.askokcancel(title, msg)
    root.destroy()
    return bool(result)


def _banner(title: str) -> None:
    width = 60
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# פונקציה ראשית
# ---------------------------------------------------------------------------

def main() -> None:
    # =========================================================================
    # שלב 1: הכנת קובץ צפי עם איקסים לבדיקה
    # =========================================================================
    _banner("שלב 1 — הכנת צפי עם איקסים")
    print()
    print("בחר את שלושת קבצי הקלט בחלונות הבחירה שיפתחו.")
    print()

    forecast_path    = _pick_file("שלב 1 — בחר קובץ צפי")
    positions_path   = _pick_file("שלב 1 — בחר קובץ עמדות")
    constraints_path = _pick_file(
        "שלב 1 — בחר קובץ אילוצים (ניתן לדלג — סגור את החלון)",
        optional=True,
    )

    out_dir      = Path(forecast_path).parent
    preview_path = str(out_dir / "צפי עם איקסים.xlsx")

    print("\nLoading files …")

    try:
        employees = load_forecast(forecast_path)
    except Exception as exc:
        print(f"[ERROR] Failed to load forecast file: {exc}")
        traceback.print_exc()
        sys.exit(1)
    print(f"  Forecast   : {len(employees)} employees loaded.")

    try:
        slots = load_positions(positions_path)
    except Exception as exc:
        print(f"[ERROR] Failed to load positions file: {exc}")
        traceback.print_exc()
        sys.exit(1)
    total_open   = sum(1 for s in slots if s.raw_value == "1")
    total_locked = sum(1 for s in slots if s.locked)
    print(f"  Positions  : {len(slots)} slots "
          f"({total_open} open, {total_locked} pre-assigned).")

    constraints = {}
    if constraints_path:
        try:
            constraints = load_constraints(constraints_path)
        except Exception as exc:
            print(f"[WARNING] Failed to load constraints: {exc}")
            print("  ממשיך ללא אילוצים — כל העובדים פנויים בכל המשמרות.")
    else:
        print("  Constraints: לא סופק — כל העובדים נחשבים פנויים.")

    merge_into_employees(employees, constraints)
    assign_night_limits(employees)

    with_c  = sum(1 for e in employees if e.has_constraints)
    without = len(employees) - with_c
    print(f"  Constraints: {with_c} עובדים עם אילוצים, {without} ללא (יסומנו צהוב).")

    out_cfg = __import__("config").OUTPUT

    print("\nWriting preview file …")
    try:
        write_forecast_with_constraints(
            employees,
            forecast_path,
            preview_path,
            name_col=out_cfg["forecast_name_col"],
            shift_start_col=out_cfg["forecast_shift_start_col"],
            header_row=out_cfg["forecast_header_row"],
        )
    except Exception as exc:
        print(f"[ERROR] Could not write preview file: {exc}")
        traceback.print_exc()
        sys.exit(1)

    print()
    cont = _ask_ok_cancel(
        "שלב 1 הושלם — נדרשת בדיקה",
        f"קובץ הצפי עם האיקסים נשמר ב:\n{out_dir}\n\n"
        "• צפי עם איקסים.xlsx\n\n"
        "אנא עבור על הקובץ, בצע שינויים ידניים אם נדרש ושמור אותו.\n\n"
        "לחץ אישור כשתהיה מוכן לשלב השיבוץ, או ביטול לעצירה.",
    )

    if not cont:
        print("הופסק על ידי המשתמש לאחר שלב 1.")
        return

    # =========================================================================
    # שלב 2: שיבוץ על בסיס הקובץ המאושר
    # =========================================================================
    print()
    _banner("שלב 2 — שיבוץ")
    print()
    print("בחר את קובץ הצפי המאושר לשיבוץ.")
    print()

    approved_path = _pick_file(
        "שלב 2 — בחר את קובץ הצפי המאושר",
        optional=False,
        initialdir=str(out_dir),
        initialfile="צפי עם איקסים.xlsx",
    )

    print("\nLoading approved forecast …")
    try:
        approved_employees = load_forecast(approved_path)
    except Exception as exc:
        print(f"[ERROR] Failed to load approved forecast: {exc}")
        traceback.print_exc()
        sys.exit(1)
    print(f"  Employees: {len(approved_employees)} loaded.")

    # הקובץ המאושר הוא מקור האמת: X בתאים = חסימה.
    # אין טעינה חוזרת של קובץ האילוצים כדי שהסרת X ידנית תיכבד.
    merge_into_employees(approved_employees, {})
    # עובד שיש לו X בצפי נחשב "יש לו אילוצים" לצורכי קידוד צבעים
    for emp in approved_employees:
        if any(v.upper() == "X" for v in emp.forecast_cells.values()):
            emp.has_constraints = True
    assign_night_limits(approved_employees)

    with_c  = sum(1 for e in approved_employees if e.has_constraints)
    without = len(approved_employees) - with_c
    print(f"  עם X (אילוצים): {with_c},  ללא: {without} (יהיו צהובים בפלט).")

    print("\nRunning scheduler …")
    scheduler = Scheduler(approved_employees, slots)
    scheduler.run()

    filled       = sum(1 for s in slots if not s.locked and s.assigned_employee)
    unfilled     = total_open - filled
    n_violations = len(scheduler.violations)

    print()
    _banner("Scheduling Summary")
    print(f"  Open positions      : {total_open}")
    print(f"  Filled              : {filled}")
    print(f"  Unfilled            : {unfilled}  {'(!)' if unfilled else 'OK'}")
    print(f"  Soft violations (X) : {n_violations}")

    if scheduler.violations:
        print()
        print("  Soft-constraint violations (employee assigned to a blocked shift):")
        for emp_name, day, shift in scheduler.violations[:15]:
            print(f"    {emp_name:30s}  {DAYS[day]:10s}  {shift.value}")
        if len(scheduler.violations) > 15:
            print(f"    … and {len(scheduler.violations) - 15} more")

    issues      = validate_schedule(list(scheduler.employees.values()), slots)
    hard_issues = [i for i in issues if "Unfilled" not in i]
    if hard_issues:
        print()
        print("  Hard-constraint issues detected:")
        for issue in hard_issues[:10]:
            print(f"    {issue}")

    print("\nWriting output files …")

    positions_output = str(out_dir / "עמדות מלא.xlsx")
    forecast_output  = str(out_dir / "צפי מלא.xlsx")

    try:
        write_positions_filled(
            slots,
            positions_path,
            positions_output,
            pos_col=out_cfg["positions_pos_col"],
            shift_col=out_cfg["positions_shift_col"],
            day_start_col=out_cfg["positions_day_start"],
            header_row=out_cfg["positions_header_row"],
        )
    except Exception as exc:
        print(f"[ERROR] Could not write positions file: {exc}")
        traceback.print_exc()

    try:
        write_forecast_filled(
            list(scheduler.employees.values()),
            approved_path,
            forecast_output,
            name_col=out_cfg["forecast_name_col"],
            shift_start_col=out_cfg["forecast_shift_start_col"],
            header_row=out_cfg["forecast_header_row"],
        )
    except Exception as exc:
        print(f"[ERROR] Could not write forecast file: {exc}")
        traceback.print_exc()

    print()
    print("Done!  Output files saved to:")
    print(f"  {positions_output}")
    print(f"  {forecast_output}")

    _show_info(
        "שיבוץ הושלם",
        f"הקבצים נשמרו בתיקייה:\n{out_dir}\n\n"
        "• צפי מלא.xlsx\n"
        "• עמדות מלא.xlsx",
    )


if __name__ == "__main__":
    main()
