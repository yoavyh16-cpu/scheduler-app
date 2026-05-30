"""
בדיקת אילוצים קשיחים עבור מנוע התזמון.

כל פונקציה מחזירה זוג (bool, reason_list) כדי שהקוראים יוכלו לרשום *מדוע*
עובד לא יכול להיות ממוקם בסלוט מסוים.
"""
from __future__ import annotations

from typing import List, Tuple

import config as _cfg
from models import Employee, PositionSlot, ShiftType

# סלוטים שבהם OT אסור (Friday Noon/Night + כל Saturday)
_FRIDAY  = _cfg.SCHEDULING["friday_idx"]
_SATURDAY = _cfg.SCHEDULING["saturday_idx"]
_OT_FORBIDDEN: set = {
    (_FRIDAY,   ShiftType.NOON),
    (_FRIDAY,   ShiftType.NIGHT),
    (_SATURDAY, ShiftType.MORNING),
    (_SATURDAY, ShiftType.NOON),
    (_SATURDAY, ShiftType.NIGHT),
}

# רצף מסודר של כל זוגות (day, shift) בשבוע — לשימוש בבדיקות חלון TR.
_ALL_SLOTS: List[Tuple[int, ShiftType]] = [
    (day, shift)
    for day in range(7)
    for shift in [ShiftType.MORNING, ShiftType.NOON, ShiftType.NIGHT]
]
_SLOT_INDEX = {slot: i for i, slot in enumerate(_ALL_SLOTS)}


def _tr_conflict(emp: Employee, day: int, shift: ShiftType) -> bool:
    """מחזיר True אם (day, shift) נופל בתוך חלון 1-לפני או 2-אחרי כל סלוט TR.

    חלון חסום סביב TR באינדקס i: {i-1, i, i+1, i+2}
    (משמרת 1 לפני, TR עצמו, 2 משמרות אחרי).
    """
    target_idx = _SLOT_INDEX.get((day, shift))
    if target_idx is None:
        return False
    for tr_day, tr_shift in emp.training_slots:
        tr_idx = _SLOT_INDEX.get((tr_day, tr_shift))
        if tr_idx is None:
            continue
        if tr_idx - 1 <= target_idx <= tr_idx + 2:
            return True
    return False


# ---------------------------------------------------------------------------
# בדיקות נפרדות (כל אחת מחזירה True כאשר נמצאת *התנגשות*)
# ---------------------------------------------------------------------------

def _already_in_slot(emp: Employee, day: int, shift: ShiftType) -> bool:
    """העובד כבר מוקצה לעמדה אחרת באותו סלוט."""
    return emp.is_working(day, shift)


def _adjacent_conflict(emp: Employee, day: int, shift: ShiftType) -> bool:
    """זוגות משמרות רצופות אסורות:
      Morning + Noon   (אותו יום)
      Noon   + Night   (אותו יום)
      Night  → Morning (יום הבא)
    """
    # רצף באותו יום
    if shift == ShiftType.NOON:
        if emp.is_working(day, ShiftType.MORNING) or emp.is_working(day, ShiftType.NIGHT):
            return True
    if shift == ShiftType.MORNING and emp.is_working(day, ShiftType.NOON):
        return True
    if shift == ShiftType.NIGHT and emp.is_working(day, ShiftType.NOON):
        return True

    # בין ימים: Night(D-1) → Morning(D)
    if shift == ShiftType.MORNING and day > 0 and emp.is_working(day - 1, ShiftType.NIGHT):
        return True

    # בין ימים: Night(D) → Morning(D+1) מכוסה אם לאחר מכן מוקצה Morning(D+1).
    # בדיקה מוקדמת: אם מוקצה Night(D) ו-Morning(D+1) כבר תפוס, חסום.
    if shift == ShiftType.NIGHT and day < 6 and emp.is_working(day + 1, ShiftType.MORNING):
        return True

    return False


def _exceeds_shift_limit(emp: Employee) -> bool:
    """העובד הגיע למקסימום המשמרות המותרות שלו."""
    return emp.assigned_count >= emp.max_shifts


def _exceeds_night_limit(emp: Employee, shift: ShiftType) -> bool:
    """סלוט לילה יעביר את העובד מעבר למכסת משמרות הלילה שלו."""
    return shift == ShiftType.NIGHT and emp.night_count >= emp.max_night_shifts


# ---------------------------------------------------------------------------
# הפונקציה הציבורית הראשית
# ---------------------------------------------------------------------------

def can_assign(
    emp: Employee,
    slot: PositionSlot,
    *,
    allow_blocked: bool = False,
    allow_overload: bool = False,
    allow_extra_night: bool = False,
) -> Tuple[bool, List[str]]:
    """בודק האם ניתן להקצות *emp* ל-*slot*.

    פרמטרים
    ----------
    allow_blocked     : מבטל את בדיקת הזמינות (X) — אילוץ רך.
    allow_overload    : מבטל רק את מכסת המשמרות המקסימלית.
                        אינו מבטל req=0, מגבלות לילה, או רצף.
    allow_extra_night : מאפשר לילה אחד נוסף מעבר ל-max_night_shifts.
                        משמש רק כאשר יש מחסור במשמרות לילה ברמת המערכת.
                        לעולם לא חל על עובדים עם req=0 או top-10
                        (max_night_shifts == 0).

    חוקים קשיחים שלעולם לא ניתן לבטל בשום דגל:
      - req == 0  (עובד בחופשה שבועית מלאה)
      - התנגשות משמרות רצופות
      - max_night_shifts == 0  (קבוצת בכירות עליונה)
    """
    reasons: List[str] = []

    # 0. משמרות נדרשות = 0 → בחופשה שבועית מלאה, לא ניתן להקצות (לא ניתן לשינוי)
    if emp.required_shifts == 0:
        reasons.append("employee has 0 required shifts this week (on leave)")
        return False, reasons

    # 0b. חלון TR — חסימה קשיחה: משמרת 1 לפני TR, TR עצמו, 2 משמרות אחרי (לא ניתן לביטול)
    if _tr_conflict(emp, slot.day, slot.shift_type):
        reasons.append("slot is within TR training window (hard block)")
        return False, reasons

    # 0c. חוקי OT — חלים בכל פעם שהקצאה זו תהיה שעות נוספות (בכל שלב)
    if emp.assigned_count >= emp.required_shifts:
        if emp.has_al_or_sle:
            reasons.append("employee has AL/SLE — OT not allowed")
            return False, reasons
        if (slot.day, slot.shift_type) in _OT_FORBIDDEN:
            # אפשר הקצאה אם לעובד יש משמרת חול שתספוג את ה-OT label.
            # _recompute_ot ידאג שה-OT flag יינחת על משמרת חול, לא על שבת/שישי-צהריים.
            has_absorber = any(
                (d, sh) not in _OT_FORBIDDEN
                for d, sh, _ in emp.assigned
            )
            if not has_absorber:
                reasons.append("OT forbidden on this slot and no weekday shift to absorb OT label")
                return False, reasons

    # 0d. X בצפי — חסימה קשיחה (לא ניתן לביטול, בניגוד ל-X בקובץ האילוצים)
    if (slot.day, slot.shift_type) in emp.forecast_blocked:
        reasons.append("shift is hard-blocked by forecast X (cannot override)")
        return False, reasons

    # 1. זמינות מקובץ האילוצים (רך — ניתן לביטול עם allow_blocked)
    if not allow_blocked and not emp.is_available(slot.day, slot.shift_type):
        reasons.append("shift is blocked (X) for this employee")

    # 1b. עמדות בוקר ארוכות: צהריים חייבים להיות זמינים — קשיח, לא ניתן לביטול.
    # עמדות אלו נמשכות לתוך הצהריים, לכן חסימת צהריים מוחלטת כמו אילוץ רצף
    # ללא תלות ב-allow_blocked.
    # בודק גם X מקובץ האילוצים וגם X שהמנהל שם בקובץ הצפי.
    _long_morning = {p.lower().strip() for p in _cfg.POSITIONS.get("long_morning_positions", [])}
    _noon_constraints_blocked = not emp.is_available(slot.day, ShiftType.NOON)
    _noon_forecast_blocked = (slot.day, ShiftType.NOON) in emp.forecast_blocked
    if (slot.shift_type == ShiftType.MORNING
            and slot.position_name.strip().lower() in _long_morning
            and (_noon_constraints_blocked or _noon_forecast_blocked)):
        reasons.append(f"{slot.position_name} is a long morning shift — noon is blocked for this employee")

    # 2. כבר עובד באותו סלוט
    if _already_in_slot(emp, slot.day, slot.shift_type):
        reasons.append("already assigned to a different position in this slot")

    # 3. התנגשות משמרות רצופות (קשיח — לא ניתן לביטול)
    if _adjacent_conflict(emp, slot.day, slot.shift_type):
        reasons.append("adjacent-shift conflict")

    # 4. מגבלת מספר משמרות (ניתן לביטול עם allow_overload)
    if not allow_overload and _exceeds_shift_limit(emp):
        reasons.append(f"at max shifts ({emp.max_shifts})")

    # 5. מגבלת משמרות לילה
    #    - max_night_shifts == 0 → קשיח, לא ניתן לביטול (קבוצת בכירות עליונה)
    #    - max_night_shifts > 0  → ניתן להרחבה בלילה אחד נוסף עם allow_extra_night
    if _exceeds_night_limit(emp, slot.shift_type):
        if emp.max_night_shifts == 0:
            reasons.append("top-seniority employee: zero nights allowed (hard rule)")
        elif not (allow_extra_night or allow_overload):
            reasons.append(f"at night-shift limit ({emp.max_night_shifts})")

    return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# אימות כלל-לוח הזמנים (לאבחון לאחר הרצה)
# ---------------------------------------------------------------------------

def validate_schedule(
    employees: List[Employee],
    slots: List[PositionSlot],
) -> List[str]:
    """מחזיר רק הפרות אילוצים קשיחים אמיתיות.

    קטגוריות
    ----------
    קשיח (מוחזר כאן):
      - התנגשויות משמרות רצופות (אותו יום או בין ימים)
      - עובד עם req=0 שקיבל משמרות
      - סלוטים פתוחים שלא מולאו

    אזהרות קיבולת (לא מוחזרות — צפויות כשהמערכת עמוסה יתר):
      - emp.assigned_count > emp.max_shifts   (עומס שלב 3)
      - emp.night_count > emp.max_night_shifts (allow_extra_night)
    """
    issues: List[str] = []

    for emp in employees:
        # התנגשויות רצף — חוק קשיח אמיתי
        for day in range(7):
            if emp.is_working(day, ShiftType.MORNING) and emp.is_working(day, ShiftType.NOON):
                issues.append(f"{emp.name}: Morning+Noon same day (day {day})")
            if emp.is_working(day, ShiftType.NOON) and emp.is_working(day, ShiftType.NIGHT):
                issues.append(f"{emp.name}: Noon+Night same day (day {day})")
            if emp.is_working(day, ShiftType.NIGHT) and day < 6 \
                    and emp.is_working(day + 1, ShiftType.MORNING):
                issues.append(f"{emp.name}: Night(day {day}) -> Morning(day {day+1})")

        # עובד עם req=0 שקיבל משמרות שאינן נעולות (stubs פטורים —
        # ההקצאות שלהם מגיעות רק מתאי עמדות מוקצות מראש, וזה צפוי)
        if emp.required_shifts == 0 and emp.assigned_count > 0 and not emp.is_stub:
            issues.append(f"{emp.name}: req=0 (on leave) but got {emp.assigned_count} shifts")

    # סלוטים פתוחים שלא מולאו
    for slot in slots:
        if slot.is_open:
            from models import DAYS
            issues.append(
                f"Unfilled: {slot.position_name} | {slot.shift_type.value} | {DAYS[slot.day]}"
            )

    return issues


def capacity_warnings(employees: List[Employee]) -> List[str]:
    """מחזיר אזהרות קיבולת (לוח זמנים עמוס יתר, לא באגים אמיתיים)."""
    warnings: List[str] = []
    for emp in employees:
        if emp.required_shifts == 0:
            continue
        if emp.assigned_count > emp.max_shifts:
            warnings.append(
                f"{emp.name}: {emp.assigned_count} shifts assigned (max {emp.max_shifts})"
            )
        if emp.night_count > emp.max_night_shifts:
            warnings.append(
                f"{emp.name}: {emp.night_count} nights assigned (limit {emp.max_night_shifts})"
                f" — capacity overflow"
            )
    return warnings
