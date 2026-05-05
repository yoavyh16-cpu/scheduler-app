"""
מנוע תזמון ליבה — חמדני עם מנגנון נפילה לאילוצים רכים.

סקירת האלגוריתם
------------------
שלב 1  הקצאה מוקדמת של סלוטים נעולים (תאי קובץ העמדות עם שם עובד).
שלב 2  תזמון המשמרות הקשות ביותר תחילה (ממוינות לפי ציון shift_difficulty):
           א. משמרות לילה — מחולקות עם עדיפות "פיזור"
              (לתת לכולם לילה 1 לפני שמישהו מקבל 2, וכו')
           ב. כל שאר המשמרות — חמדני, העובד הנצרך ביותר ראשון
שלב 3  מילוי סלוטים שנותרו פתוחים לאחר שלב 2.
שלב 4  טיפול בעובדים מתחת למכסת המשמרות הנדרשת שלהם (מילוי מחסור).

סדר בחירת מועמדים
--------------------------
מועמדים רגילים (ללא הפרה):
  מיון לפי (need יורד, night_count עולה ללילות, ותק: צעיר ראשון ללילות / בכיר ראשון לסלוטים טובים)
מועמדים עם הפרה (ביטול אילוץ חסום):
  מיון לפי (ותק עולה — הצעיר ביותר סופג את ההפרה)
מועמדים חירום (התעלמות ממגבלות משמרת/לילה):
  כמו הפרה, אך גם מבטל מכסות משמרות ולילה.
"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple

from constraints import can_assign, _tr_conflict
import config as _cfg
from models import (
    DAYS,
    SHIFT_ORDER,
    Employee,
    PositionSlot,
    ShiftType,
    shift_difficulty,
)


class Scheduler:
    def __init__(self, employees: List[Employee], slots: List[PositionSlot]) -> None:
        # אינדוקס עובדים לפי שם (אותיות קטנות) לחיפוש מהיר
        self.employees: Dict[str, Employee] = {e.name: e for e in employees}
        self.slots = slots
        # יומן של (employee_name, day, shift_type) עבור ביטולי אילוצים רכים
        self.violations: List[Tuple[str, int, ShiftType]] = []
        self._build_seniority_data(employees, slots)

    def _build_seniority_data(
        self, employees: List[Employee], slots: List[PositionSlot]
    ) -> None:
        """חישוב מוקדם של דרגות ותק והקצאות קבוצות חשיבות.

        אנו מחלקים עובדים (ממוינים מהבכיר ביותר → הצעיר ביותר) לקבוצות
        שמתאימות לרמות החשיבות המדורגות. הקבוצה הבכירה ביותר
        מועדפת לעמדות חשיבות 1, הקבוצה הבאה לחשיבות 2,
        וכך הלאה. בשימוש ב-_pick_normal_candidate כדי שהשכבה הנכונה
        תמיד תהיה הבחירה הראשונה לכל רמת חשיבות.
        """
        lower_senior = _cfg.FORECAST.get("seniority_lower_means_senior", False)
        # הוצאת stubs (ללא שורת צפי) מדירוג הותק
        real_emps = [e for e in employees if not e.is_stub]
        sorted_emps = sorted(
            real_emps,
            key=lambda e: e.seniority,
            reverse=not lower_senior,  # הבכיר ביותר ראשון
        )

        # דרגת ותק: 1 = הבכיר ביותר
        self._seniority_rank: Dict[str, int] = {
            e.name: i + 1 for i, e in enumerate(sorted_emps)
        }
        n_emps = len(sorted_emps)

        # רמות חשיבות ייחודיות ממוינות בסדר עולה (הכי חשוב ראשון)
        imp_levels = sorted(
            set(s.position_importance for s in slots if s.position_importance < 999)
        )
        n_levels = len(imp_levels)

        # מיפוי רמת חשיבות → האינדקס 0-based שלה (לחישוב אי-התאמה)
        self._imp_level_index: Dict[int, int] = {lv: i for i, lv in enumerate(imp_levels)}
        self._n_importance_levels: int = n_levels
        self._n_real_employees: int = n_emps

        # הקצאת כל עובד לקבוצת חשיבות אחת בדיוק
        # (הבכיר ביותר → importance-level[0], הצעיר ביותר → importance-level[-1])
        self._importance_group: Dict[str, int] = {}
        if n_levels > 0 and n_emps > 0:
            for i, emp in enumerate(sorted_emps):
                level_idx = min(int(i * n_levels / n_emps), n_levels - 1)
                self._importance_group[emp.name] = imp_levels[level_idx]

    # -----------------------------------------------------------------------
    # נקודת כניסה ראשית
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """מריץ את צינור התזמון המלא."""
        # שלב 1
        self._apply_locked_slots()

        # הפרדת סלוטים פתוחים לדליי לילה ולא-לילה,
        # שניהם ממוינים לפי קושי יורד
        open_slots = [s for s in self.slots if s.is_open]
        # מיון: עמדות חשובות ביותר ראשון, אחר כך קושי יום/משמרת באותה חשיבות.
        # מבטיח שעובדים בכירים (עם עדיפות בחירה) מוקצים תמיד
        # לעמדות בעלות החשיבות הגבוהה ביותר ללא תלות בקושי היום.
        night_slots = sorted(
            [s for s in open_slots if s.shift_type == ShiftType.NIGHT],
            key=lambda s: (s.position_importance, -s.difficulty),
        )
        other_slots = sorted(
            [s for s in open_slots if s.shift_type != ShiftType.NIGHT],
            key=lambda s: (s.position_importance, -s.difficulty),
        )

        # שלב 2א — לילות (חלוקה מפוזרת)
        self._assign_nights(night_slots)

        # שלב 2ב — שאר הסלוטים
        self._assign_slots(other_slots)

        # שלב 3 — כל הסלוטים שעדיין פתוחים (מעבר שני עם חוקים מרופים)
        self._fill_remaining()

        # שלב 4 — ערבוב מחדש: מילוי סלוטים פתוחים על ידי העברת עובד מלא
        #           לסלוט הפתוח ומילוי חוזר של הסלוט שהתפנה
        self._fill_by_reshuffle()

        # שלב 5 — מילוי עובדים מתחת למכסה (דרישה קשיחה)
        self._fill_underquota()

        # שלב 5ב — שרשרת 4-כיוונית עמוקה יותר לעובדים שעדיין מתחת למכסה
        self._fill_underquota_deep()

        # שלב 5ג — מוצא אחרון: כפיית מילוי עובדים מתחת למכסה עם הפרות רכות
        self._fill_underquota_force()

        # שלב 6 — החלפת הפרות כאשר קיימת החלפה נקייה
        self._swap_violations()

        # שלב 6ב — העברת הפרות שנותרו מעובדים בכירים לצעירים
        self._migrate_violations_to_juniors()

        # שלב 7 — בתוך כל (day, shift), מיון מחדש כך שעמדות חשובות יותר
        #           תמיד מכילות עובדים בכירים יותר
        self._sort_within_shifts()

        # שלב 8 — חישוב מחדש של דגלי OT לאחר שכל השלבים התייצבו
        self._recompute_ot()

        # שלב 9 — רישום עובדים מתחת למכסה (אמור להיות ריק)
        self._report_shortage()

    # -----------------------------------------------------------------------
    # שלב 1
    # -----------------------------------------------------------------------

    def _apply_locked_slots(self) -> None:
        """רושם עובדים מוקצים מראש ברשימת Employee.assigned שלהם."""
        for slot in self.slots:
            if not slot.locked:
                continue
            emp_name = slot.assigned_employee or ""
            emp = self.employees.get(emp_name)
            if emp is None:
                # עובד מופיע בקובץ העמדות אך לא בצפי → יצירת stub.
                # required_shifts=0 / max_shifts=0 מונע מהמתזמן להקצות
                # stub זה לכל סלוט נוסף (שאינו נעול).
                emp = Employee(
                    name=emp_name,
                    seniority=0,
                    required_shifts=0,
                    max_shifts=0,
                    is_stub=True,
                    has_constraints=False,
                    availability={(d, s): True for d in range(7) for s in SHIFT_ORDER},
                )
                self.employees[emp_name] = emp
            if not emp.is_working(slot.day, slot.shift_type):
                emp.assigned.append((slot.day, slot.shift_type, slot.position_name))
                # רישום הפרה אם הסלוט הנעול מתנגש עם זמינות
                if emp.has_constraints and not emp.is_available(slot.day, slot.shift_type):
                    emp.violations.append((slot.day, slot.shift_type))
                    self.violations.append((emp.name, slot.day, slot.shift_type))

    # -----------------------------------------------------------------------
    # שלב 2א — משמרות לילה עם חלוקה מפוזרת
    # -----------------------------------------------------------------------

    def _assign_nights(self, night_slots: List[PositionSlot]) -> None:
        """מקצה לילות תוך פיזור שווה בין עובדים זכאים.

        לוגיקה: כאשר מספר מועמדים זכאים, מועדף זה שיש לו
        פחות לילות עד כה (ומכסת הלילה שלו גבוהה מספיק לקבל עוד).
        זה מיישם באופן טבעי "לתת לכולם לילה 1 לפני שמישהו מקבל 2".
        """
        for slot in night_slots:
            if not slot.is_open:
                continue
            self._try_assign(slot, night_spread=True)

    # -----------------------------------------------------------------------
    # שלב 2ב — סלוטים שאינם לילה
    # -----------------------------------------------------------------------

    def _assign_slots(self, slots: List[PositionSlot]) -> None:
        for slot in slots:
            if not slot.is_open:
                continue
            self._try_assign(slot)

    # -----------------------------------------------------------------------
    # שלב 3 — מעבר שני לסלוטים שעדיין פתוחים
    # -----------------------------------------------------------------------

    def _fill_remaining(self) -> None:
        """מנסה קשה יותר עבור סלוטים שנותרו פתוחים לאחר המעבר הראשי.

        סלוטי לילה: ניסיון allow_extra_night תחילה (לילה אחד נוסף לצעירים,
        לעולם לא מבטל כלל אפס-לילה לבכירות עליונה).
        סלוטים שאינם לילה: ניסיון allow_overload (משמרות נוספות) כמוצא אחרון.
        req=0 ואילוצי רצף לעולם אינם מבוטלים.
        """
        remaining = [s for s in self.slots if s.is_open]
        if not remaining:
            return
        print(f"[Scheduler] Phase 3: {len(remaining)} slot(s) still open, running second pass.")

        def _ot_priority(s: PositionSlot) -> int:
            """נמוך יותר = מעובד ראשון כאשר נדרשים עובדי OT.
            עדיפות לבוקרי ימי חול ל-OT, אחר כך Friday morning, אחר כך noon, אחר כך night.
            סלוטי OT אסורים (Friday noon/night, Saturday) מעובדים אחרונים."""
            fri, sat = _cfg.SCHEDULING["friday_idx"], _cfg.SCHEDULING["saturday_idx"]
            if (s.day, s.shift_type) in (
                (fri, ShiftType.NOON), (fri, ShiftType.NIGHT),
                (sat, ShiftType.MORNING), (sat, ShiftType.NOON), (sat, ShiftType.NIGHT),
            ):
                return 9
            if s.shift_type == ShiftType.MORNING and s.day != sat:
                return 1 if s.day != fri else 2
            if s.shift_type == ShiftType.NOON:
                return 3
            return 4  # Night

        remaining.sort(key=_ot_priority)

        for slot in remaining:
            if not slot.is_open:
                continue

            if slot.shift_type == ShiftType.NIGHT:
                # שלב 1: אפשר לילה אחד נוסף (לצעירים בלבד, לא top-10)
                candidates = self._get_candidates(slot, allow_blocked=True,
                                                  allow_extra_night=True)
                if candidates:
                    best = self._pick_violation_candidate(candidates)
                    self._do_assign(slot, best,
                                    violation=not best.is_available(slot.day, slot.shift_type),
                                    overload=False)
                    continue
                # שלב 2: אפשר הפרת חסימה — אך max_shifts=7 הוא מכסה קשיחה
                candidates = self._get_candidates(slot, allow_blocked=True,
                                                  allow_extra_night=True)
                if candidates:
                    best = self._pick_violation_candidate(candidates)
                    self._do_assign(slot, best, violation=True, overload=False)
                    continue
            else:
                # שאינו לילה: אפשר הפרת חסימה — max_shifts=7 הוא מכסה קשיחה
                candidates = self._get_candidates(slot, allow_blocked=True)
                if candidates:
                    best = self._pick_violation_candidate(candidates)
                    self._do_assign(slot, best, violation=True, overload=False)
                    continue

            print(f"[Scheduler] WARNING: Could not fill "
                  f"{slot.position_name} | {slot.shift_type.value} | {DAYS[slot.day]}")

    # -----------------------------------------------------------------------
    # שלב 4 — החלפת הפרות
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # שלב 5 — מילוי עובדים מתחת למכסה
    # -----------------------------------------------------------------------

    def _transfer_slot(self, slot: PositionSlot,
                       from_emp: Employee, to_emp: Employee) -> None:
        """העברת הקצאת סלוט מ-from_emp ל-to_emp, כולל עדכון כל המצב."""
        d, s, p = slot.day, slot.shift_type, slot.position_name
        slot.assigned_employee = to_emp.name
        from_emp.assigned.remove((d, s, p))
        if (d, s, p) in from_emp.ot_assignments:
            from_emp.ot_assignments.remove((d, s, p))
        to_emp.assigned.append((d, s, p))
        if to_emp.assigned_count > to_emp.required_shifts:
            to_emp.ot_assignments.append((d, s, p))
            slot.is_ot = True
        else:
            slot.is_ot = False

    def _fill_underquota(self) -> None:
        """מבטיח שכל עובד מגיע ל-required_shifts (דרישה קשיחה).

        אסטרטגיה א — העברת OT ישירה:
          emp_b יש לו יותר משמרות מהנדרש → מסירת אחד מסלוטיו ל-emp_a
          (emp_b נשאר במכסה שלו או מעליה).

        אסטרטגיה ב — שרשרת 3-כיוונית:
          emp_a לוקח סלוט מ-emp_b, emp_b לוקח סלוט מ-emp_c (שיש לו OT).
          מכסה מקרים שבהם אין סלוט OT ישיר שמתאים ל-emp_a.

        אסטרטגיה ג (מוצא אחרון — יוצרת הפרה):
          אפשר הקצאת סלוט חסום ל-emp_a אם אין פתרון נקי.
        """
        improved = True
        while improved:
            improved = False
            under = [e for e in self.employees.values()
                     if e.assigned_count < e.required_shifts]
            if not under:
                break

            for emp_a in under:
                # אסטרטגיה א: איתור סלוט OT הטוב ביותר מבין כל עובדי ה-OT.
                # "הטוב ביותר" = סלוט שחשיבות עמדתו הכי קרובה לקבוצת
                # emp_a, כדי שעובדים בכירים לא ינחתו בעמדות נמוכת-עדיפות
                # רק כדי למלא את מכסתם.
                emp_a_group = self._importance_group.get(emp_a.name, 999)

                all_candidates: List[Tuple[int, "PositionSlot", "Employee"]] = []
                for emp_b in list(self.employees.values()):
                    if emp_b is emp_a:
                        continue
                    if emp_b.assigned_count <= emp_b.required_shifts:
                        continue
                    for d, sh, pos in list(emp_b.assigned):
                        slot = next(
                            (s for s in self.slots
                             if s.assigned_employee == emp_b.name
                             and s.day == d and s.shift_type == sh
                             and s.position_name == pos),
                            None,
                        )
                        if slot is None or slot.locked:
                            continue
                        ok, _ = can_assign(emp_a, slot)
                        if not ok:
                            continue
                        imp = slot.position_importance
                        dist = abs(imp - emp_a_group) if imp < 999 else 998
                        all_candidates.append((dist, slot, emp_b))

                if all_candidates:
                    all_candidates.sort(key=lambda x: x[0])
                    _, best_slot, best_emp_b = all_candidates[0]
                    d, sh = best_slot.day, best_slot.shift_type
                    self._transfer_slot(best_slot, best_emp_b, emp_a)
                    print(f"[Scheduler] UnderQuota(A): {emp_a.name} "
                          f"<- {DAYS[d]} {sh.value} from {best_emp_b.name}")
                    improved = True
                    break

                # אסטרטגיה ב: שרשרת 3-כיוונית — emp_a <- slot_x(emp_b) <- slot_y(emp_c, יש OT)
                for slot_x in self.slots:
                    if slot_x.locked or not slot_x.assigned_employee:
                        continue
                    emp_b = self.employees.get(slot_x.assigned_employee)
                    if emp_b is None or emp_b is emp_a:
                        continue
                    if not can_assign(emp_a, slot_x)[0]:
                        continue
                    # איתור slot_y: emp_c יש לו OT ו-emp_b יכול לעבוד slot_y
                    for slot_y in self.slots:
                        if slot_y.locked or not slot_y.assigned_employee:
                            continue
                        if slot_y is slot_x:
                            continue
                        emp_c = self.employees.get(slot_y.assigned_employee)
                        if emp_c is None or emp_c is emp_a or emp_c is emp_b:
                            continue
                        if emp_c.assigned_count <= emp_c.required_shifts:
                            continue  # ל-emp_c אין OT לתת
                        # emp_b חייב להיות מסוגל לקחת slot_y
                        # (הסרת slot_x זמנית מ-emp_b לסימולציה של ההחלפה)
                        if not self._can_swap_into(emp_b, slot_x, slot_y):
                            continue
                        # ביצוע השרשרת: emp_c מאבד slot_y → emp_b עובר לשם → emp_a לוקח slot_x
                        self._transfer_slot(slot_y, emp_c, emp_b)
                        self._transfer_slot(slot_x, emp_b, emp_a)
                        print(f"[Scheduler] UnderQuota(B): {emp_a.name} "
                              f"<- {DAYS[slot_x.day]} {slot_x.shift_type.value} ({emp_b.name} "
                              f"<- {DAYS[slot_y.day]} {slot_y.shift_type.value} from {emp_c.name})")
                        improved = True
                        break
                    if improved:
                        break
                if improved:
                    break

                if improved:
                    break

    def _fill_underquota_deep(self) -> None:
        """שלב 5ב: שרשרת 4-כיוונית לעובדים שעדיין מתחת למכסה לאחר שלב 5.

        שרשרת: emp_a <- slot_x(emp_b) <- slot_y(emp_c) <- slot_z(emp_d, OT)
        ל-emp_d חייב להיות OT; כל שלב מאומת דרך _can_swap_into.
        רץ רק עבור עובדים שנותרים מתחת ל-required_shifts.
        """
        still_under = [e for e in self.employees.values()
                       if e.assigned_count < e.required_shifts]
        if not still_under:
            return

        for emp_a in still_under:
            while emp_a.assigned_count < emp_a.required_shifts:
                found = False
                for slot_x in self.slots:
                    if slot_x.locked or not slot_x.assigned_employee:
                        continue
                    if not can_assign(emp_a, slot_x)[0]:
                        continue
                    emp_b = self.employees.get(slot_x.assigned_employee)
                    if emp_b is None or emp_b is emp_a:
                        continue

                    for slot_y in self.slots:
                        if slot_y is slot_x or slot_y.locked or not slot_y.assigned_employee:
                            continue
                        emp_c = self.employees.get(slot_y.assigned_employee)
                        if emp_c is None or emp_c is emp_a or emp_c is emp_b:
                            continue
                        if not self._can_swap_into(emp_b, slot_x, slot_y):
                            continue

                        for slot_z in self.slots:
                            if slot_z is slot_x or slot_z is slot_y or slot_z.locked:
                                continue
                            if not slot_z.assigned_employee:
                                continue
                            emp_d = self.employees.get(slot_z.assigned_employee)
                            if emp_d is None or emp_d is emp_a or emp_d is emp_b or emp_d is emp_c:
                                continue
                            if emp_d.assigned_count <= emp_d.required_shifts:
                                continue
                            if not self._can_swap_into(emp_c, slot_y, slot_z):
                                continue

                            self._transfer_slot(slot_z, emp_d, emp_c)
                            self._transfer_slot(slot_y, emp_c, emp_b)
                            self._transfer_slot(slot_x, emp_b, emp_a)
                            print(
                                f"[Optimizer] DeepChain: {emp_a.name} <- "
                                f"{DAYS[slot_x.day]} {slot_x.shift_type.value} "
                                f"({emp_b.name} <- {DAYS[slot_y.day]} {slot_y.shift_type.value}, "
                                f"{emp_c.name} <- {DAYS[slot_z.day]} {slot_z.shift_type.value}, "
                                f"{emp_d.name} -OT)"
                            )
                            found = True
                            break
                        if found:
                            break
                    if found:
                        break
                if not found:
                    break

    def _fill_underquota_force(self) -> None:
        """שלב 5ג: מילוי מוצא אחרון באמצעות הפרות רכות.

        נקרא לאחר מיצוי כל העברות השרשרת הנקיות. מוצא עובד OT
        שסלוטו emp_a יכול לעבוד גם אם הוא חסום בזמינות שלו (allow_blocked=True).
        אילוצים קשיחים (רצף, TR, כפל-יום, req=0) לעולם אינם מבוטלים.
        מעדיף את הסלוט שדרגת חשיבותו הכי קרובה לקבוצת emp_a.
        """
        still_under = [e for e in self.employees.values()
                       if e.assigned_count < e.required_shifts]
        if not still_under:
            return

        improved = True
        while improved:
            improved = False
            for emp_a in [e for e in self.employees.values()
                          if e.assigned_count < e.required_shifts]:
                emp_a_group = self._importance_group.get(emp_a.name, 999)
                candidates: List[Tuple[int, PositionSlot, Employee]] = []

                for emp_b in self.employees.values():
                    if emp_b is emp_a:
                        continue
                    if emp_b.assigned_count <= emp_b.required_shifts:
                        continue  # ל-emp_b אין OT לתת
                    for d, sh, pos in list(emp_b.assigned):
                        slot = next(
                            (s for s in self.slots
                             if s.assigned_employee == emp_b.name
                             and s.day == d and s.shift_type == sh
                             and s.position_name == pos),
                            None,
                        )
                        if slot is None or slot.locked:
                            continue
                        ok, _ = can_assign(emp_a, slot, allow_blocked=True)
                        if not ok:
                            continue
                        imp = slot.position_importance
                        dist = abs(imp - emp_a_group) if imp < 999 else 998
                        candidates.append((dist, slot, emp_b))

                if not candidates:
                    continue

                candidates.sort(key=lambda x: x[0])
                _, best_slot, best_emp_b = candidates[0]
                d, sh = best_slot.day, best_slot.shift_type
                is_violation = not emp_a.is_available(d, sh)
                self._transfer_slot(best_slot, best_emp_b, emp_a)
                if is_violation:
                    emp_a.violations.append((d, sh))
                    self.violations.append((emp_a.name, d, sh))
                print(
                    f"[Scheduler] UnderQuota(C): {emp_a.name} "
                    f"<- {DAYS[d]} {sh.value} from {best_emp_b.name}"
                    + (" [soft violation]" if is_violation else "")
                )
                improved = True

    # -----------------------------------------------------------------------
    # שלב 4 — ערבוב מחדש למילוי סלוטים פתוחים
    # -----------------------------------------------------------------------

    def _fill_by_reshuffle(self) -> None:
        """מלא סלוטים פתוחים על ידי העברת עובד מלא (max-shifts) לסלוט הפתוח
        ומילוי חוזר של הסלוט שהתפנה עם עובד אחר.

        לכל סלוט פתוח:
          1. איתור עובד A (במקסימום משמרות) שיכול לעבוד בסלוט הפתוח
             לאחר הסרת אחת מהקצאותיו הקיימות זמנית.
          2. איתור עובד B (מתחת למקסימום) שיכול לקחת את הסלוט שהתפנה של A.
          3. ביצוע: A עובר לסלוט הפתוח, B לוקח את הסלוט הישן של A.
        """
        for open_slot in [s for s in self.slots if s.is_open]:
            filled = False
            for emp_a in list(self.employees.values()):
                if emp_a.assigned_count < emp_a.max_shifts:
                    continue  # לא מלא — שלב 3 אמור היה לטפל בזה

                for release_day, release_shift, release_pos in list(emp_a.assigned):
                    release_slot = next(
                        (s for s in self.slots
                         if s.assigned_employee == emp_a.name
                         and s.day == release_day and s.shift_type == release_shift
                         and s.position_name == release_pos),
                        None,
                    )
                    if release_slot is None or release_slot.locked:
                        continue

                    # האם emp_a יכול לעבוד open_slot לאחר שחרור release_slot?
                    if not self._can_swap_into(emp_a, release_slot, open_slot):
                        continue

                    # איתור emp_b שיכול למלא את release_slot; עדיפות לנצרך ביותר
                    eligible_b = [
                        cand for cand in self.employees.values()
                        if cand is not emp_a and can_assign(cand, release_slot)[0]
                    ]
                    if not eligible_b:
                        continue
                    eligible_b.sort(
                        key=lambda e: -(e.required_shifts - e.assigned_count)
                    )
                    emp_b = eligible_b[0]

                    # ביצוע הערבוב מחדש
                    release_slot.assigned_employee = emp_b.name
                    emp_a.assigned.remove((release_day, release_shift, release_pos))
                    emp_b.assigned.append((release_day, release_shift, release_pos))

                    self._do_assign(open_slot, emp_a)
                    print(f"[Scheduler] Reshuffle: {emp_a.name} -> "
                          f"{open_slot.position_name} {DAYS[open_slot.day]} "
                          f"{open_slot.shift_type.value} "
                          f"(vacated {DAYS[release_day]} {release_shift.value} -> {emp_b.name})")
                    filled = True
                    break
                if filled:
                    break

    def _can_swap_into(self, emp: Employee, release: PositionSlot,
                       target: PositionSlot) -> bool:
        """בודק האם emp יכול לקחת target לאחר שחרור release.

        מסיר זמנית את release מ-emp.assigned כדי ש-can_assign יראה
        את המצב לאחר ההחלפה. כל האילוצים (TR, רצף, לילות, וכו')
        נבדקים דרך can_assign — מקור יחיד של אמת.
        """
        entry = (release.day, release.shift_type, release.position_name)
        emp.assigned.remove(entry)
        try:
            ok, _ = can_assign(emp, target, allow_blocked=False, allow_overload=True)
        finally:
            emp.assigned.append(entry)
        return ok

    def _swap_violations(self) -> None:
        """מבטל הפרות על ידי החלפת זוגות עובדים מוקצים כבר.

        לכל הפרה (emp_a מוקצה לסלוט חסום), מוצא emp_b
        כך ששני העובדים עוברים בדיקות can_assign לאחר ההחלפה.
        חוזר עד שאין יותר החלפות אפשריות.
        """
        def _clear_vacated_violation(emp: Employee, day: int, shift: ShiftType) -> None:
            """מסיר רשומת הפרה אם העובד מפנה את הסלוט."""
            if (day, shift) in emp.violations:
                emp.violations.remove((day, shift))
                try:
                    self.violations.remove((emp.name, day, shift))
                except ValueError:
                    pass

        swapped = True
        while swapped:
            swapped = False
            for v_emp_name, v_day, v_shift in list(self.violations):
                emp_a = self.employees.get(v_emp_name)
                if emp_a is None:
                    continue
                slot_a = next(
                    (s for s in self.slots
                     if s.assigned_employee == v_emp_name
                     and s.day == v_day and s.shift_type == v_shift),
                    None,
                )
                if slot_a is None or slot_a.locked:
                    continue

                found_this = False

                # --- החלפה דו-כיוונית ---
                for slot_b in self.slots:
                    if slot_b is slot_a or slot_b.locked or not slot_b.assigned_employee:
                        continue
                    emp_b_name = slot_b.assigned_employee
                    emp_b = self.employees.get(emp_b_name)
                    if emp_b is None:
                        continue
                    # אל תמיר הפרה בלילה נוסף: אם emp_a יקבל לילה
                    # שהוא כבר מעבר למכסתו, דלג על החלפה זו.
                    if (slot_b.shift_type == ShiftType.NIGHT
                            and v_shift != ShiftType.NIGHT
                            and emp_a.max_night_shifts > 0
                            and emp_a.night_count >= emp_a.max_night_shifts):
                        continue
                    if not self._can_swap_into(emp_a, slot_a, slot_b):
                        continue
                    if not self._can_swap_into(emp_b, slot_b, slot_a):
                        continue

                    slot_a.assigned_employee = emp_b_name
                    slot_b.assigned_employee = v_emp_name
                    emp_a.assigned.remove((v_day, v_shift, slot_a.position_name))
                    emp_a.assigned.append((slot_b.day, slot_b.shift_type, slot_b.position_name))
                    emp_b.assigned.remove((slot_b.day, slot_b.shift_type, slot_b.position_name))
                    emp_b.assigned.append((v_day, v_shift, slot_a.position_name))

                    emp_a.violations.remove((v_day, v_shift))
                    self.violations.remove((v_emp_name, v_day, v_shift))
                    _clear_vacated_violation(emp_b, slot_b.day, slot_b.shift_type)

                    print(f"[Scheduler] Swap: {v_emp_name} "
                          f"({DAYS[v_day]} {v_shift.value} -> "
                          f"{DAYS[slot_b.day]} {slot_b.shift_type.value}) "
                          f"<-> {emp_b_name} "
                          f"({DAYS[slot_b.day]} {slot_b.shift_type.value} -> "
                          f"{DAYS[v_day]} {v_shift.value})")
                    swapped = True
                    found_this = True
                    break

                if found_this:
                    continue  # הפרה הבאה

                # --- סיבוב 3-כיוני: emp_a→slot_b, emp_b→slot_c, emp_c→slot_a ---
                # מבטל את ההפרה של emp_a מבלי ליצור חדשות.
                for slot_b in self.slots:
                    if slot_b is slot_a or slot_b.locked or not slot_b.assigned_employee:
                        continue
                    emp_b_name = slot_b.assigned_employee
                    emp_b = self.employees.get(emp_b_name)
                    if emp_b is None or emp_b_name == v_emp_name:
                        continue
                    # אותה בדיקת מגבלת לילה כמו בענף הדו-כיוני
                    if (slot_b.shift_type == ShiftType.NIGHT
                            and v_shift != ShiftType.NIGHT
                            and emp_a.max_night_shifts > 0
                            and emp_a.night_count >= emp_a.max_night_shifts):
                        continue
                    # emp_a חייב לקחת slot_b בצורה נקייה (ללא הפרת זמינות)
                    if not self._can_swap_into(emp_a, slot_a, slot_b):
                        continue

                    for slot_c in self.slots:
                        if (slot_c is slot_a or slot_c is slot_b
                                or slot_c.locked or not slot_c.assigned_employee):
                            continue
                        emp_c_name = slot_c.assigned_employee
                        emp_c = self.employees.get(emp_c_name)
                        if emp_c is None or emp_c_name in (v_emp_name, emp_b_name):
                            continue
                        # emp_b חייב לקחת slot_c בצורה נקייה לאחר שחרור slot_b
                        if not self._can_swap_into(emp_b, slot_b, slot_c):
                            continue
                        # emp_c חייב לקחת slot_a (סלוט ההפרה המקורי) בצורה נקייה
                        if not self._can_swap_into(emp_c, slot_c, slot_a):
                            continue

                        # ביצוע: a→b, b→c, c→a
                        slot_a.assigned_employee = emp_c_name
                        slot_b.assigned_employee = v_emp_name
                        slot_c.assigned_employee = emp_b_name
                        emp_a.assigned.remove((v_day, v_shift, slot_a.position_name))
                        emp_a.assigned.append((slot_b.day, slot_b.shift_type, slot_b.position_name))
                        emp_b.assigned.remove((slot_b.day, slot_b.shift_type, slot_b.position_name))
                        emp_b.assigned.append((slot_c.day, slot_c.shift_type, slot_c.position_name))
                        emp_c.assigned.remove((slot_c.day, slot_c.shift_type, slot_c.position_name))
                        emp_c.assigned.append((v_day, v_shift, slot_a.position_name))

                        emp_a.violations.remove((v_day, v_shift))
                        self.violations.remove((v_emp_name, v_day, v_shift))
                        _clear_vacated_violation(emp_b, slot_b.day, slot_b.shift_type)
                        _clear_vacated_violation(emp_c, slot_c.day, slot_c.shift_type)

                        print(f"[Scheduler] 3way: {v_emp_name} "
                              f"({DAYS[v_day]} {v_shift.value}"
                              f"->{DAYS[slot_b.day]} {slot_b.shift_type.value}) | "
                              f"{emp_b_name} "
                              f"(->{DAYS[slot_c.day]} {slot_c.shift_type.value}) | "
                              f"{emp_c_name} "
                              f"(->{DAYS[v_day]} {v_shift.value})")
                        swapped = True
                        found_this = True
                        break
                    if found_this:
                        break

    def _migrate_violations_to_juniors(self) -> None:
        """שלב 6ב: העברת הפרות שנותרו מעובדים בכירים לצעירים יותר.

        כאשר שלב 6 לא יכול לבטל הפרה לגמרי, מנסה למצוא עובד צעיר יותר
        שיכול לספוג אותה. emp_a (בכיר, מפר) עובר לסלוט נקי;
        emp_b (הסופג הצעיר ביותר שנמצא) לוקח את הסלוט המופר של emp_a —
        יוצר או שומר את ההפרה על העובד הצעיר.

        תנאים נדרשים:
          - דרגת ותק emp_b > דרגת ותק emp_a (emp_b צעיר יותר)
          - emp_a יכול לעבוד בסלוט של emp_b בצורה נקייה (_can_swap_into עם allow_blocked=False)
          - emp_b יכול לעבוד פיזית בסלוט המופר של emp_a (allow_blocked=True)
        """
        improved = True
        while improved:
            improved = False
            for v_emp_name, v_day, v_shift in list(self.violations):
                emp_a = self.employees.get(v_emp_name)
                if emp_a is None:
                    continue
                slot_a = next(
                    (s for s in self.slots
                     if s.assigned_employee == v_emp_name
                     and s.day == v_day and s.shift_type == v_shift),
                    None,
                )
                if slot_a is None or slot_a.locked:
                    continue

                rank_a = self._seniority_rank.get(v_emp_name, 999)

                # איתור העובד הצעיר ביותר שיכול לספוג את ההפרה
                best: Optional[Tuple[int, "PositionSlot", "Employee"]] = None

                for slot_b in self.slots:
                    if slot_b is slot_a or slot_b.locked or not slot_b.assigned_employee:
                        continue
                    emp_b_name = slot_b.assigned_employee
                    if emp_b_name == v_emp_name:
                        continue
                    emp_b = self.employees.get(emp_b_name)
                    if emp_b is None or emp_b.is_stub:
                        continue

                    rank_b = self._seniority_rank.get(emp_b_name, 999)
                    if rank_b <= rank_a:
                        continue  # emp_b חייב להיות צעיר יותר בהחלט

                    # emp_a חייב לעבור לסלוט_b בצורה נקייה (ללא הפרת זמינות)
                    if not self._can_swap_into(emp_a, slot_a, slot_b):
                        continue

                    # emp_b חייב להיות מסוגל לעבוד פיזית ב-slot_a (הפרה מותרת)
                    entry_b = (slot_b.day, slot_b.shift_type, slot_b.position_name)
                    emp_b.assigned.remove(entry_b)
                    try:
                        ok_b, _ = can_assign(emp_b, slot_a,
                                             allow_blocked=True, allow_overload=True)
                    finally:
                        emp_b.assigned.append(entry_b)
                    if not ok_b:
                        continue

                    # עדיפות לסופג הצעיר ביותר (מספר דרגה גבוה ביותר)
                    if best is None or rank_b > best[0]:
                        best = (rank_b, slot_b, emp_b)

                if best is None:
                    continue

                _, slot_b, emp_b = best
                emp_b_name = emp_b.name
                new_violation = not emp_b.is_available(slot_a.day, slot_a.shift_type)
                old_b_vio = (slot_b.day, slot_b.shift_type) in emp_b.violations

                # ביצוע ההחלפה
                slot_a.assigned_employee = emp_b_name
                slot_b.assigned_employee = v_emp_name
                emp_a.assigned.remove((v_day, v_shift, slot_a.position_name))
                emp_a.assigned.append((slot_b.day, slot_b.shift_type, slot_b.position_name))
                emp_b.assigned.remove((slot_b.day, slot_b.shift_type, slot_b.position_name))
                emp_b.assigned.append((v_day, v_shift, slot_a.position_name))

                # ניקוי הפרת emp_a
                emp_a.violations.remove((v_day, v_shift))
                self.violations.remove((v_emp_name, v_day, v_shift))

                # ניקוי הפרה ישנה של emp_b ב-slot_b אם הייתה
                if old_b_vio:
                    emp_b.violations.remove((slot_b.day, slot_b.shift_type))
                    try:
                        self.violations.remove((emp_b_name, slot_b.day, slot_b.shift_type))
                    except ValueError:
                        pass

                # רישום הפרה חדשה על emp_b ב-slot_a אם רלוונטי
                if new_violation:
                    emp_b.violations.append((slot_a.day, slot_a.shift_type))
                    self.violations.append((emp_b_name, slot_a.day, slot_a.shift_type))

                outcome = "eliminated" if not new_violation else "migrated->junior"
                print(
                    f"[Scheduler] MigrateViol: {v_emp_name} (rank {rank_a}) "
                    f"({DAYS[v_day]} {v_shift.value}"
                    f" -> {DAYS[slot_b.day]} {slot_b.shift_type.value}) "
                    f"<-> {emp_b_name} (rank {best[0]}) "
                    f"({DAYS[slot_b.day]} {slot_b.shift_type.value}"
                    f" -> {DAYS[v_day]} {v_shift.value}) [{outcome}]"
                )
                improved = True
                break

    # -----------------------------------------------------------------------
    # שלב 7 — מיון בתוך משמרות לפי התאמת ותק-לחשיבות
    # -----------------------------------------------------------------------

    def _sort_within_shifts(self) -> None:
        """בתוך כל (day, shift), מקצה מחדש עובדים כך שעמדות חשובות יותר
        תמיד מכילות עובדים בכירים יותר.

        מכיוון שאנו מסדרים מחדש עובדים רק באותו סלוט זמן, אף
        אילוץ קשיח אינו משתנה (רצף, מגבלות לילה, מקסימום משמרות, ו-
        זמינות — כולם מוּגדים לפי (day, shift), לא לפי שם עמדה).
        ההקצאה מחדש תמיד תקינה עבור סלוטים פתוחים ומדורגים.
        """
        from collections import defaultdict

        # קיבוץ סלוטים פתוחים ומדורגים לפי (day, shift_type)
        slot_groups: Dict[Tuple[int, ShiftType], List[PositionSlot]] = defaultdict(list)
        for slot in self.slots:
            if slot.locked or not slot.assigned_employee:
                continue
            if slot.position_importance >= 999:
                continue
            emp = self.employees.get(slot.assigned_employee)
            if emp is None or emp.is_stub:
                continue
            slot_groups[(slot.day, slot.shift_type)].append(slot)

        n_swaps = 0
        for (day, shift_type), group in slot_groups.items():
            if len(group) < 2:
                continue

            # מיון סלוטים בסדר עולה לפי חשיבות (הכי חשוב = המספר הנמוך ביותר ראשון)
            group.sort(key=lambda s: s.position_importance)

            # איסוף העובדים המוקצים כעת, ממוינים לפי דרגת ותק
            old_names = [s.assigned_employee for s in group]
            new_names = sorted(
                old_names,
                key=lambda name: self._seniority_rank.get(name, 999),
            )

            if old_names == new_names:
                continue  # כבר בסדר הנכון

            # שלב 1 — הסרת רשומות (day, shift, position) ישנות מכל עובד
            for slot, old_name, new_name in zip(group, old_names, new_names):
                if old_name == new_name:
                    continue
                emp = self.employees[old_name]
                entry = (day, shift_type, slot.position_name)
                if entry in emp.assigned:
                    emp.assigned.remove(entry)
                if entry in emp.ot_assignments:
                    emp.ot_assignments.remove(entry)

            # שלב 2 — הוספת רשומות חדשות ועדכון slot.assigned_employee
            for slot, old_name, new_name in zip(group, old_names, new_names):
                if old_name == new_name:
                    continue
                emp = self.employees[new_name]
                entry = (day, shift_type, slot.position_name)
                emp.assigned.append(entry)
                slot.assigned_employee = new_name
                n_swaps += 1

        if n_swaps:
            print(f"[Scheduler] Phase 7: re-sorted {n_swaps} within-shift assignments "
                  f"to match seniority to importance.")

    # -----------------------------------------------------------------------
    # שלב 8 — חישוב מחדש של דגלי OT
    # -----------------------------------------------------------------------

    def _recompute_ot(self) -> None:
        """בניה מחדש של emp.ot_assignments ו-slot.is_ot לאחר התייצבות כל השלבים.

        חוקים:
        - סלוטים אסורים (Friday Noon/Night + כל Saturday) לעולם אינם מסומנים OT.
        - בין ההקצאות הזכאיות, עדיפות ל-Morning תחילה, אחר כך Noon, בבחירת
          אילו משמרות נושאות את סמן ה-OT הכתום.
        """
        import config as _cfg
        _friday   = _cfg.SCHEDULING["friday_idx"]
        _saturday = _cfg.SCHEDULING["saturday_idx"]
        _forbidden: set = {
            (_friday,   ShiftType.NOON),
            (_friday,   ShiftType.NIGHT),
            (_saturday, ShiftType.MORNING),
            (_saturday, ShiftType.NOON),
            (_saturday, ShiftType.NIGHT),
        }
        _shift_pref = {ShiftType.MORNING: 0, ShiftType.NOON: 1, ShiftType.NIGHT: 2}

        # מפת בדיקה של סלוטים: (name, day, shift, pos) -> PositionSlot
        slot_lookup: Dict[Tuple[str, int, ShiftType, str], PositionSlot] = {}
        for slot in self.slots:
            if slot.assigned_employee:
                key = (slot.assigned_employee, slot.day, slot.shift_type, slot.position_name)
                slot_lookup[key] = slot

        for emp in self.employees.values():
            emp.ot_assignments.clear()
            if emp.is_stub:
                # Stubs הם עובדים מוקצים מראש שאינם בצפי; לעולם לא מסמנים OT
                for day, shift, pos in emp.assigned:
                    slot = slot_lookup.get((emp.name, day, shift, pos))
                    if slot is not None:
                        slot.is_ot = False
                continue
            extra = max(0, emp.assigned_count - emp.required_shifts)

            ot_set: set = set()
            if extra > 0:
                # זכאי = לא בסלוטי OT אסורים; עדיפות Morning אחר כך Noon אחר כך Night
                eligible = [
                    (day, shift, pos)
                    for day, shift, pos in emp.assigned
                    if (day, shift) not in _forbidden
                ]
                eligible.sort(key=lambda t: (_shift_pref.get(t[1], 99), t[0]))
                for day, shift, pos in eligible[:extra]:
                    ot_set.add((day, shift, pos))
                    emp.ot_assignments.append((day, shift, pos))

            for day, shift, pos in emp.assigned:
                slot = slot_lookup.get((emp.name, day, shift, pos))
                if slot is not None:
                    slot.is_ot = (day, shift, pos) in ot_set

    # -----------------------------------------------------------------------
    # שלב 9 — דיווח מחסור
    # -----------------------------------------------------------------------

    def _report_shortage(self) -> None:
        under = [
            emp for emp in self.employees.values()
            if emp.assigned_count < emp.required_shifts
        ]
        if under:
            print(f"[Scheduler] NOTE: {len(under)} employee(s) below required shift count:")
            for emp in under:
                print(f"  {emp.name}: assigned={emp.assigned_count}, "
                      f"required={emp.required_shifts}")

    # -----------------------------------------------------------------------
    # פונקציות עזר להקצאה
    # -----------------------------------------------------------------------

    def _try_assign(self, slot: PositionSlot, *, night_spread: bool = False) -> None:
        """ניסיון להקצות את העובד הזכאי הטוב ביותר ל-*slot*."""
        # --- מועמדים רגילים (ללא הפרת אילוץ) ---
        candidates = self._get_candidates(slot)
        if candidates:
            best = self._pick_normal_candidate(candidates, slot, night_spread=night_spread)
            self._do_assign(slot, best)
            return

        # --- מועמדים עם הפרה (ביטול חסום/X) ---
        candidates = self._get_candidates(slot, allow_blocked=True)
        if candidates:
            best = self._pick_violation_candidate(candidates)
            self._do_assign(slot, best, violation=True)
            return

        # הסלוט נותר פתוח; _fill_remaining() יטפל בו

    def _get_candidates(
        self,
        slot: PositionSlot,
        *,
        allow_blocked: bool = False,
        allow_overload: bool = False,
        allow_extra_night: bool = False,
    ) -> List[Employee]:
        return [
            emp for emp in self.employees.values()
            if can_assign(emp, slot,
                          allow_blocked=allow_blocked,
                          allow_overload=allow_overload,
                          allow_extra_night=allow_extra_night)[0]
        ]

    def _junior_key(self, emp: Employee) -> int:
        """מפתח מיון שבו ערך נמוך יותר = צעיר יותר.

        מכבד את config.FORECAST['seniority_lower_means_senior']:
          True  → ותק 1 = הבכיר ביותר, 56 = הצעיר ביותר
                  → שלילה כדי שהמספר הגבוה ביותר ימוין ראשון (הצעיר ביותר ראשון)
          False → ותק 1 = הצעיר ביותר, 100 = הבכיר ביותר
                  → שימוש בערך הגולמי כך שהמספר הנמוך ביותר ימוין ראשון (הצעיר ביותר ראשון)
        """
        lower_senior = _cfg.FORECAST.get('seniority_lower_means_senior', False)
        return -emp.seniority if lower_senior else emp.seniority

    def _pick_normal_candidate(
        self,
        candidates: List[Employee],
        slot: PositionSlot,
        *,
        night_spread: bool = False,
    ) -> Employee:
        """בחירת המועמד הטוב ביותר מבלי להפר אילוץ כלשהו.

        לעמדות מדורגות (חשיבות < 999) ומשמרות שאינן לילה:
          1. עדיפות למועמדים שקבוצת חשיבותם המוגדרת מראש מתאימה לסלוט.
          2. בתוך הקבוצה המועדפת, מיון לפי צורך אחר כך ותק.
          3. אם אין מועמד מהקבוצה המועדפת, נפילה לכל המועמדים
             ממוינים לפי קרבת ותק לדרגת היעד.

        למשמרות לילה ועמדות לא-מדורגות, חל המיון המקורי:
          ראשוני  : צורך (יותר צורך = נבחר ראשון)
          משני    : מספר לילות (פחות לילות = מועדף, לפיזור)
          שלישוני : ותק (צעיר סופג לילות / בכיר מקבל סלוטים טובים)
        """
        def _base_key(emp: Employee):
            need   = emp.required_shifts - emp.assigned_count
            nights = emp.night_count if night_spread else 0
            sen    = self._junior_key(emp) if night_spread else -self._junior_key(emp)
            return (-need, nights, sen)

        slot_imp = slot.position_importance
        if night_spread or slot_imp >= 999:
            return min(candidates, key=_base_key)

        # --- סלוט לא-לילה מדורג: התאמת ותק לחשיבות ---
        preferred = [
            e for e in candidates
            if self._importance_group.get(e.name) == slot_imp
        ]
        if preferred:
            return min(preferred, key=_base_key)

        # הקבוצה המועדפת מוצתה: בחירה לפי קרבת ותק לדרגת יעד
        n_emps   = self._n_real_employees
        n_levels = self._n_importance_levels
        tgt_idx  = self._imp_level_index.get(slot_imp, 0)
        ideal_rank = (tgt_idx / max(n_levels - 1, 1)) * (n_emps - 1) + 1

        def _proximity_key(emp: Employee):
            sr   = self._seniority_rank.get(emp.name, n_emps)
            need = emp.required_shifts - emp.assigned_count
            return (abs(sr - ideal_rank), -need, -self._junior_key(emp))

        return min(candidates, key=_proximity_key)

    def _pick_violation_candidate(self, candidates: List[Employee]) -> Employee:
        """עבור הפרה (ביטול משמרת חסומה), בחירת העובד הצעיר ביותר תחילה."""
        def sort_key(emp: Employee):
            need = emp.required_shifts - emp.assigned_count
            return (self._junior_key(emp), -need)

        return min(candidates, key=sort_key)

    def _do_assign(
        self,
        slot: PositionSlot,
        emp: Employee,
        *,
        violation: bool = False,
        overload: bool = False,
    ) -> None:
        """מבצע את ההקצאה ומעדכן את כל המצב."""
        slot.assigned_employee = emp.name
        emp.assigned.append((slot.day, slot.shift_type, slot.position_name))

        if violation and not emp.is_available(slot.day, slot.shift_type):
            emp.violations.append((slot.day, slot.shift_type))
            self.violations.append((emp.name, slot.day, slot.shift_type))

        if emp.assigned_count > emp.required_shifts:
            emp.ot_assignments.append((slot.day, slot.shift_type, slot.position_name))
            slot.is_ot = True

        if overload and emp.assigned_count > emp.max_shifts:
            print(f"[Scheduler] Overload: {emp.name} now has "
                  f"{emp.assigned_count} shifts (cap {emp.max_shifts})")
