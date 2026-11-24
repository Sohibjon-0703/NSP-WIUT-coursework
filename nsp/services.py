"""«package» services - business rules that span several entities (report Figure 2.1).

QrTokenService, AttendanceService (+ WorkloadService), RegisterService, RiskService,
FeedbackService, KpiService, RetentionService and NotificationService (realises Notifier).
Services receive the signed-in user and check the permission before acting (NFR-01.1).
"""
import abc
import datetime as dt
import time
from typing import Any, Dict, List, Optional

from .domain import (
    INDICATORS, INDICATOR_LABELS, Achievement, Administrator, AlertStatus, AttendanceMethod,
    AttendanceRecord, AttendanceStatus, CorrectionRequest, CorrectionStatus, Director, DropoutRisk,
    FollowUpAction, Group, HeadSpecialist, KpiIndicator, KpiLabel, KpiSnapshot, KpiWeights, Lesson,
    LessonRegister, MarketingSpend, MonthlyRating, Notification, QrToken, RegisterEntry, RiskAlert,
    RiskLevel, Room, RuleViolation, SessionFlag, Student, StudentCircumstance, StudentStatus, Subject, Teacher,
    TeacherFeedback, User, Withdrawal, WithdrawalReason, WorkSession, month_label, month_of,
    month_start, shift_month,
)
from .integration import EduTizimGateway
from .persistence import AuditLog, Repository


class Clock:
    def now(self) -> dt.datetime:
        return dt.datetime.now().replace(microsecond=0)

    def today(self) -> dt.date:
        return self.now().date()

    def month(self) -> str:
        return month_of(self.today())


def require(user: User, permission: str) -> None:
    if not user.can(permission):
        raise PermissionError("Your role (%s) is not allowed to do this (NFR-01.1)." % user.ROLE)


def _hm(t: Optional[dt.datetime]) -> str:
    return t.strftime("%H:%M") if t else ""


# --------------------------------------------------------------------------- notifications
class Notifier(abc.ABC):
    """Interface so Teams or Telegram delivery can be added without touching RiskService (Figure 2.4)."""

    @abc.abstractmethod
    def send(self, to: User, message: str, page: str = "") -> None: ...


class NotificationService(Notifier):
    def __init__(self, repo: Repository, clock: Clock):
        self.repo, self.clock = repo, clock

    def send(self, to: User, message: str, page: str = "") -> None:
        self.repo.save(Notification(user_id=to.id, message=message, created_at=self.clock.now(), page=page))

    def send_to_role(self, role: type, message: str, page: str = "") -> None:
        for u in self.repo.find(role):
            self.send(u, message, page)

    def inbox(self, user: User) -> List[Notification]:
        return self.repo.find(Notification, order="-id", user_id=user.id)[:30]

    def mark_read(self, user: User) -> None:
        for n in self.repo.find(Notification, user_id=user.id, read=False):
            n.read = True
            self.repo.save(n)


class Service:
    def __init__(self, ctx: "Services"):
        self.ctx = ctx
        self.repo: Repository = ctx.repo
        self.audit: AuditLog = ctx.audit
        self.clock: Clock = ctx.clock
        self.notifier: NotificationService = ctx.notifier

    # small shared lookups
    def group_of(self, student: Student) -> Group:
        return self.repo.get(Group, student.group_id)

    def lesson_title(self, lesson: Lesson) -> str:
        g = self.repo.get(Group, lesson.group_id)
        r = self.repo.get(Room, lesson.room_id)
        name = "Support session (%s)" % g.name if lesson.is_support_session else g.name
        return "%s, %s, %s-%s" % (name, r.name, _hm(lesson.start), _hm(lesson.end))


# --------------------------------------------------------------------------- QR
class QrTokenService(Service):
    def token_for(self, room_id: int) -> Dict[str, Any]:
        room = self.repo.get(Room, room_id)
        if not room:
            raise LookupError("Unknown room")
        now = time.time()
        tok = room.current_token(now)
        return {"room": room.name, "value": tok.value,
                "seconds_left": int(tok.issued_at + QrToken.LIFETIME - now)}

    def old_token(self, room_id: int) -> str:
        """Test helper: a photo of the code taken 2.5 minutes ago."""
        return self.repo.get(Room, room_id).current_token(time.time() - 150).value

    def validate(self, value: str) -> Room:
        tok = QrToken.parse(value)
        room = self.repo.get(Room, tok.room_id)
        if not room or not tok.is_valid(room.qr_secret, time.time()):
            raise RuleViolation("This code has expired or is not valid. Scan the code currently shown on the room screen.")
        return room


# --------------------------------------------------------------------------- attendance + workload
class AttendanceService(Service):
    def lessons_today(self, user: User, day: dt.date) -> List[Lesson]:
        if isinstance(user, Teacher):
            ls = self.repo.find(Lesson, order="start", teacher_id=user.id)
        elif isinstance(user, Student):
            ls = self.repo.find(Lesson, order="start", group_id=user.group_id, is_support_session=False)
        else:
            return []
        return [l for l in ls if l.start.date() == day]

    def find_lesson(self, user: User, room: Room, at: dt.datetime) -> Lesson:
        todays = self.lessons_today(user, at.date())
        here = [l for l in todays if l.room_id == room.id and l.is_check_in_open(at)]
        if here:
            return min(here, key=lambda l: abs((l.start - at).total_seconds()))
        if not todays:
            raise RuleViolation("You have no lesson scheduled today.")
        near = min(todays, key=lambda l: abs((l.start - at).total_seconds()))
        r = self.repo.get(Room, near.room_id)
        if near.room_id != room.id:
            raise RuleViolation("You have no lesson in %s now - your lesson is in %s at %s." % (room.name, r.name, _hm(near.start)))
        w = dt.timedelta(minutes=Lesson.CHECK_IN_WINDOW)
        raise RuleViolation("Check-in for your %s lesson is open from %s to %s." % (_hm(near.start), _hm(near.start - w), _hm(near.start + w)))

    def my_day(self, user: User) -> Dict[str, Any]:
        today = self.clock.today()
        out = []
        for l in self.lessons_today(user, today):
            rec = self.repo.first(AttendanceRecord, user_id=user.id, lesson_id=l.id)
            ws = self.repo.first(WorkSession, teacher_id=user.id, lesson_id=l.id) if isinstance(user, Teacher) else None
            out.append({"lesson_id": l.id, "title": self.lesson_title(l), "room_id": l.room_id,
                        "room": self.repo.get(Room, l.room_id).name, "start": _hm(l.start), "end": _hm(l.end),
                        "support": l.is_support_session,
                        "check_in": rec and {"time": _hm(rec.timestamp), "status": rec.status_text(), "method": rec.method.value},
                        "session": ws and {"flag": ws.flag.value, "check_out": _hm(ws.check_out),
                                           "worked": ws.worked_minutes(), "overtime": ws.overtime_minutes()}})
        return {"date": today.strftime("%A %d %B %Y"), "lessons": out, "is_teacher": isinstance(user, Teacher)}

    def check_in(self, user: User, token: str, at: dt.datetime) -> Dict[str, Any]:
        require(user, "attendance.self")
        room = self.ctx.qr.validate(token)
        lesson = self.find_lesson(user, room, at)
        existing = self.repo.first(AttendanceRecord, user_id=user.id, lesson_id=lesson.id)
        if existing:   # UC-01 5b: no duplicate record
            return {"duplicate": True, "time": _hm(existing.timestamp), "status": existing.status_text(),
                    "lesson": self.lesson_title(lesson)}
        rec = self.repo.save(AttendanceRecord(user_id=user.id, lesson_id=lesson.id, timestamp=at,
                                              method=AttendanceMethod.QR, minutes_late=lesson.minutes_late(at)))
        self._after_check_in(user, lesson, rec)
        self.audit.record(user, "Check-in (QR)", "%s - %s" % (user.full_name, self.lesson_title(lesson)), "", rec.status_text())
        return {"duplicate": False, "time": _hm(at), "status": rec.status_text(), "late": rec.is_late(),
                "lesson": self.lesson_title(lesson), "teacher": isinstance(user, Teacher)}

    def _after_check_in(self, user: User, lesson: Lesson, rec: AttendanceRecord) -> None:
        if isinstance(user, Teacher):
            self.repo.save(WorkSession(teacher_id=user.id, lesson_id=lesson.id, check_in=rec.timestamp,
                                       planned_start=lesson.start, planned_end=lesson.end))
        elif isinstance(user, Student):
            self.ctx.registers.prefill(lesson, user, rec)

    def check_out(self, user: User, token: str, at: dt.datetime) -> Dict[str, Any]:
        require(user, "attendance.self")
        if not isinstance(user, Teacher):
            raise RuleViolation("Only teachers check out; it closes the work session used for hours and overtime.")
        room = self.ctx.qr.validate(token)
        open_ = self.repo.find(WorkSession, order="-id", teacher_id=user.id, flag=SessionFlag.OPEN)
        if not open_:
            raise RuleViolation("You have no open work session. Check in first.")
        ws = open_[0]
        lesson = self.repo.get(Lesson, ws.lesson_id)
        if lesson.room_id != room.id:
            raise RuleViolation("Scan the code in %s, where your lesson is." % self.repo.get(Room, lesson.room_id).name)
        ws.close(at)
        self.repo.save(ws)
        self.audit.record(user, "Check-out (QR)", "%s - %s" % (user.full_name, self.lesson_title(lesson)), "",
                          "%s, worked %d min, overtime %d min" % (ws.flag.value, ws.worked_minutes(), ws.overtime_minutes()))
        return {"flag": ws.flag.value, "worked": ws.worked_minutes(), "overtime": ws.overtime_minutes(),
                "early": ws.early_minutes(), "time": _hm(at)}

    # ---- administrator desk
    def desk(self, admin: User) -> Dict[str, Any]:
        require(admin, "attendance.manual")
        today = self.clock.today()
        people = []
        for t in self.repo.find(Teacher, order="full_name"):
            people.append({"id": t.id, "name": t.full_name, "role": "%s teacher" % t.KIND})
        for s in self.repo.find(Student, order="full_name", status=StudentStatus.ACTIVE):
            if self.lessons_today(s, today):
                people.append({"id": s.id, "name": s.full_name, "role": "Student, " + self.group_of(s).name})
        lessons_today = {l.id: l for l in self.repo.find(Lesson) if l.start.date() == today}
        log = []
        for rec in self.repo.find(AttendanceRecord, order="-timestamp"):
            l = lessons_today.get(rec.lesson_id)
            if not l:
                continue
            u = self.repo.get(User, rec.user_id)
            log.append({"time": _hm(rec.timestamp), "name": u.full_name, "role": u.ROLE, "lesson": self.lesson_title(l),
                        "method": rec.method.value, "status": rec.status_text(), "reason": rec.reason})
        pending = []
        for ws in self.repo.find(WorkSession, order="planned_start", flag=SessionFlag.MISSING_CHECKOUT):
            t = self.repo.get(User, ws.teacher_id)
            pending.append({"id": ws.id, "teacher": t.full_name, "lesson": self.lesson_title(self.repo.get(Lesson, ws.lesson_id)),
                            "date": ws.planned_start.strftime("%a %d %b"), "check_in": _hm(ws.check_in),
                            "planned_end": _hm(ws.planned_end)})
        overrides = []
        for reg in self.repo.find(LessonRegister, locked=True):
            l = lessons_today.get(reg.lesson_id)
            if l:
                for e in reg.entries:
                    if e.teacher_override:
                        overrides.append({"student": self.repo.get(User, e.student_id).full_name,
                                          "lesson": self.lesson_title(l), "status": e.status.value})
        return {"people": people, "log": log, "pending_sessions": pending, "overrides": overrides}

    def confirm_manually(self, admin: User, user_id: int, at: dt.datetime, reason: str) -> Dict[str, Any]:
        require(admin, "attendance.manual")
        if not (reason or "").strip():
            raise RuleViolation("A reason is required for manual confirmation (FR-01.7).")
        user = self.repo.get(User, user_id)
        if not user:
            raise RuleViolation("Choose a person.")
        todays = self.lessons_today(user, at.date())
        if not todays:
            raise RuleViolation("%s has no lesson today." % user.full_name)
        lesson = min(todays, key=lambda l: abs((l.start - at).total_seconds()))
        if self.repo.first(AttendanceRecord, user_id=user.id, lesson_id=lesson.id):
            raise RuleViolation("%s is already checked in for the %s lesson." % (user.full_name, _hm(lesson.start)))
        rec = self.repo.save(AttendanceRecord(user_id=user.id, lesson_id=lesson.id, timestamp=at, method=AttendanceMethod.MANUAL,
                                              minutes_late=lesson.minutes_late(at), reason=reason.strip(), confirmed_by=admin.id))
        self._after_check_in(user, lesson, rec)
        self.audit.record(admin, "Manual attendance", "%s - %s" % (user.full_name, self.lesson_title(lesson)), "",
                          "%s, %s. Reason: %s" % (_hm(at), rec.status_text(), reason.strip()))
        return {"name": user.full_name, "status": rec.status_text()}

    def confirm_session(self, admin: User, session_id: int, check_out: str, reason: str) -> None:
        require(admin, "session.confirm")
        ws = self.repo.get(WorkSession, session_id)
        if not ws or ws.flag is not SessionFlag.MISSING_CHECKOUT:
            raise RuleViolation("This session no longer needs confirmation.")
        try:
            t = dt.datetime.combine(ws.planned_end.date(), dt.time.fromisoformat(check_out))
        except (TypeError, ValueError):
            raise RuleViolation("Enter the real check-out time, for example 16:45.")
        old = "Missing check-out (auto-closed at %s)" % _hm(ws.check_out)
        ws.confirm(t, reason)
        self.repo.save(ws)
        teacher = self.repo.get(User, ws.teacher_id)
        self.audit.record(admin, "Work session corrected", "%s - %s" % (teacher.full_name, ws.planned_start.strftime("%d %b")),
                          old, "Check-out %s. Reason: %s" % (check_out, ws.note))

    def nightly_close(self) -> int:
        """FR-01.6 job: sessions still open after the end of an earlier day are closed and flagged."""
        now, n = self.clock.now(), 0
        for ws in self.repo.find(WorkSession, flag=SessionFlag.OPEN):
            if ws.planned_end.date() < now.date() and ws.auto_close(now):
                self.repo.save(ws)
                n += 1
                t = self.repo.get(User, ws.teacher_id)
                self.notifier.send_to_role(Administrator, "Missing check-out: %s on %s. Please confirm the real time."
                                           % (t.full_name, ws.planned_start.strftime("%d %b")), "desk")
        return n


class WorkloadService(Service):
    def report(self, user: User, month: str) -> Dict[str, Any]:
        require(user, "workload.view")
        rows = [self._row(t, month) for t in self.repo.find(Teacher, order="id")]
        for r in rows:
            r["difference"] = round(r["worked_h"] - r["planned_h"], 1)
        return {"month": month, "label": month_label(month), "rows": rows}

    def _row(self, t: Teacher, month: str) -> Dict[str, Any]:
        now = self.clock.now()
        lessons = [l for l in self.repo.find(Lesson, teacher_id=t.id) if month_of(l.start) == month and l.end <= now]
        sessions = [s for s in self.repo.find(WorkSession, teacher_id=t.id) if month_of(s.planned_start) == month]
        records = [r for r in self.repo.find(AttendanceRecord, user_id=t.id) if month_of(r.timestamp) == month]
        groups = [g.name for g in self.repo.find(Group) if t.id in g.teacher_ids()]
        return {"id": t.id, "name": t.full_name, "role": t.KIND, "subject": t.subject.value, "groups": ", ".join(groups),
                "lessons": sum(1 for l in lessons if not l.is_support_session),
                "support_sessions": sum(1 for l in lessons if l.is_support_session),
                "planned_h": round(sum(l.duration_minutes() for l in lessons) / 60.0, 1),
                "worked_h": round(sum(s.worked_minutes() for s in sessions) / 60.0, 1),
                "overtime_h": round(sum(s.overtime_minutes() for s in sessions) / 60.0, 1),
                "late": sum(1 for r in records if r.is_late()),
                "left_early": sum(1 for s in sessions if s.flag is SessionFlag.LEFT_EARLY),
                "missing": sum(1 for s in sessions if s.flag is SessionFlag.MISSING_CHECKOUT)}


# --------------------------------------------------------------------------- lesson register
class RegisterService(Service):
    def todays_register(self, teacher: User, lesson_id: Optional[int] = None) -> Dict[str, Any]:
        require(teacher, "register.keep")
        lessons = [l for l in self.ctx.attendance.lessons_today(teacher, self.clock.today()) if not l.is_support_session]
        if not lessons:
            raise RuleViolation("You have no group lesson today, so there is no register to keep.")
        lesson = next((l for l in lessons if l.id == lesson_id), None) if lesson_id else None
        if lesson is None:
            lesson = next((l for l in lessons if not self._reg(l) or not self._reg(l).locked), lessons[0])
        reg = self.register_for(lesson)
        return self.view(reg, lesson, lessons)

    def _reg(self, lesson: Lesson) -> Optional[LessonRegister]:
        return self.repo.first(LessonRegister, lesson_id=lesson.id)

    def register_for(self, lesson: Lesson) -> LessonRegister:
        reg = self._reg(lesson)
        if reg:
            return reg
        entries = []
        for s in self.repo.find(Student, order="id", group_id=lesson.group_id):
            if not s.is_active():
                continue
            rec = self.repo.first(AttendanceRecord, user_id=s.id, lesson_id=lesson.id)
            entries.append(RegisterEntry(student_id=s.id, scanned=bool(rec), scan_time=_hm(rec.timestamp) if rec else "",
                                         status=(AttendanceStatus.LATE if rec.is_late() else AttendanceStatus.PRESENT) if rec else None))
        return self.repo.save(LessonRegister(lesson_id=lesson.id, teacher_id=lesson.teacher_id, entries=entries))

    def prefill(self, lesson: Lesson, student: Student, rec: AttendanceRecord) -> None:
        reg = self._reg(lesson)
        if reg and not reg.locked:
            e = reg.entry(student.id)
            e.scanned, e.scan_time, e.teacher_override = True, _hm(rec.timestamp), False
            e.status = AttendanceStatus.LATE if rec.is_late() else AttendanceStatus.PRESENT
            self.repo.save(reg)

    def view(self, reg: LessonRegister, lesson: Lesson, lessons: List[Lesson]) -> Dict[str, Any]:
        rows = []
        for e in reg.entries:
            s = self.repo.get(Student, e.student_id)
            pending = self.repo.first(CorrectionRequest, register_id=reg.id, student_id=s.id, status=CorrectionStatus.PENDING)
            rows.append({"student_id": s.id, "name": s.full_name, "status": e.status.value if e.status else None,
                         "reason": e.reason, "scanned": e.scanned, "scan_time": e.scan_time, "override": e.teacher_override,
                         "absence_run": s.consecutive_absences, "pending_correction": pending.new_status.value if pending else None})
        return {"register_id": reg.id, "locked": reg.locked, "submitted_at": _hm(reg.submitted_at),
                "late_submission": reg.late_submission, "lesson": self.lesson_title(lesson), "lesson_id": lesson.id,
                "lessons": [{"id": l.id, "title": self.lesson_title(l)} for l in lessons], "rows": rows,
                "statuses": [s.value for s in AttendanceStatus]}

    def _own(self, teacher: User, register_id: int) -> LessonRegister:
        require(teacher, "register.keep")
        reg = self.repo.get(LessonRegister, register_id)
        if not reg or reg.teacher_id != teacher.id:
            raise PermissionError("You can only keep the registers of your own lessons.")
        return reg

    @staticmethod
    def _status(value: str) -> AttendanceStatus:
        try:
            return AttendanceStatus(value)
        except ValueError:
            raise RuleViolation("Unknown status.")

    def set_status(self, teacher: User, register_id: int, student_id: int, status: str, reason: str = "") -> None:
        reg = self._own(teacher, register_id)
        reg.set_status(student_id, self._status(status), reason or "")
        self.repo.save(reg)

    def submit(self, teacher: User, register_id: int) -> Dict[str, Any]:
        reg = self._own(teacher, register_id)
        lesson = self.repo.get(Lesson, reg.lesson_id)
        reg.submit(self.clock.now(), lesson.start.date())
        self.repo.save(reg)
        alerts = []
        for e in reg.entries:
            s = self.repo.get(Student, e.student_id)
            if e.affects_absence_run():
                run = s.update_absence_run(e.status)
                self.repo.save(s)
                if e.status is AttendanceStatus.ABSENT and run >= 3:      # UC-02 7a, FR-05.2
                    self.ctx.risk.evaluate_alerts(s, "%d consecutive absences (last on %s)" % (run, lesson.start.strftime("%d %b")))
                    alerts.append(s.full_name)
            if e.teacher_override:
                self.audit.record(teacher, "Teacher override", "%s - %s" % (s.full_name, self.lesson_title(lesson)),
                                  "Not scanned", e.status.value)
        counts = {}
        for e in reg.entries:
            counts[e.status.value] = counts.get(e.status.value, 0) + 1
        self.audit.record(teacher, "Register submitted", self.lesson_title(lesson) + " " + lesson.start.strftime("%d %b"), "",
                          ", ".join("%s %d" % kv for kv in counts.items()) + (" (late submission)" if reg.late_submission else ""))
        self.ctx.gateway.push_register(reg)                                     # NFR-05.2
        return {"alerts": alerts}

    def request_correction(self, teacher: User, register_id: int, student_id: int, status: str, reason: str) -> None:
        reg = self._own(teacher, register_id)
        if not reg.locked:
            raise RuleViolation("The register is not submitted yet, so you can change the status directly.")
        new = self._status(status)
        old = reg.entry(student_id).status
        if new is old:
            raise RuleViolation("The register already shows %s." % new.value)
        if not (reason or "").strip():
            raise RuleViolation("Explain why the register needs correcting.")
        if self.repo.first(CorrectionRequest, register_id=reg.id, student_id=student_id, status=CorrectionStatus.PENDING):
            raise RuleViolation("A correction for this student is already waiting for the Head Specialist.")
        self.repo.save(CorrectionRequest(register_id=reg.id, student_id=student_id, old_status=old, new_status=new,
                                         reason=reason.strip(), requested_by=teacher.id, requested_at=self.clock.now()))
        s = self.repo.get(User, student_id)
        self.notifier.send_to_role(HeadSpecialist, "%s asks to correct %s: %s to %s." % (teacher.full_name, s.full_name, old.value, new.value), "approvals")
        self.audit.record(teacher, "Correction requested", s.full_name, old.value, new.value + ". Reason: " + reason.strip())

    def pending_corrections(self, user: User) -> List[Dict[str, Any]]:
        require(user, "correction.decide")
        out = []
        for c in self.repo.find(CorrectionRequest, order="-id", status=CorrectionStatus.PENDING):
            reg = self.repo.get(LessonRegister, c.register_id)
            lesson = self.repo.get(Lesson, reg.lesson_id)
            out.append({"id": c.id, "student": self.repo.get(User, c.student_id).full_name,
                        "lesson": self.lesson_title(lesson) + ", " + lesson.start.strftime("%a %d %b"),
                        "old": c.old_status.value if c.old_status else "-", "new": c.new_status.value, "reason": c.reason,
                        "teacher": self.repo.get(User, c.requested_by).full_name,
                        "requested": c.requested_at.strftime("%d %b %H:%M")})
        return out

    def decide(self, hs: User, request_id: int, approve: bool) -> None:
        require(hs, "correction.decide")
        c = self.repo.get(CorrectionRequest, request_id)
        if not c:
            raise LookupError("Unknown request")
        c.decide(approve, hs.id, self.clock.now())
        self.repo.save(c)
        s = self.repo.get(Student, c.student_id)
        teacher = self.repo.get(User, c.requested_by)
        if approve:
            reg = self.repo.get(LessonRegister, c.register_id)
            old = reg.apply_correction(c.student_id, c.new_status)
            self.repo.save(reg)
            self._rerun(s, reg, old, c.new_status)
            self.audit.record(hs, "Register correction approved", s.full_name, old.value if old else "-",
                              "%s (requested by %s)" % (c.new_status.value, teacher.full_name))
        else:
            self.audit.record(hs, "Register correction rejected", s.full_name, c.old_status.value if c.old_status else "-",
                              "unchanged (requested %s by %s)" % (c.new_status.value, teacher.full_name))
        self.notifier.send(teacher, "Your correction for %s was %s." % (s.full_name, "approved" if approve else "rejected"), "register")

    def _rerun(self, s: Student, reg: LessonRegister, old: Optional[AttendanceStatus], new: AttendanceStatus) -> None:
        """Keep the absence run right when the corrected register is the student's latest one."""
        regs = [r for r in self.repo.find(LessonRegister, locked=True) if any(e.student_id == s.id for e in r.entries)]
        latest = max(regs, key=lambda r: self.repo.get(Lesson, r.lesson_id).start)
        if latest.id != reg.id:
            return
        if new is AttendanceStatus.ABSENT and old is not AttendanceStatus.ABSENT:
            s.consecutive_absences += 1
        elif new in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            s.consecutive_absences = 0
        elif old is AttendanceStatus.ABSENT:
            s.consecutive_absences = max(0, s.consecutive_absences - 1)
        self.repo.save(s)


# --------------------------------------------------------------------------- dropout risk
class RiskService(Service):
    POINTS = {"absences": 40, "feedback": 30, "circumstance": 20, "debt": 15}

    def teachers_of(self, s: Student) -> List[int]:
        return self.group_of(s).teacher_ids()

    def has_negative_feedback(self, s: Student, month: str) -> bool:
        teachers = self.teachers_of(s)
        low = {f.teacher_id for f in self.repo.find(TeacherFeedback, student_id=s.id, month=month) if f.is_negative()}
        return bool(teachers) and len(low) > len(teachers) / 2.0

    def circumstances(self, s: Student) -> List[StudentCircumstance]:
        since = self.clock.today() - dt.timedelta(days=90)
        return [c for c in self.repo.find(StudentCircumstance, order="-id", student_id=s.id)
                if c.is_financial_or_social() and c.recorded_on >= since]

    def calculate_score(self, s: Student) -> DropoutRisk:
        today, now = self.clock.today(), self.clock.now()
        if s.enrolled_on and (today - s.enrolled_on).days < 14:      # FR-05.3
            risk = DropoutRisk(student_id=s.id, level=RiskLevel.NOT_ENOUGH_DATA, calculated_at=now,
                               factors=["Enrolled %s - less than two weeks of data" % s.enrolled_on.strftime("%d %b")])
        else:
            w = []
            if s.consecutive_absences:
                w.append(("%d consecutive absence(s)" % s.consecutive_absences,
                          self.POINTS["absences"] * min(s.consecutive_absences / 3.0, 1)))
            if self.has_negative_feedback(s, self.clock.month()):
                w.append(("Low ratings for most of their teachers this month", self.POINTS["feedback"]))
            for c in self.circumstances(s)[:1]:
                w.append(("%s circumstance: %s" % (c.category, c.note), self.POINTS["circumstance"]))
            if s.tuition_debt:
                w.append(("Unpaid tuition (EduTizim balance)", self.POINTS["debt"]))
            risk = DropoutRisk.from_factors(s.id, w, now)
        old = self.repo.first(DropoutRisk, student_id=s.id)
        if old:
            risk.id = old.id
        return self.repo.save(risk)

    def open_alert(self, student_id: int) -> Optional[RiskAlert]:
        return next((a for a in self.repo.find(RiskAlert, order="-id", student_id=student_id) if a.is_open()), None)

    def evaluate_alerts(self, s: Student, reason: str) -> RiskAlert:
        """Raise an alert, or add the reason to the open one (FR-05.2, UC-02 7b), then notify (FR-05.3)."""
        self.calculate_score(s)
        alert = self.open_alert(s.id)
        if alert is None:
            alert = self.repo.save(RiskAlert(student_id=s.id, reasons=[reason], raised_at=self.clock.now()))
            msg = "Dropout alert raised for %s (%s): %s." % (s.full_name, self.group_of(s).name, reason)
            self.audit.record(None, "Dropout alert raised", s.full_name, "", reason)
        else:
            alert.add_reason(reason)
            self.repo.save(alert)
            msg = "New reason added to the open alert for %s: %s." % (s.full_name, reason)
        for role in (Administrator, HeadSpecialist):
            self.notifier.send_to_role(role, msg, "risk")
        return alert

    def daily_refresh(self) -> None:
        for s in self.repo.find(Student, status=StudentStatus.ACTIVE):
            self.calculate_score(s)

    def dashboard(self, user: User, subject: str = "", level: str = "") -> Dict[str, Any]:
        require(user, "risk.view")
        today = self.clock.today()
        groups = {g.id: g for g in self.repo.find(Group)}
        rows, overdue = [], 0
        for s in self.repo.find(Student, order="full_name"):
            if not s.is_active():
                continue
            g = groups[s.group_id]
            risk = self.repo.first(DropoutRisk, student_id=s.id) or self.calculate_score(s)
            alerts = self.repo.find(RiskAlert, order="-id", student_id=s.id)
            alert = alerts[0] if alerts else None
            od = bool(alert and alert.is_open() and any(f.is_overdue(today) for f in alert.follow_ups))
            overdue += od
            rows.append({"id": s.id, "name": s.full_name, "subject": g.subject.value, "group": g.name,
                         "absences": s.consecutive_absences, "score": risk.score, "level": risk.level.value,
                         "alert": alert.status.value if alert else "", "overdue": od})
        counts = {lv.value: sum(1 for r in rows if r["level"] == lv.value) for lv in RiskLevel}
        open_alerts = sum(1 for a in self.repo.find(RiskAlert) if a.is_open())
        if subject:
            rows = [r for r in rows if r["subject"] == subject]
        if level:
            rows = [r for r in rows if r["level"] == level]
        rank = {"High": 0, "Medium": 1, "Low": 2, "Not enough data": 3}
        rows.sort(key=lambda r: (rank[r["level"]], -r["score"], r["name"]))
        month = self.clock.month()
        sync = self.ctx.gateway.last_sync()
        return {"rows": rows, "counts": counts, "open_alerts": open_alerts, "overdue": overdue,
                "dropout": self.ctx.retention.stats(self.repo.find(Student), month), "month": month_label(month),
                "last_sync": sync.strftime("%d %b %H:%M") if sync else None,
                "subjects": [x.value for x in Subject], "levels": [x.value for x in RiskLevel],
                "can_follow_up": user.can("risk.followup"), "can_record": user.can("student.record")}

    def detail(self, user: User, student_id: int) -> Dict[str, Any]:
        require(user, "risk.view")
        s = self.repo.get(Student, student_id)
        if not s:
            raise LookupError("Unknown student")
        risk = self.calculate_score(s)
        g = self.group_of(s)
        today = self.clock.today()
        alerts = self.repo.find(RiskAlert, order="-id", student_id=s.id)
        alert = alerts[0] if alerts else None
        name = lambda uid: self.repo.get(User, uid).full_name
        fb = self.repo.find(TeacherFeedback, student_id=s.id, month=self.clock.month())
        assignees = [self.repo.first(Administrator), self.repo.first(HeadSpecialist)] + [self.repo.get(User, t) for t in g.teacher_ids()]
        return {"id": s.id, "name": s.full_name, "group": g.name, "subject": g.subject.value, "level_of_study": s.level,
                "enrolled": s.enrolled_on.strftime("%d %b %Y") if s.enrolled_on else "", "active": s.is_active(),
                "score": risk.score, "level": risk.level.value, "factors": risk.factors, "absences": s.consecutive_absences,
                "debt": s.tuition_debt,
                "feedback": {"given": len(fb), "low": sum(1 for f in fb if f.is_negative()), "teachers": len(g.teacher_ids())},
                "circumstances": [{"category": c.category, "note": c.note, "on": c.recorded_on.strftime("%d %b")}
                                  for c in self.repo.find(StudentCircumstance, order="-id", student_id=s.id)],
                "alert": alert and {"status": alert.status.value, "open": alert.is_open(), "reasons": alert.reasons,
                                    "raised": alert.raised_at.strftime("%d %b %H:%M"), "note": alert.closed_note,
                                    "follow_ups": [{"description": f.description, "assignee": name(f.assignee_id),
                                                    "due": f.due.strftime("%d %b"), "overdue": f.is_overdue(today), "done": f.done}
                                                   for f in alert.follow_ups]},
                "assignees": [{"id": a.id, "name": "%s (%s)" % (a.full_name, a.ROLE)} for a in assignees if a],
                "withdrawal_reasons": [r.value for r in WithdrawalReason],
                "circumstance_categories": list(StudentCircumstance.CATEGORIES),
                "today": today.isoformat(), "default_due": (today + dt.timedelta(days=3)).isoformat()}

    def assign_follow_up(self, user: User, student_id: int, description: str, assignee_id: int, due: str) -> None:
        require(user, "risk.followup")
        s = self.repo.get(Student, student_id)
        try:
            due_d = dt.date.fromisoformat(due)
        except (TypeError, ValueError):
            raise RuleViolation("Choose a due date for the follow-up.")
        if due_d < self.clock.today():
            raise RuleViolation("The due date cannot be in the past.")
        assignee = self.repo.get(User, int(assignee_id or 0))
        if not assignee:
            raise RuleViolation("Choose who is responsible for the follow-up.")
        alert = self.open_alert(s.id) or RiskAlert(student_id=s.id, raised_at=self.clock.now(),
                                                   reasons=["Flagged by the %s from the dashboard" % user.ROLE])
        alert.assign(FollowUpAction(description=(description or "").strip(), assignee_id=assignee.id, due=due_d, created_by=user.id))
        self.repo.save(alert)
        self.notifier.send(assignee, "Follow-up for %s: %s - due %s." % (s.full_name, description.strip(), due_d.strftime("%d %b")), "risk")
        self.audit.record(user, "Follow-up assigned", s.full_name, "", "%s -> %s, due %s" % (description.strip(), assignee.full_name, due_d.strftime("%d %b")))

    def close_alert(self, user: User, student_id: int, outcome: str, reason: str = "", note: str = "") -> None:
        require(user, "risk.followup")
        s = self.repo.get(Student, student_id)
        if outcome == "returned":
            alert = self.open_alert(s.id)
            if not alert:
                raise RuleViolation("%s has no open alert." % s.full_name)
            alert.close(AlertStatus.RESOLVED, note or "Student returned to lessons")
            self.repo.save(alert)
            self.audit.record(user, "Alert resolved", s.full_name, "In progress / Open", "Resolved - student returned")
        elif outcome == "withdrawn":
            self.record_withdrawal(user, s, reason, note)
        else:
            raise RuleViolation("Unknown outcome.")

    def record_circumstance(self, admin: User, student_id: int, category: str, note: str) -> None:
        require(admin, "student.record")
        s = self.repo.get(Student, student_id)
        if not (note or "").strip():
            raise RuleViolation("Describe the circumstance, for example 'Parent reported job loss'.")
        if category not in StudentCircumstance.CATEGORIES:
            raise RuleViolation("Choose a category.")
        c = self.repo.save(StudentCircumstance(student_id=s.id, category=category, note=note.strip(),
                                               recorded_on=self.clock.today(), recorded_by=admin.id))
        self.audit.record(admin, "Circumstance recorded", s.full_name, "", "%s: %s" % (category, c.note))
        if c.is_financial_or_social():
            self.evaluate_alerts(s, "%s circumstance recorded: %s" % (category, c.note))
        else:
            self.calculate_score(s)

    def record_withdrawal(self, user: User, s: Student, reason: str, note: str = "") -> None:
        if not (user.can("student.record") or user.can("risk.followup")):
            raise PermissionError("Your role cannot record withdrawals.")
        if not s.is_active():
            raise RuleViolation("%s has already left." % s.full_name)
        try:
            r = WithdrawalReason(reason)
        except ValueError:
            raise RuleViolation("Choose the reason the student left (FR-05.5).")
        self.repo.save(Withdrawal(student_id=s.id, date=self.clock.today(), reason=r, note=(note or "").strip(), recorded_by=user.id))
        s.withdraw()
        self.repo.save(s)
        alert = self.open_alert(s.id)
        if alert:
            alert.close(AlertStatus.CLOSED_WITHDRAWN, r.value)
            self.repo.save(alert)
        self.audit.record(user, "Withdrawal recorded", s.full_name, "Active", "Withdrawn - %s" % r.value)
        if not isinstance(user, Administrator):
            self.notifier.send_to_role(Administrator, "%s recorded that %s left (%s). Please update EduTizim enrolment."
                                       % (user.full_name, s.full_name, r.value), "risk")

    def export_csv(self, user: User) -> str:
        d = self.dashboard(user)
        lines = ["Student,Subject,Group,Absence run,Score,Level,Alert"]
        lines += ["%s,%s,%s,%d,%d,%s,%s" % (r["name"], r["subject"], r["group"], r["absences"], r["score"], r["level"], r["alert"])
                  for r in d["rows"]]
        return "\n".join(lines)


class FeedbackService(Service):
    def my_teachers(self, student: User) -> Dict[str, Any]:
        require(student, "feedback.give")
        month = self.clock.month()
        out = []
        for tid in self.ctx.risk.teachers_of(student):
            t = self.repo.get(Teacher, tid)
            fb = self.repo.first(TeacherFeedback, student_id=student.id, teacher_id=tid, month=month)
            out.append({"id": t.id, "name": t.full_name, "role": "%s teacher, %s" % (t.KIND, t.subject.value),
                        "given": fb.rating if fb else None})
        return {"month": month_label(month), "teachers": out}

    def submit(self, student: User, teacher_id: int, rating: Any, comment: str = "") -> Dict[str, Any]:
        require(student, "feedback.give")
        month = self.clock.month()
        if int(teacher_id) not in self.ctx.risk.teachers_of(student):
            raise RuleViolation("You can only rate teachers who teach you (FR-04.4).")
        t = self.repo.get(Teacher, int(teacher_id))
        if self.repo.first(TeacherFeedback, student_id=student.id, teacher_id=t.id, month=month):
            raise RuleViolation("You have already rated %s this month. You can rate again next month." % t.full_name)
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise RuleViolation("Choose a rating from 1 to 5.")
        fb = self.repo.save(TeacherFeedback(student_id=student.id, teacher_id=t.id, month=month, rating=rating,
                                            comment=(comment or "").strip()))
        alert = False
        if fb.is_negative() and self.ctx.risk.has_negative_feedback(student, month):
            self.ctx.risk.evaluate_alerts(student, "Low ratings (1-2) for most of their teachers this month")
            alert = True
        return {"alert": alert}

    def summary(self, teacher_id: int, month: str) -> Dict[str, Any]:
        """Teachers and the KPI only ever see the average and anonymous comments (FR-04.4)."""
        fb = self.repo.find(TeacherFeedback, teacher_id=teacher_id, month=month)
        avg = round(sum(f.rating for f in fb) / len(fb), 2) if fb else None
        return {"count": len(fb), "average": avg, "comments": [f.comment for f in fb if f.comment][-4:]}


# --------------------------------------------------------------------------- KPI
class KpiService(Service):
    def weights_for(self, month: str) -> Optional[KpiWeights]:
        rows = [w for w in self.repo.find(KpiWeights) if w.effective_from <= month]
        return max(rows, key=lambda w: w.effective_from) if rows else None

    def weights_view(self, hs: User) -> Dict[str, Any]:
        require(hs, "kpi.weights")
        cur = self.clock.month()
        nxt = shift_month(cur, 1)
        current, scheduled = self.weights_for(cur), self.repo.first(KpiWeights, effective_from=nxt)
        ratings = []
        for t in self.repo.find(Teacher, order="id"):
            r = self.repo.first(MonthlyRating, teacher_id=t.id, month=cur)
            ratings.append({"id": t.id, "name": t.full_name, "role": t.KIND, "score": r.score if r else None})
        return {"labels": INDICATOR_LABELS, "keys": [k for k, _ in INDICATORS], "current": current.as_dict(),
                "scheduled": scheduled.as_dict() if scheduled else None, "current_month": month_label(cur),
                "next_month": month_label(nxt), "ratings": ratings}

    def save_weights(self, hs: User, values: Dict[str, Any]) -> None:
        require(hs, "kpi.weights")
        eff = shift_month(self.clock.month(), 1)        # FR-04.1: new weights apply from next month
        w = KpiWeights.from_dict(values, eff, hs.id)
        old = self.weights_for(eff)
        existing = self.repo.first(KpiWeights, effective_from=eff)
        if existing:
            w.id = existing.id
        self.repo.save(w)
        self.audit.record(hs, "KPI weights changed", "Weights from " + month_label(eff), old.as_dict() if old else "", w.as_dict())

    def rate(self, hs: User, teacher_id: int, score: Any) -> None:
        require(hs, "kpi.rate")
        month = self.clock.month()
        try:
            score = float(score)
        except (TypeError, ValueError):
            raise RuleViolation("A rating must be a number between 0 and 10 (FR-04.2).")
        r = MonthlyRating(teacher_id=int(teacher_id), month=month, score=score)
        old = self.repo.first(MonthlyRating, teacher_id=r.teacher_id, month=month)
        if old:
            r.id = old.id
        self.repo.save(r)
        t = self.repo.get(User, r.teacher_id)
        self.audit.record(hs, "Monthly rating", "%s - %s" % (t.full_name, month_label(month)), str(old.score) if old else "", str(score))

    # ---- indicators
    def punctuality(self, t: Teacher, month: str) -> Optional[float]:
        sessions = [s for s in self.repo.find(WorkSession, teacher_id=t.id) if month_of(s.planned_start) == month]
        if not sessions:
            return None
        late = sum(1 for r in self.repo.find(AttendanceRecord, user_id=t.id) if month_of(r.timestamp) == month and r.is_late())
        missing = sum(1 for s in sessions if s.flag is SessionFlag.MISSING_CHECKOUT)
        n = len(sessions)
        return float(max(0, round(100.0 * (n - late - 0.5 * missing) / n)))

    def achievements_score(self, t: Teacher, month: str) -> float:
        end = month_start(shift_month(month, 1))
        start = end - dt.timedelta(days=365)
        pts = sum(a.points for a in self.repo.find(Achievement, teacher_id=t.id)
                  if a.verified and a.verified_on and start <= a.verified_on < end)
        return float(min(100, pts))

    def indicators(self, t: Teacher, month: str) -> List[KpiIndicator]:
        agg = self.ctx.gateway.monthly_aggregates(t.id, month)
        fb = self.repo.find(TeacherFeedback, teacher_id=t.id, month=month)
        rating = self.repo.first(MonthlyRating, teacher_id=t.id, month=month)
        punct = self.punctuality(t, month)
        ret = self.ctx.retention.teacher_retention(t, month)
        enough_fb = len(fb) >= 5                       # FR-04.5: fewer than five responses -> left out
        return [
            KpiIndicator("results", agg.get("results"), t.records_tests() and agg.get("results") is not None),
            KpiIndicator("attendance", agg.get("attendance"), agg.get("attendance") is not None),
            KpiIndicator("punctuality", punct, punct is not None),
            KpiIndicator("retention", ret, ret is not None),
            KpiIndicator("feedback", round(sum(f.rating for f in fb) / len(fb) * 20, 1) if enough_fb else None, enough_fb),
            KpiIndicator("rating", rating.score * 10 if rating else None, True),
            KpiIndicator("achievements", self.achievements_score(t, month), True),
        ]

    def calculate(self, t: Teacher, month: str, fresh: bool = False) -> KpiSnapshot:
        if not fresh:
            stored = self.repo.first(KpiSnapshot, teacher_id=t.id, month=month)
            if stored and stored.locked:               # past appraisals never change (FR-04.1, FR-04.6)
                return stored
        weights = self.weights_for(month)
        if not weights or not weights.is_valid():      # UC-04 2a
            self.notifier.send_to_role(HeadSpecialist, "KPI weights for %s are missing or do not total 100%%." % month_label(month), "weights")
            raise RuleViolation("KPI weights are not configured for %s. The Head Specialist has been notified." % month_label(month))
        inds = self.indicators(t, month)
        rating_missing = next(i for i in inds if i.name == "rating").value is None
        provisional = month >= self.clock.month() or rating_missing
        return KpiSnapshot.compose(t.id, month, inds, weights, provisional)

    def close_month(self, month: str) -> int:
        """Month-end job: locks a snapshot for every teacher whose KPI is complete."""
        n = 0
        for t in self.repo.find(Teacher):
            if self.repo.first(KpiSnapshot, teacher_id=t.id, month=month, locked=True):
                continue
            snap = self.calculate(t, month, fresh=True)
            if snap.label is not KpiLabel.PROVISIONAL:
                snap.lock()
                self.repo.save(snap)
                n += 1
        return n

    def months(self) -> List[Dict[str, str]]:
        cur = self.clock.month()
        return [{"value": shift_month(cur, -i), "label": month_label(shift_month(cur, -i)) + (" (month to date)" if i == 0 else " (closed)")}
                for i in range(6)]

    def _row(self, t: Teacher, month: str) -> Dict[str, Any]:
        snap = self.calculate(t, month)
        try:
            prev = self.calculate(t, shift_month(month, -1)).overall
        except RuleViolation:
            prev = None
        vals = {i.name: (round(i.value) if i.counts() else None) for i in snap.indicators}
        return {"id": t.id, "name": t.full_name, "subject": t.subject.value, "role": t.KIND, "values": vals,
                "overall": snap.overall, "label": snap.label.value, "change": round(snap.overall - prev, 1) if prev is not None else None}

    def table(self, user: User, month: str, subject: str = "", kind: str = "") -> Dict[str, Any]:
        require(user, "kpi.view_all")
        rows = [self._row(t, month) for t in self.repo.find(Teacher)
                if (not subject or t.subject.value == subject) and (not kind or t.KIND == kind)]
        rows.sort(key=lambda r: -r["overall"])
        return {"month": month, "label": month_label(month), "rows": rows, "keys": [k for k, _ in INDICATORS],
                "labels": INDICATOR_LABELS, "months": self.months(), "subjects": [s.value for s in Subject]}

    def detail(self, user: User, teacher_id: int, month: str) -> Dict[str, Any]:
        if not (user.can("kpi.view_all") or (user.can("kpi.own") and user.id == teacher_id)):
            raise PermissionError("Teachers can only see their own KPI (NFR-01.1).")
        t = self.repo.get(Teacher, teacher_id)
        snap = self.calculate(t, month)
        eff = snap.effective_weights()
        trend = []
        for i in range(5, -1, -1):
            m = shift_month(month, -i)
            try:
                s = self.calculate(t, m)
                trend.append({"month": month_label(m)[:3], "overall": s.overall, "label": s.label.value})
            except RuleViolation:
                pass
        groups = [g for g in self.repo.find(Group) if t.id in g.teacher_ids()]
        achievements = [{"title": a.title, "kind": a.kind, "points": a.points, "verified": a.verified,
                         "on": (a.verified_on or a.submitted_on).strftime("%d %b %Y")}
                        for a in self.repo.find(Achievement, order="-id", teacher_id=t.id)]
        return {"id": t.id, "name": t.full_name, "role": t.KIND, "subject": t.subject.value, "month": month_label(month),
                "overall": snap.overall, "label": snap.label.value, "locked": snap.locked,
                "breakdown": [{"key": i.name, "label": INDICATOR_LABELS[i.name], "value": i.value, "counts": i.counts(),
                               "applicable": i.applicable, "weight": snap.weights_used.get(i.name), "effective": eff.get(i.name)}
                              for i in snap.indicators],
                "trend": trend,
                "groups": [{"name": g.name, **self.ctx.retention.stats(self.repo.find(Student, group_id=g.id), month)} for g in groups],
                "feedback": self.ctx.feedback.summary(t.id, month), "achievements": achievements,
                "achievement_kinds": list(Achievement.POINTS)}

    def export_csv(self, user: User, month: str) -> str:
        d = self.table(user, month)
        head = ["Rank", "Teacher", "Subject", "Role"] + [INDICATOR_LABELS[k] for k in d["keys"]] + ["KPI", "Status"]
        lines = [",".join(head)]
        for i, r in enumerate(d["rows"], 1):
            vals = ["" if r["values"][k] is None else str(r["values"][k]) for k in d["keys"]]
            lines.append(",".join([str(i), r["name"], r["subject"], r["role"]] + vals + [str(r["overall"]), r["label"]]))
        return "\n".join(lines)

    # ---- achievements (FR-04.3)
    def add_achievement(self, teacher: User, title: str, kind: str, proof_file: str) -> None:
        require(teacher, "achievement.add")
        if not (title or "").strip():
            raise RuleViolation("Enter the achievement, for example 'IELTS 8.0 (Academic)'.")
        if kind not in Achievement.POINTS:
            raise RuleViolation("Choose the type of achievement.")
        if not (proof_file or "").strip():
            raise RuleViolation("Attach proof (a scan of the certificate).")
        self.repo.save(Achievement(teacher_id=teacher.id, title=title.strip(), kind=kind, proof_file=proof_file.strip(),
                                   points=Achievement.POINTS[kind], submitted_on=self.clock.today()))
        self.notifier.send_to_role(HeadSpecialist, "%s added an achievement to verify: %s." % (teacher.full_name, title.strip()), "approvals")
        self.audit.record(teacher, "Achievement added", teacher.full_name, "", title.strip())

    def pending_achievements(self, hs: User) -> List[Dict[str, Any]]:
        require(hs, "achievement.verify")
        return [{"id": a.id, "teacher": self.repo.get(User, a.teacher_id).full_name, "title": a.title, "kind": a.kind,
                 "points": a.points, "proof": a.proof_file, "submitted": a.submitted_on.strftime("%d %b")}
                for a in self.repo.find(Achievement, order="-id", verified=False)]

    def verify_achievement(self, hs: User, achievement_id: int) -> None:
        require(hs, "achievement.verify")
        a = self.repo.get(Achievement, achievement_id)
        a.verify(hs.id, self.clock.today())
        self.repo.save(a)
        t = self.repo.get(User, a.teacher_id)
        self.audit.record(hs, "Achievement verified", t.full_name, "Pending", "Verified: %s (+%d)" % (a.title, a.points))
        self.notifier.send(t, "Your achievement '%s' was verified and now counts in your KPI." % a.title, "mykpi")


# --------------------------------------------------------------------------- retention & acquisition
class RetentionService(Service):
    def _withdrawals(self) -> Dict[int, Withdrawal]:
        return {w.student_id: w for w in self.repo.find(Withdrawal)}

    def stats(self, students: List[Student], month: str) -> Dict[str, Any]:
        """FR-05.6: of the students active at the start of the month, how many were still active at the end."""
        start, nxt = month_start(month), month_start(shift_month(month, 1))
        wd = self._withdrawals()
        at_start = [s for s in students if s.enrolled_on and s.enrolled_on < start
                    and (s.id not in wd or wd[s.id].date >= start)]
        left = [s for s in at_start if s.id in wd and wd[s.id].counts_in_dropout_rate(month)]
        rate = round(100.0 * len(left) / len(at_start), 1) if at_start else 0.0
        return {"active_start": len(at_start), "left": len(left), "rate": rate, "retention": round(100 - rate, 1)}

    def _students_of_teacher(self, t: Teacher) -> List[Student]:
        gids = [g.id for g in self.repo.find(Group) if t.id in g.teacher_ids()]
        return [s for s in self.repo.find(Student) if s.group_id in gids]

    def teacher_retention(self, t: Teacher, month: str) -> Optional[float]:
        st = self.stats(self._students_of_teacher(t), month)
        return st["retention"] if st["active_start"] else None

    def _subject_students(self, subject: Subject) -> List[Student]:
        gids = [g.id for g in self.repo.find(Group) if g.subject is subject]
        return [s for s in self.repo.find(Student) if s.group_id in gids]

    def dashboard(self, user: User, month: str) -> Dict[str, Any]:
        require(user, "retention.view")
        everyone = self.repo.find(Student)
        subjects = [{"subject": s.value, **self.stats(self._subject_students(s), month)} for s in Subject]
        teachers = [{"name": t.full_name, "role": t.KIND, "subject": t.subject.value, **self.stats(self._students_of_teacher(t), month)}
                    for t in self.repo.find(Teacher, order="id")]
        trend = [{"month": month_label(shift_month(month, -i))[:3], **self.stats(everyone, shift_month(month, -i))} for i in range(5, -1, -1)]
        reasons: Dict[str, int] = {}
        for w in self.repo.find(Withdrawal):
            if month_start(shift_month(month, -5)) <= w.date < month_start(shift_month(month, 1)):
                reasons[w.reason.value] = reasons.get(w.reason.value, 0) + 1
        return {"month": month_label(month), "total": self.stats(everyone, month), "subjects": subjects, "teachers": teachers,
                "trend": trend, "reasons": reasons, "acquisition": self.acquisition(month), "months": self.ctx.kpi.months()}

    def acquisition(self, month: str) -> List[Dict[str, Any]]:
        """FR-05.7: marketing spend divided by new students in that subject."""
        out = []
        for subj in Subject:
            spend = self.repo.first(MarketingSpend, subject=subj, month=month)
            new = sum(1 for s in self._subject_students(subj) if s.enrolled_on and month_of(s.enrolled_on) == month)
            amount = spend.amount if spend else 0.0
            out.append({"subject": subj.value, "spend": amount, "new": new, "cost": round(amount / new) if new else None})
        return out

    def marketing(self, admin: User) -> Dict[str, Any]:
        require(admin, "marketing.edit")
        cur = self.clock.month()
        months = [shift_month(cur, -i) for i in range(5, -1, -1)]
        hist = []
        for subj in Subject:
            row = {"subject": subj.value, "values": []}
            for m in months:
                sp = self.repo.first(MarketingSpend, subject=subj, month=m)
                row["values"].append(sp.amount if sp else 0)
            hist.append(row)
        return {"month": month_label(cur), "months": [month_label(m)[:3] for m in months], "history": hist,
                "acquisition": self.acquisition(cur)}

    def set_spend(self, admin: User, subject: str, amount: Any) -> None:
        require(admin, "marketing.edit")
        try:
            subj, amount = Subject(subject), float(amount)
        except (TypeError, ValueError):
            raise RuleViolation("Enter the amount spent in USD.")
        if amount < 0:
            raise RuleViolation("Spend cannot be negative.")
        month = self.clock.month()
        sp = self.repo.first(MarketingSpend, subject=subj, month=month) or MarketingSpend(subject=subj, month=month)
        old = sp.amount if sp.id else ""
        sp.amount, sp.entered_by = amount, admin.id
        self.repo.save(sp)
        self.audit.record(admin, "Marketing spend", "%s - %s" % (subj.value, month_label(month)), str(old), str(amount))


# --------------------------------------------------------------------------- container
class Services:
    def __init__(self, repo: Repository, gateway: EduTizimGateway, clock: Clock, audit: AuditLog):
        self.repo, self.gateway, self.clock, self.audit = repo, gateway, clock, audit
        self.notifier = NotificationService(repo, clock)
        self.qr = QrTokenService(self)
        self.attendance = AttendanceService(self)
        self.workload = WorkloadService(self)
        self.registers = RegisterService(self)
        self.risk = RiskService(self)
        self.feedback = FeedbackService(self)
        self.kpi = KpiService(self)
        self.retention = RetentionService(self)
