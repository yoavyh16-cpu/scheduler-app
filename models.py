"""
מודלי נתונים למערכת שיבוץ כוח אדם.

קבועים מספריים (מקסימום משמרות, ימים רצופים, וכו') נקראים מ-config.py
כדי שניתן לשנות אותם מבלי לגעת בקובץ זה.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# ספירות (Enums)
# ---------------------------------------------------------------------------

class ShiftType(Enum):
    MORNING = "Morning"
    NOON    = "Noon"
    NIGHT   = "Night"


SHIFT_ORDER: List[ShiftType] = [ShiftType.MORNING, ShiftType.NOON, ShiftType.NIGHT]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


# ---------------------------------------------------------------------------
# ציון קושי (קורא אינדקסי ימים מ-config בעת ייבוא)
# ---------------------------------------------------------------------------

def shift_difficulty(day: int, shift: ShiftType) -> int:
    """מחזיר ציון עדיפות לתזמון. גבוה יותר → קשה יותר → מתוזמן ראשון."""
    import config as _cfg
    s = _cfg.SCHEDULING
    thu, fri, sat = s["thursday_idx"], s["friday_idx"], s["saturday_idx"]

    if day == thu and shift == ShiftType.NIGHT:
        return 1000
    if day in (fri, sat):
        if shift == ShiftType.NIGHT:
            return 730
        if shift == ShiftType.NOON:
            return 720
        return 710
    if shift == ShiftType.NIGHT:
        return 500
    if shift == ShiftType.NOON:
        return 300
    return 100


# ---------------------------------------------------------------------------
# מחלקות נתוני ליבה
# ---------------------------------------------------------------------------

@dataclass
class Employee:
    name: str
    seniority: int                  # ערך מספרי גבוה יותר = בכיר יותר

    # ערכי תאי צפי גולמיים עם מפתח (day_index, ShiftType)
    # ערכים: "" | "AL" | "SLA" | "SLE" | טקסט שרירותי
    forecast_cells: Dict[Tuple[int, ShiftType], str] = field(default_factory=dict)

    required_shifts: int = 5        # מוחלף על ידי data_loader מברירות המחדל של config
    max_shifts:      int = 7

    # True  = עובד יכול לעבוד בסלוט זה
    # False = חסום (קובץ האילוצים ציין אותו כלא זמין) — אילוץ רך
    availability: Dict[Tuple[int, ShiftType], bool] = field(default_factory=dict)
    has_constraints: bool = False   # False → לא הותאם לקובץ האילוצים → צהוב בפלט
    matched_constraints: bool = False  # True רק אם הותאם בהצלחה לקובץ האילוצים

    # X בקובץ הצפי — חסימה קשיחה שלא ניתן לבטל (בניגוד ל-X בקובץ האילוצים)
    forecast_blocked: Set[Tuple[int, ShiftType]] = field(default_factory=set)

    # טאפלים של (day, shift_type, position_name) שנוספו על ידי המתזמן
    assigned: List[Tuple[int, ShiftType, str]] = field(default_factory=list)

    # מוגדר על ידי assign_night_limits() לפני תחילת התזמון
    max_night_shifts: int = 1

    # סלוטים שבהם הוקצה עובד למרות אילוץ חסום
    violations: List[Tuple[int, ShiftType]] = field(default_factory=list)

    # זוגות (day, shift) שבהם TR מופיע בצפי
    training_slots: List[Tuple[int, ShiftType]] = field(default_factory=list)

    # הקצאות שהן OT (מעבר ל-required_shifts) — מוגדר על ידי המתזמן
    ot_assignments: List[Tuple[int, ShiftType, str]] = field(default_factory=list)

    # True לעובדים שנוצרו מתאי עמדות מוקצות מראש שאין להם
    # רשומה תואמת בקובץ הצפי (עובדי stub).
    is_stub: bool = False

    # שורת Excel 1-based בחוברת הצפי (מוגדרת על ידי data_loader)
    excel_row: int = 0

    # ------------------------------------------------------------------
    @property
    def assigned_count(self) -> int:
        return len(self.assigned)

    @property
    def night_count(self) -> int:
        return sum(1 for _, s, _ in self.assigned if s == ShiftType.NIGHT)

    def is_working(self, day: int, shift: ShiftType) -> bool:
        return any(d == day and s == shift for d, s, _ in self.assigned)

    def working_days(self) -> List[int]:
        return sorted({d for d, _, _ in self.assigned})

    def needs_more_shifts(self) -> bool:
        return self.assigned_count < self.required_shifts

    @property
    def has_al_or_sle(self) -> bool:
        special = {"AL", "SLE"}
        return any(v.upper() in special for v in self.forecast_cells.values())

    def is_available(self, day: int, shift: ShiftType) -> bool:
        if not self.has_constraints:
            return True
        return self.availability.get((day, shift), False)


@dataclass
class PositionSlot:
    position_name: str
    shift_type:    ShiftType
    day:           int          # 0 = Sunday … 6 = Saturday (ניתן להגדרה ב-config.py)
    raw_value:     str          # "0" | "1" | <employee_name>

    assigned_employee:   Optional[str] = None
    locked:              bool          = False  # True לתאים מוקצים מראש
    is_ot:               bool          = False  # True כאשר ההקצאה היא שעות נוספות

    # מספר שורת Excel 1-based בחוברת העמדות (מוגדר על ידי data_loader).
    # משמש ל-output writer לאיתור התא המדויק לעדכון.
    excel_row: int = 0

    # דרגת חשיבות עמדה מעמודת "importantncy" (1 = החשובה ביותר).
    # 999 = לא ידוע / לא קיים בעמודה.
    position_importance: int = 999

    @property
    def is_open(self) -> bool:
        return not self.locked and self.assigned_employee is None

    @property
    def difficulty(self) -> int:
        return shift_difficulty(self.day, self.shift_type)
