"""
טוענים לקבצי Excel של צפי, עמדות ואילוצים.

כל שמות העמודות, ההיסטים של השורות ומיפויי הערכים נקראים מ-config.py.
כדי להתאים לפריסת קובץ שונה, ערוך רק את config.py.

מבנה קבצים אמיתי (זוהה מקבצי דוגמה):
  צפי      : כותרת של 2 שורות (שמות ימים / שמות משמרות), נתונים משורה 2.
              עמודה 0 = שם, עמודה 1 = הערות/ותק, עמודות 2-22 = משמרות,
              עמודה 23 = משמרות נדרשות.
              שורות כותרת-סעיף (למשל "GUARDS") מכילות תא הערות לא-מספרי
              ומדולגות אוטומטית.

  עמדות   : שורה 0 = ריקה, שורה 1 = כותרת ("Post / Type / shift / days …"),
              נתונים משורה 2.
              עמודה 0 = שם עמדה, עמודה 1 = Type (מוסלקת), עמודה 2 = משמרת,
              עמודות 3-9 = 7 עמודות ימים.

  אילוצים : שורה 0 = כותרת, נתונים משורה 1.
               עמודה 0 = מספר סידורי (מוסלק), עמודה 1 = שם עובד,
               עמודות 2-8 = תא אחד לכל יום עם משמרות זמינות מופרדות בפסיקים,
               למשל "morning, noon, night" או "" (לא זמין באותו יום).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

import config
from models import Employee, PositionSlot, ShiftType, SHIFT_ORDER


# ===========================================================================
# פונקציות עזר לנרמול ברמה נמוכה
# ===========================================================================

def _norm(val) -> str:
    """מחזיר מחרוזת ללא רווחים מכל ערך תא Excel גולמי."""
    if val is None:
        return ""
    if isinstance(val, float):
        import math
        if math.isnan(val):
            return ""
        if val == int(val):
            return str(int(val))
    return str(val).strip()


def _is_closed(val: str, closed_set: Set[str]) -> bool:
    return val in closed_set


def _parse_shift_type(raw: str) -> Optional[ShiftType]:
    """ממפה מחרוזת גולמית ל-ShiftType באמצעות POSITIONS['shift_map']."""
    v = raw.lower().strip()
    for canonical, synonyms in config.POSITIONS["shift_map"].items():
        if v in [s.lower() for s in synonyms]:
            return {"morning": ShiftType.MORNING,
                    "noon":    ShiftType.NOON,
                    "night":   ShiftType.NIGHT}[canonical]
    return None


def _find_col(df: pd.DataFrame, keywords: List[str], default: int) -> int:
    """מחזיר את אינדקס העמודה 0-based שהכותרת שלה מתאימה לאחד ממילות המפתח.

    ההתאמה אינה תלויה ברישיות ומסירה רווחים מסביב.
    נופל ל-*default* אם אין כותרת מתאימה.
    ה-*default* מוחזר כמות שהוא גם אם שווה לברירת המחדל של name_col
    (האחריות על מניעת התנגשויות היא של הקוראים).
    """
    kw_set = {k.lower().strip() for k in keywords}
    for i, col in enumerate(df.columns):
        if _norm(str(col)).lower() in kw_set:
            return i
    return default


def _build_shift_block() -> List[ShiftType]:
    order = config.FORECAST.get("shift_block_order", "morning_first")
    if order == "night_first":
        return [ShiftType.NIGHT, ShiftType.MORNING, ShiftType.NOON]
    return [ShiftType.MORNING, ShiftType.NOON, ShiftType.NIGHT]


# ===========================================================================
# טוען צפי
# ===========================================================================

def load_forecast(filepath: str) -> List[Employee]:
    """טוען את קובץ Excel של הצפי.

    לקובץ יש כותרת של שתי שורות; הנתונים מתחילים ב-`config.FORECAST['data_start_row']`.
    שורות שבהן תא הותק אינו מספרי (כותרות סעיף, שורות ריקות,
    כותרות-משנה) מדולגות אוטומטית.
    """
    cfg        = config.FORECAST
    skip_vals  = {v.lower() for v in cfg["skip_seniority_values"]}
    special_cells = {s.upper() for s in cfg["special_cells"]}
    shift_order   = _build_shift_block()
    data_start    = cfg["data_start_row"]
    default_req   = config.SCHEDULING["default_required_shifts"]

    # קריאת הקובץ כולו מבלי לפרש שורה כלשהי ככותרת, כדי לשמור
    # שליטה מלאה על אילו שורות הן נתונים ואילו הן כותרות/סעיפים.
    df = pd.read_excel(filepath, header=None)

    # שימוש בשורה `data_start - 1` (שורת הכותרת האחרונה) לגזירת מיקומי עמודות.
    # זה מטפל בכותרות ממוזגות / רב-שכבתיות בצורה חלקה.
    header_row_series = df.iloc[data_start - 1] if data_start >= 1 else df.iloc[0]
    header_cols = [_norm(str(v)).lower() for v in header_row_series]

    def _col_by_keyword(keywords: List[str], default: int) -> int:
        kw_set = {k.lower() for k in keywords}
        for i, h in enumerate(header_cols):
            if h in kw_set:
                return i
        return default

    name_col      = _col_by_keyword(cfg["name_keywords"],      cfg["name_col_default"])
    seniority_col = _col_by_keyword(cfg["seniority_keywords"],  cfg["seniority_col_default"])
    required_col  = _col_by_keyword(cfg["required_keywords"],   cfg.get("required_col_default", -1))
    shift_start   = seniority_col + 1

    employees: List[Employee] = []

    for pandas_idx in range(data_start, len(df)):
        row = df.iloc[pandas_idx]

        name = _norm(row.iloc[name_col]) if name_col < len(row) else ""
        if not name:
            continue

        # דילוג שורות כותרת-סעיף: תא הותק שלהן אינו מספרי
        raw_sen = _norm(row.iloc[seniority_col]) if seniority_col < len(row) else ""
        if raw_sen.lower() in skip_vals:
            continue
        try:
            seniority = int(float(raw_sen))
        except (ValueError, TypeError):
            continue  # שורה לא תקינה של עובד

        # 21 תאי משמרת
        forecast_cells: Dict[Tuple[int, ShiftType], str] = {}
        special_count = 0
        for idx in range(21):
            col_idx = shift_start + idx
            if col_idx >= len(row):
                break
            day   = idx // 3
            shift = shift_order[idx % 3]
            val   = _norm(row.iloc[col_idx])
            forecast_cells[(day, shift)] = val
            if val.upper() in special_cells:
                special_count += 1

        # סלוטי TR — אוסף זוגות (day, shift) שבהם תא הצפי = "TR"
        training_slots = [
            (day, shift)
            for (day, shift), val in forecast_cells.items()
            if val.upper() == "TR"
        ]

        # משמרות נדרשות
        req: int
        if required_col != -1 and required_col < len(row):
            raw_req = _norm(row.iloc[required_col])
            try:
                req = int(float(raw_req))
            except (ValueError, TypeError):
                req = max(0, default_req - special_count)
        else:
            req = max(0, default_req - special_count)

        # מספר שורת Excel (1-based): pandas_idx + 1
        excel_row = pandas_idx + 1

        employees.append(Employee(
            name=name,
            seniority=seniority,
            forecast_cells=forecast_cells,
            required_shifts=req,
            training_slots=training_slots,
            excel_row=excel_row,
        ))

    return employees


# ===========================================================================
# טוען עמדות
# ===========================================================================

def load_positions(filepath: str) -> List[PositionSlot]:
    """טוען את קובץ Excel של העמדות.

    סדר השורות בקובץ קובע את עדיפות העמדה (שורה 2 = עדיפות גבוהה ביותר).
    """
    cfg        = config.POSITIONS
    data_start = cfg["data_start_row"]
    closed_set = set(cfg["closed_values"])
    open_val   = cfg["open_value"]

    df = pd.read_excel(filepath, header=None)

    # גזירת מיקומי עמודות משורת הכותרת
    header_row_idx = cfg["header_row"]
    header_series  = df.iloc[header_row_idx] if header_row_idx < len(df) else df.iloc[0]
    header_cols    = [_norm(str(v)).lower() for v in header_series]

    def _col_by_kw(keywords: List[str], default: int) -> int:
        kw_set = {k.lower() for k in keywords}
        for i, h in enumerate(header_cols):
            if h in kw_set:
                return i
        return default

    pos_col        = _col_by_kw(cfg["position_keywords"],  cfg["position_col_default"])
    shift_col      = _col_by_kw(cfg["shift_keywords"],     cfg["shift_col_default"])
    importance_col = _col_by_kw(cfg.get("importance_keywords", []),
                                cfg.get("importance_col_default", -1))
    day_start = cfg["day_start_col"]

    # בטיחות: אם shift_col מצביע לאותה עמודה כמו pos_col
    # (יכול לקרות כש-"type" מתאים בטעות), השתמש בברירת המחדל.
    if shift_col == pos_col:
        shift_col = cfg["shift_col_default"]

    # בניית מילון דרגת עדיפות מעמודת החשיבות.
    # העמודה מכילה רשימה מדורגת של שמות עמדות (לא מספרים לכל שורה):
    # העמדה בשורה data_start מקבלת דרגה 1 (הכי חשובה), דרגה 2 הבאה, וכו'.
    # אם עמדה מופיעה מספר פעמים, ההופעה הראשונה שלה מנצחת.
    priority_rank: dict = {}
    if importance_col != -1:
        rank = 1
        for pandas_idx in range(data_start, len(df)):
            raw = _norm(df.iloc[pandas_idx, importance_col]) if importance_col < df.shape[1] else ""
            if not raw:
                continue
            key = raw.lower().strip()
            if key not in priority_rank:
                priority_rank[key] = rank
            rank += 1

    slots: List[PositionSlot] = []

    for pandas_idx in range(data_start, len(df)):
        row = df.iloc[pandas_idx]

        pos_name = _norm(row.iloc[pos_col]) if pos_col < len(row) else ""
        if not pos_name:
            continue

        importance = priority_rank.get(pos_name.lower().strip(), 999)

        shift_raw  = _norm(row.iloc[shift_col]) if shift_col < len(row) else ""
        shift_type = _parse_shift_type(shift_raw)

        if shift_type is None:
            # ניסיון להסיק מתוך שם העמדה עצמה
            combined = (pos_name + " " + shift_raw).lower()
            for canonical, synonyms in cfg["shift_map"].items():
                if any(s.lower() in combined for s in synonyms):
                    shift_type = {"morning": ShiftType.MORNING,
                                  "noon":    ShiftType.NOON,
                                  "night":   ShiftType.NIGHT}[canonical]
                    break

        if shift_type is None:
            print(f"[Loader] Warning: Cannot determine shift type for "
                  f"'{pos_name}' (raw='{shift_raw}'). Row {pandas_idx + 1} skipped.")
            continue

        # מספר שורת Excel 1-based: pandas_idx + 1
        excel_row = pandas_idx + 1

        for day in range(7):
            col_idx = day_start + day
            if col_idx >= len(row):
                break
            raw_val = _norm(row.iloc[col_idx])

            if _is_closed(raw_val, closed_set):
                continue

            # בדיקה אם הערך הוא מספר (למשל "2" או "3" מציין
            # כמה עובדים נדרשים לסלוט זה).
            try:
                count = int(raw_val)
                is_numeric = True
            except (ValueError, TypeError):
                count = 1
                is_numeric = False

            if is_numeric and count >= 1:
                # יצירת `count` סלוטים פתוחים לעמדה/יום/משמרת זו
                for _ in range(count):
                    slots.append(PositionSlot(
                        position_name=pos_name,
                        shift_type=shift_type,
                        day=day,
                        raw_value="1",
                        excel_row=excel_row,
                        position_importance=importance,
                    ))
            else:
                # ערך לא-מספרי = שם עובד מוקצה מראש
                slots.append(PositionSlot(
                    position_name=pos_name,
                    shift_type=shift_type,
                    day=day,
                    raw_value=raw_val,
                    assigned_employee=raw_val,
                    locked=True,
                    excel_row=excel_row,
                    position_importance=importance,
                ))

    return slots


# ===========================================================================
# טוען אילוצים (פורמט פסיק-מופרד לכל יום)
# ===========================================================================

def _parse_available_shifts(cell_val: str) -> Set[ShiftType]:
    """מנתח תא מופרד בפסיקים לקבוצת ShiftTypes.

    תומך בשלושה פורמטים:
      - "morning, noon"              — שמות אנגליים פשוטים
      - "בוקר-בוקר, ערב-ערב"        — פורמט עברי X-X
      - "Bravo.5 בוקר-בוקר, ..."    — שם עמדה + משמרת (מחלץ רק את המשמרת)
    """
    if not cell_val.strip():
        return set()

    synonyms = config.CONSTRAINTS["shift_synonyms"]
    result: Set[ShiftType] = set()
    canonical_to_type = {
        "morning": ShiftType.MORNING,
        "noon":    ShiftType.NOON,
        "night":   ShiftType.NIGHT,
    }
    syns_lower = {c: [s.lower() for s in syns] for c, syns in synonyms.items()}

    for part in cell_val.split(","):
        part = part.strip()
        if not part:
            continue
        words = part.split()
        # ניסיון לכל חלק מהסוף להתחלה: "Bravo.5 בוקר-בוקר" → בודק "בוקר-בוקר" תחילה
        for word in reversed(words):
            token = word.lower()
            matched = False
            for canonical, syns in syns_lower.items():
                if token in syns:
                    result.add(canonical_to_type[canonical])
                    matched = True
                    break
            if matched:
                break

    return result


def _detect_day_columns(header_cols: List[str]) -> Dict[int, int]:
    """מזהה אוטומטית את עמודות הימים לפי כותרות העמודות.

    מחזיר: { day_index(0=ראשון … 6=שבת): col_index }
    עובד עם כותרות עבריות (ראשון, שני, ...) ואנגליות (SUNDAY, MONDAY, ...).
    """
    day_keywords = config.CONSTRAINTS.get("day_header_keywords", {})
    day_cols: Dict[int, int] = {}
    for day_idx, keywords in day_keywords.items():
        kw_set = {k.lower() for k in keywords}
        for col_idx, h in enumerate(header_cols):
            if h in kw_set:
                day_cols[day_idx] = col_idx
                break
    return day_cols


def load_constraints(filepath: str) -> Dict[str, Dict[Tuple[int, ShiftType], bool]]:
    """טוען את קובץ Excel של האילוצים.

    תומך בשני פורמטים (מוגדר ב-config.CONSTRAINTS['cell_format']):

    "per_day_comma_separated"  — 7 עמודות ימים, כל תא הוא רשימה מופרדת בפסיקים
        של משמרות זמינות. עמודות הימים מזוהות אוטומטית לפי הכותרת
        (ראשון/שני/... או SUNDAY/MONDAY/...), כך שניתן לטעון את הקובץ
        המקורי ישירות מבלי להסיר עמודות כמו "מס", "הערות", "סהכ".

    "per_shift_21col"          — 21 עמודות משמרת נפרדות; לא-ריק = זמין.

    מחזיר:
        { employee_name: { (day, shift): True | False } }
    """
    cfg        = config.CONSTRAINTS
    data_start = cfg["data_start_row"]
    fmt        = cfg.get("cell_format", "per_day_comma_separated")

    df = pd.read_excel(filepath, header=None)

    # גזירת עמודת השם משורת הכותרת
    header_row_idx = cfg["header_row"]
    header_series  = df.iloc[header_row_idx] if header_row_idx < len(df) else df.iloc[0]
    header_cols    = [_norm(str(v)).lower() for v in header_series]

    def _col_by_kw(keywords: List[str], default: int) -> int:
        kw_set = {k.lower() for k in keywords}
        for i, h in enumerate(header_cols):
            if h in kw_set:
                return i
        return default

    name_col = _col_by_kw(cfg["name_keywords"], cfg["name_col_default"])

    constraints: Dict[str, Dict[Tuple[int, ShiftType], bool]] = {}

    for pandas_idx in range(data_start, len(df)):
        row = df.iloc[pandas_idx]

        name = _norm(row.iloc[name_col]) if name_col < len(row) else ""
        if not name:
            continue
        # דילוג שורות דמויות-כותרת
        if name.lower() in {k.lower() for k in cfg["name_keywords"]}:
            continue

        availability: Dict[Tuple[int, ShiftType], bool] = {}

        if fmt == "per_day_comma_separated":
            # זיהוי אוטומטי של עמודות ימים לפי כותרת; נפילה לday_start_col אם לא נמצאו
            day_cols = _detect_day_columns(header_cols)
            if day_cols:
                for day in range(7):
                    col_idx = day_cols.get(day)
                    cell_val = _norm(row.iloc[col_idx]) if col_idx is not None and col_idx < len(row) else ""
                    available_shifts = _parse_available_shifts(cell_val)
                    for shift in SHIFT_ORDER:
                        availability[(day, shift)] = shift in available_shifts
            else:
                # ברירת מחדל: day_start_col קבוע
                day_start = cfg["day_start_col"]
                num_days  = cfg.get("num_day_cols", 7)
                for day in range(num_days):
                    col_idx  = day_start + day
                    cell_val = _norm(row.iloc[col_idx]) if col_idx < len(row) else ""
                    available_shifts = _parse_available_shifts(cell_val)
                    for shift in SHIFT_ORDER:
                        availability[(day, shift)] = shift in available_shifts

        else:  # per_shift_21col
            blocked_vals = {v.lower() for v in cfg["blocked_values"]}
            shift_order  = _build_shift_block()
            day_start    = cfg["day_start_col"]
            for idx in range(21):
                col_idx = day_start + idx
                if col_idx >= len(row):
                    break
                day   = idx // 3
                shift = shift_order[idx % 3]
                val   = _norm(row.iloc[col_idx])
                availability[(day, shift)] = (
                    val != "" and val.lower() not in blocked_vals
                )

        constraints[name] = availability

    return constraints


# ===========================================================================
# פונקציות עזר להעשרה לאחר טעינה
# ===========================================================================

def _normalise_name(name: str) -> str:
    """מנרמל שם להתאמה מקורבת.

    ממיר לאותיות קטנות, מסיר רווחים, מוחק סעיפים בסוגריים ו
    פיסוק כדי ש-"BISHRI (BOAZ) KESEM" יתאים ל-"BISHRI KESEM".
    """
    import re
    n = name.lower().strip()
    # הסרת כל מה שבתוך סוגריים (כולל הסוגריים עצמם)
    n = re.sub(r'\([^)]*\)', '', n)
    # הסרת תווים לא-אלפאנומריים פרט לרווחים
    n = re.sub(r"[^a-z0-9֐-׿\s]", "", n)
    # כיווץ רווחים
    n = " ".join(n.split())
    return n


def merge_into_employees(
    employees: List[Employee],
    constraints: Dict[str, Dict[Tuple[int, ShiftType], bool]],
) -> None:
    """ממזג נתוני אילוצים לאובייקטי Employee (בעצמו).

    עובדים שנעדרים מ-*constraints*:
      → has_constraints=False, כל המשמרות זמינות, שורה צהובה בפלט.
    עובדים שקיימים ב-*constraints* אך נעדרים מהצפי:
      → לא מתווספים; במקום זאת מודפסת אזהרה.

    התאמת שמות היא דו-שלבית:
      1. התאמה מדויקת (אותיות קטנות + הסרת רווחים)
      2. התאמה מנורמלת (הסרת סוגריים, מחיקת פיסוק)
    """
    # בניית מפות בדיקה מדויקות ומנורמלות
    exact_map = {e.name.strip().lower(): e for e in employees}
    norm_map  = {_normalise_name(e.name): e for e in employees}

    unmatched_constraint_names: List[str] = []

    for emp_name, avail in constraints.items():
        exact_key = emp_name.strip().lower()
        norm_key  = _normalise_name(emp_name)

        emp = exact_map.get(exact_key) or norm_map.get(norm_key)
        if emp is None:
            unmatched_constraint_names.append(emp_name)
            continue

        emp.availability    = avail
        emp.has_constraints = True

    if unmatched_constraint_names:
        print(f"[Loader] {len(unmatched_constraint_names)} constraint entries could not "
              f"be matched to a forecast employee (name mismatch):")
        for n in unmatched_constraint_names[:5]:
            print(f"  {n!r}")
        if len(unmatched_constraint_names) > 5:
            print(f"  … and {len(unmatched_constraint_names)-5} more")

    # עובדים ללא רשומת אילוצים → כל המשמרות זמינות (צהוב)
    for emp in employees:
        if not emp.has_constraints:
            emp.availability = {(d, s): True for d in range(7) for s in SHIFT_ORDER}

    # X בצפי הוא חסימה קשיחה: תמיד מבטל את קובץ האילוצים.
    for emp in employees:
        for (day, shift), val in emp.forecast_cells.items():
            if val.upper() == "X":
                emp.availability[(day, shift)] = False


def assign_night_limits(employees: List[Employee]) -> None:
    """מגדיר max_night_shifts לכל עובד בהתאם לקבוצת דרגת הותק שלו.

    מכבד את config.FORECAST['seniority_lower_means_senior']:
      True  → ערך ותק נמוך יותר = בכיר יותר → מיון עולה
      False → ערך ותק גבוה יותר = בכיר יותר → מיון יורד
    """
    groups       = config.SCHEDULING["night_limit_groups"]
    lower_senior = config.FORECAST.get("seniority_lower_means_senior", False)

    sorted_emps = sorted(
        employees,
        key=lambda e: e.seniority,
        reverse=not lower_senior,  # עולה אם lower=senior, אחרת יורד
    )

    cursor = 0
    for group_size, night_limit in groups:
        if group_size is None:
            for emp in sorted_emps[cursor:]:
                emp.max_night_shifts = night_limit
            break
        end = min(cursor + group_size, len(sorted_emps))
        for emp in sorted_emps[cursor:end]:
            emp.max_night_shifts = night_limit
        cursor = end
        if cursor >= len(sorted_emps):
            break
