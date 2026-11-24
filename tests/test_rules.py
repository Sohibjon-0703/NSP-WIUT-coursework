"""Tests for the main business rules. Run from the project folder:  python -m unittest -v"""
import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path

from nsp import App
from nsp import domain as d

T = dt.datetime(2026, 11, 24, 15, 0)


class DomainRules(unittest.TestCase):
    def test_abstract_classes_cannot_be_created(self):
        with self.assertRaises(TypeError):
            d.User(full_name="x")
        with self.assertRaises(TypeError):
            d.Teacher(full_name="x")
        self.assertTrue(d.MainTeacher().records_tests())
        self.assertFalse(d.SupportTeacher().records_tests())

    def test_role_permissions(self):
        self.assertTrue(d.Director().can("audit.view"))
        self.assertFalse(d.MainTeacher().can("kpi.view_all"))
        self.assertTrue(d.HeadSpecialist().can("correction.decide"))

    def test_absence_run(self):                      # FR-02.6
        s = d.Student()
        for st in (d.AttendanceStatus.ABSENT, d.AttendanceStatus.ABSENT, d.AttendanceStatus.ABSENT_EXCUSED, d.AttendanceStatus.ABSENT):
            s.update_absence_run(st)
        self.assertEqual(s.consecutive_absences, 3)
        s.update_absence_run(d.AttendanceStatus.LATE)
        self.assertEqual(s.consecutive_absences, 0)

    def test_qr_token_expires_and_is_signed(self):   # FR-01.1, NFR-01.3
        room = d.Room(id=3, qr_secret="secret")
        now = time.time()
        tok = room.current_token(now)
        self.assertTrue(d.QrToken.parse(tok.value).is_valid("secret", now))
        self.assertFalse(tok.is_valid("secret", now + 150))
        forged = d.QrToken(3, tok.issued_at, "0" * 16)
        self.assertFalse(forged.is_valid("secret", now))

    def test_lesson_window_and_lateness(self):       # FR-01.2, FR-01.3
        l = d.Lesson(start=T, end=T + dt.timedelta(minutes=90))
        self.assertTrue(l.is_check_in_open(T - dt.timedelta(minutes=30)))
        self.assertFalse(l.is_check_in_open(T + dt.timedelta(minutes=31)))
        self.assertFalse(d.AttendanceRecord(minutes_late=10).is_late())
        self.assertTrue(d.AttendanceRecord(minutes_late=11).is_late())

    def test_work_session(self):                     # FR-01.5, FR-01.6, FR-03.3
        ws = d.WorkSession(check_in=T - dt.timedelta(minutes=5), planned_start=T, planned_end=T + dt.timedelta(minutes=90))
        ws.close(T + dt.timedelta(minutes=100))
        self.assertEqual((ws.worked_minutes(), ws.overtime_minutes(), ws.flag), (105, 10, d.SessionFlag.CLOSED))
        forgot = d.WorkSession(check_in=T, planned_start=T, planned_end=T + dt.timedelta(minutes=90))
        self.assertTrue(forgot.auto_close(T + dt.timedelta(minutes=151)))
        self.assertEqual(forgot.worked_minutes(), 0)          # left out until confirmed

    def test_register_rules(self):                   # FR-02.2, 02.3, 02.4, 02.5
        reg = d.LessonRegister(entries=[d.RegisterEntry(student_id=1, scanned=True, status=d.AttendanceStatus.PRESENT),
                                        d.RegisterEntry(student_id=2)])
        with self.assertRaises(d.RuleViolation):
            reg.submit(T, T.date())
        with self.assertRaises(d.RuleViolation):
            reg.set_status(2, d.AttendanceStatus.ABSENT_EXCUSED, "")
        self.assertTrue(reg.set_status(2, d.AttendanceStatus.PRESENT).teacher_override)
        reg.submit(T + dt.timedelta(days=1), T.date())
        self.assertTrue(reg.locked and reg.late_submission)
        with self.assertRaises(d.RuleViolation):
            reg.set_status(1, d.AttendanceStatus.ABSENT)

    def test_kpi_weights_and_partial_score(self):    # FR-04.1, FR-04.5
        with self.assertRaises(d.RuleViolation):
            d.KpiWeights.from_dict({"results": 50, "attendance": 60}, "2026-12", 8)
        w = d.KpiWeights()
        inds = [d.KpiIndicator("results", None, False), d.KpiIndicator("attendance", 80), d.KpiIndicator("punctuality", 100),
                d.KpiIndicator("retention", 90), d.KpiIndicator("feedback", 80), d.KpiIndicator("rating", 70),
                d.KpiIndicator("achievements", 40)]
        snap = d.KpiSnapshot.compose(1, "2026-10", inds, w, provisional=False)
        self.assertEqual(snap.label, d.KpiLabel.PARTIAL)
        self.assertAlmostEqual(sum(snap.effective_weights().values()), 100, delta=0.2)
        self.assertAlmostEqual(snap.overall, (80*10 + 100*10 + 90*20 + 80*20 + 70*10 + 40*5) / 75, places=1)
        with self.assertRaises(d.RuleViolation):
            d.KpiSnapshot.compose(1, "2026-11", inds, w, provisional=True).lock()

    def test_ratings_and_feedback_ranges(self):      # FR-04.2, FR-04.4
        with self.assertRaises(d.RuleViolation):
            d.MonthlyRating(score=10.5)
        with self.assertRaises(d.RuleViolation):
            d.TeacherFeedback(rating=6)

    def test_alert_lifecycle(self):                  # FR-05.2, FR-05.4
        a = d.RiskAlert(reasons=["3 consecutive absences"])
        a.add_reason("3 consecutive absences")
        self.assertEqual(len(a.reasons), 1)
        a.assign(d.FollowUpAction(description="Call parent", assignee_id=7, due=T.date()))
        self.assertEqual(a.status, d.AlertStatus.IN_PROGRESS)
        self.assertTrue(a.follow_ups[0].is_overdue(T.date() + dt.timedelta(days=1)))
        a.close(d.AlertStatus.RESOLVED)
        with self.assertRaises(d.RuleViolation):
            a.assign(d.FollowUpAction(description="x", due=T.date()))


class ServiceFlows(unittest.TestCase):
    """End-to-end through the services, on a fresh copy of the demo data."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = App(Path(cls.tmp.name) / "test.db")
        cls.s, cls.repo = cls.app.services, cls.app.repo

    @classmethod
    def tearDownClass(cls):
        cls.app.repo.con.close()
        cls.tmp.cleanup()

    def user(self, cls, **kw):
        return self.repo.first(cls, **kw)

    def test_third_absence_raises_one_alert_and_notifies(self):
        teacher = self.repo.get(d.User, 1)
        reg = self.s.registers.todays_register(teacher)
        aziza = self.user(d.Student, full_name="Aziza Nurmatova")
        for row in reg["rows"]:
            status = "Absent" if row["student_id"] == aziza.id else row["status"] or "Present"
            self.s.registers.set_status(teacher, reg["register_id"], row["student_id"], status)
        result = self.s.registers.submit(teacher, reg["register_id"])
        self.assertEqual(result["alerts"], ["Aziza Nurmatova"])
        self.assertIsNotNone(self.s.risk.open_alert(aziza.id))
        admin = self.user(d.Administrator)
        self.assertTrue(any("Aziza" in n.message for n in self.s.notifier.inbox(admin)))

    def test_closed_month_is_not_changed_by_new_weights(self):
        hs, director = self.user(d.HeadSpecialist), self.user(d.Director)
        prev = d.shift_month(self.s.clock.month(), -1)
        before = [r["overall"] for r in self.s.kpi.table(director, prev)["rows"]]
        self.s.kpi.save_weights(hs, {"results": 10, "attendance": 10, "punctuality": 10, "retention": 10,
                                     "feedback": 50, "rating": 5, "achievements": 5})
        after = [r["overall"] for r in self.s.kpi.table(director, prev)["rows"]]
        self.assertEqual(before, after)

    def test_permissions_are_enforced_by_services(self):
        teacher = self.repo.get(d.User, 1)
        with self.assertRaises(PermissionError):
            self.s.kpi.table(teacher, self.s.clock.month())
        with self.assertRaises(PermissionError):
            self.s.kpi.detail(teacher, 2, self.s.clock.month())

    def test_expired_code_is_rejected(self):
        timur = self.user(d.Student, full_name="Timur Olimov")
        old = self.s.qr.old_token(3)
        with self.assertRaises(d.RuleViolation):
            self.s.attendance.check_in(timur, old, dt.datetime.combine(self.s.clock.today(), dt.time(14, 58)))

    def test_feedback_only_for_own_teachers_once_a_month(self):
        timur = self.user(d.Student, full_name="Timur Olimov")
        with self.assertRaises(d.RuleViolation):
            self.s.feedback.submit(timur, 3, 5)
        self.s.feedback.submit(timur, 5, 4)
        with self.assertRaises(d.RuleViolation):
            self.s.feedback.submit(timur, 5, 4)


if __name__ == "__main__":
    unittest.main()
