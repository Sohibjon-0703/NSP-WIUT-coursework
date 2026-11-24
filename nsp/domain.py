"""«package» domain - entities and the rules they own (report Figures 2.2, 2.3, 2.4).

Every class here is a plain object with private-by-convention state and public
methods that enforce its own rules, so a rule cannot be bypassed by a caller
(for example, the absence run only changes through Student.update_absence_run).
"""
from __future__ import annotations

import datetime as dt
import enum
import hashlib
import hmac
from dataclasses import dataclass, field
from typing import ClassVar, Dict, FrozenSet, List, Optional, Tuple


class RuleViolation(Exception):
    """A business rule was broken. The message tells the user what to do next (NFR-03.5)."""


# --------------------------------------------------------------------------- months
def month_of(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def shift_month(month: str, n: int) -> str:
    y, m = int(month[:4]), int(month[5:7])
    i = y * 12 + (m - 1) + n
    return "%04d-%02d" % (i // 12, i % 12 + 1)


def month_start(month: str) -> dt.date:
    return dt.date(int(month[:4]), int(month[5:7]), 1)


def month_label(month: str) -> str:
    return month_start(month).strftime("%B %Y")


# --------------------------------------------------------------------------- enumerations
class Language(enum.Enum):
    UZ = "Uzbek (Latin)"
    RU = "Russian"


class Subject(enum.Enum):
    CHEMISTRY = "Chemistry"
    BIOLOGY = "Biology"
    ENGLISH = "English"


class StudentStatus(enum.Enum):
    ACTIVE = "Active"
    WITHDRAWN = "Withdrawn"


class AttendanceStatus(enum.Enum):
    PRESENT = "Present"
    LATE = "Late"
    ABSENT = "Absent"
    ABSENT_EXCUSED = "Absent - excused"


class AttendanceMethod(enum.Enum):
    QR = "QR"
    MANUAL = "Manual"
    FACE_ID = "Face ID"          # NFR-05.5: can be used later without changing stored records


class SessionFlag(enum.Enum):
    OPEN = "Open"
    CLOSED = "Closed"
    LEFT_EARLY = "Left early"
    MISSING_CHECKOUT = "Missing check-out"
    CONFIRMED = "Confirmed by administrator"


class RiskLevel(enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    NOT_ENOUGH_DATA = "Not enough data"


class AlertStatus(enum.Enum):
    OPEN = "Open"
    IN_PROGRESS = "In progress"
    RESOLVED = "Resolved"
    CLOSED_WITHDRAWN = "Closed - withdrawn"


class KpiLabel(enum.Enum):
    FINAL = "Final"
    PARTIAL = "Partial"
    PROVISIONAL = "Provisional"


class WithdrawalReason(enum.Enum):
    FINANCIAL = "Financial"
    MOVED = "Moved away"
    CHANGED_COURSE = "Changed course"
    NOT_SATISFIED = "Not satisfied"
    OTHER = "Other"


class CorrectionStatus(enum.Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


# --------------------------------------------------------------------------- users (Figure 2.2)
@dataclass
class User:
    """Abstract: every person who signs in is exactly one of the concrete roles below."""
    id: int = 0
    full_name: str = ""
    phone: str = ""
    language: Language = Language.UZ

    ABSTRACT: ClassVar[bool] = True
    ROLE: ClassVar[str] = ""
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset()

    def __post_init__(self):
        if type(self).__dict__.get("ABSTRACT", False):
            raise TypeError(type(self).__name__ + " is abstract")

    def can(self, permission: str) -> bool:
        """Role-based access (NFR-01.1): each subclass declares what it may do."""
        return permission in self.PERMISSIONS

    def login(self) -> bool:
        """Identity belongs to EduTizim (NFR-01.2). The prototype signs in by choosing a user."""
        return True


@dataclass
class Student(User):
    level: str = ""
    status: StudentStatus = StudentStatus.ACTIVE
    consecutive_absences: int = 0
    group_id: int = 0
    enrolled_on: Optional[dt.date] = None
    tuition_debt: bool = False

    ABSTRACT: ClassVar[bool] = False
    ROLE: ClassVar[str] = "Student"
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset({"attendance.self", "feedback.give"})

    def update_absence_run(self, status: AttendanceStatus) -> int:
        """FR-02.6: Absent adds one, Present/Late resets, an excused absence leaves it as it is."""
        if status is AttendanceStatus.ABSENT:
            self.consecutive_absences += 1
        elif status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE):
            self.consecutive_absences = 0
        return self.consecutive_absences

    def is_active(self) -> bool:
        return self.status is StudentStatus.ACTIVE

    def withdraw(self) -> None:
        self.status = StudentStatus.WITHDRAWN


@dataclass
class Teacher(User):
    """Abstract: a teacher is either a MainTeacher or a SupportTeacher."""
    subject: Subject = Subject.CHEMISTRY
    availability: str = ""

    ABSTRACT: ClassVar[bool] = True
    ROLE: ClassVar[str] = "Teacher"
    KIND: ClassVar[str] = ""
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset(
        {"attendance.self", "register.keep", "kpi.own", "achievement.add"})

    def records_tests(self) -> bool:
        """Polymorphic: decides whether the test & mock results KPI indicator applies (FR-04.5)."""
        raise NotImplementedError


@dataclass
class MainTeacher(Teacher):
    ABSTRACT: ClassVar[bool] = False
    KIND: ClassVar[str] = "Main"

    def records_tests(self) -> bool:
        return True


@dataclass
class SupportTeacher(Teacher):
    ABSTRACT: ClassVar[bool] = False
    KIND: ClassVar[str] = "Support"

    def records_tests(self) -> bool:
        return False


@dataclass
class Administrator(User):
    ABSTRACT: ClassVar[bool] = False
    ROLE: ClassVar[str] = "Administrator"
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset(
        {"attendance.manual", "session.confirm", "student.record", "marketing.edit",
         "risk.view", "risk.followup"})


@dataclass
class HeadSpecialist(User):
    ABSTRACT: ClassVar[bool] = False
    ROLE: ClassVar[str] = "Head Specialist"
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset(
        {"kpi.weights", "kpi.rate", "correction.decide", "achievement.verify",
         "kpi.view_all", "risk.view"})


@dataclass
class Director(User):
    ABSTRACT: ClassVar[bool] = False
    ROLE: ClassVar[str] = "Director"
    PERMISSIONS: ClassVar[FrozenSet[str]] = frozenset(
        {"risk.view", "risk.followup", "kpi.view_all", "workload.view",
         "retention.view", "audit.view"})


# --------------------------------------------------------------------------- rooms, groups, lessons
@dataclass
class QrToken:
    """The value shown on the room tablet: a signed room id and time stamp, no personal data (NFR-01.3)."""
    room_id: int
    issued_at: int
    signature: str

    LIFETIME: ClassVar[int] = 60      # FR-01.1: the code changes every 60 seconds
    GRACE: ClassVar[int] = 5          # tolerance for the moment the code is replaced mid-scan

    @staticmethod
    def sign(secret: str, room_id: int, issued_at: int) -> str:
        msg = ("%d.%d" % (room_id, issued_at)).encode()
        return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:16]

    @property
    def value(self) -> str:
        return "NSP1.%d.%d.%s" % (self.room_id, self.issued_at, self.signature)

    @classmethod
    def parse(cls, value: str) -> "QrToken":
        try:
            prefix, room, issued, sig = (value or "").split(".")
            if prefix != "NSP1":
                raise ValueError
            return cls(int(room), int(issued), sig)
        except ValueError:
            raise RuleViolation("This is not an NSP room code. Scan the code on the classroom screen.")

    def is_valid(self, secret: str, now: float) -> bool:
        expected = self.sign(secret, self.room_id, self.issued_at)
        age = now - self.issued_at
        return hmac.compare_digest(expected, self.signature) and -self.GRACE <= age <= self.LIFETIME + self.GRACE


@dataclass
class Room:
    id: int = 0
    name: str = ""
    qr_secret: str = ""

    def current_token(self, now: float) -> QrToken:
        issued = int(now) // QrToken.LIFETIME * QrToken.LIFETIME
        return QrToken(self.id, issued, QrToken.sign(self.qr_secret, self.id, issued))


@dataclass
class Group:
    id: int = 0
    name: str = ""
    subject: Subject = Subject.CHEMISTRY
    capacity: int = 25
    teacher_id: int = 0
    support_teacher_id: Optional[int] = None
    room_id: int = 0

    def has_free_place(self, enrolled: int) -> bool:
        return enrolled < self.capacity

    def teacher_ids(self) -> List[int]:
        return [t for t in (self.teacher_id, self.support_teacher_id) if t]


@dataclass
class Lesson:
    id: int = 0
    group_id: int = 0
    room_id: int = 0
    teacher_id: int = 0
    start: Optional[dt.datetime] = None
    end: Optional[dt.datetime] = None
    is_support_session: bool = False

    CHECK_IN_WINDOW: ClassVar[int] = 30   # FR-01.2: minutes before / after the start
    LATE_AFTER: ClassVar[int] = 10        # FR-01.3

    def is_check_in_open(self, t: dt.datetime) -> bool:
        w = dt.timedelta(minutes=self.CHECK_IN_WINDOW)
        return self.start - w <= t <= self.start + w

    def minutes_late(self, t: dt.datetime) -> int:
        return max(0, int((t - self.start).total_seconds() // 60))

    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


# --------------------------------------------------------------------------- attendance (Figure 2.3)
@dataclass
class AttendanceRecord:
    id: int = 0
    user_id: int = 0
    lesson_id: int = 0
    timestamp: Optional[dt.datetime] = None
    method: AttendanceMethod = AttendanceMethod.QR
    minutes_late: int = 0
    reason: str = ""
    confirmed_by: Optional[int] = None

    def is_late(self) -> bool:
        return self.minutes_late > Lesson.LATE_AFTER

    def status_text(self) -> str:
        return "Late (%d min)" % self.minutes_late if self.is_late() else "On time"


@dataclass
class WorkSession:
    """Opened by a teacher's check-in and closed by the check-out (FR-01.5)."""
    id: int = 0
    teacher_id: int = 0
    lesson_id: int = 0
    check_in: Optional[dt.datetime] = None
    planned_start: Optional[dt.datetime] = None
    planned_end: Optional[dt.datetime] = None
    check_out: Optional[dt.datetime] = None
    flag: SessionFlag = SessionFlag.OPEN
    note: str = ""

    AUTO_CLOSE_AFTER: ClassVar[int] = 60    # FR-01.6

    def close(self, t: dt.datetime) -> None:
        if self.flag is not SessionFlag.OPEN:
            raise RuleViolation("You have already checked out of this lesson.")
        if t <= self.check_in:
            raise RuleViolation("Check-out time must be after your check-in at %s." % self.check_in.strftime("%H:%M"))
        self.check_out = t
        self.flag = SessionFlag.LEFT_EARLY if t < self.planned_end else SessionFlag.CLOSED

    def auto_close(self, now: dt.datetime) -> bool:
        """Closes a forgotten session at the planned end and flags it for the Administrator."""
        if self.flag is SessionFlag.OPEN and now >= self.planned_end + dt.timedelta(minutes=self.AUTO_CLOSE_AFTER):
            self.check_out = self.planned_end
            self.flag = SessionFlag.MISSING_CHECKOUT
            return True
        return False

    def confirm(self, check_out: dt.datetime, note: str) -> None:
        if not note.strip():
            raise RuleViolation("Enter a reason for the correction (FR-03.4).")
        if check_out <= self.check_in:
            raise RuleViolation("Check-out must be after the check-in at %s." % self.check_in.strftime("%H:%M"))
        self.check_out, self.flag, self.note = check_out, SessionFlag.CONFIRMED, note.strip()

    def counts_for_hours(self) -> bool:
        """FR-03.3: sessions with a missing check-out are left out until confirmed."""
        return self.flag in (SessionFlag.CLOSED, SessionFlag.LEFT_EARLY, SessionFlag.CONFIRMED)

    def worked_minutes(self) -> int:
        return int((self.check_out - self.check_in).total_seconds() // 60) if self.counts_for_hours() else 0

    def overtime_minutes(self) -> int:
        if not self.counts_for_hours():
            return 0
        return max(0, int((self.check_out - self.planned_end).total_seconds() // 60))

    def early_minutes(self) -> int:
        if not self.counts_for_hours():
            return 0
        return max(0, int((self.planned_end - self.check_out).total_seconds() // 60))


@dataclass
class RegisterEntry:
    """Part of a LessonRegister (composition): saved and deleted with it."""
    student_id: int = 0
    status: Optional[AttendanceStatus] = None
    reason: str = ""
    scanned: bool = False
    scan_time: str = ""
    teacher_override: bool = False

    def affects_absence_run(self) -> bool:
        return self.status is not None and self.status is not AttendanceStatus.ABSENT_EXCUSED


@dataclass
class LessonRegister:
    id: int = 0
    lesson_id: int = 0
    teacher_id: int = 0
    entries: List[RegisterEntry] = field(default_factory=list)
    locked: bool = False
    submitted_at: Optional[dt.datetime] = None
    late_submission: bool = False

    def entry(self, student_id: int) -> RegisterEntry:
        for e in self.entries:
            if e.student_id == student_id:
                return e
        raise RuleViolation("This student is not in the group.")

    def set_status(self, student_id: int, status: AttendanceStatus, reason: str = "") -> RegisterEntry:
        if self.locked:
            raise RuleViolation("The register is locked. Use 'Request correction' instead (FR-02.7).")
        if status is AttendanceStatus.ABSENT_EXCUSED and not reason.strip():
            raise RuleViolation("An excused absence needs a reason, for example illness reported by a parent.")
        e = self.entry(student_id)
        e.status, e.reason = status, reason.strip()
        # FR-02.4: Present/Late without a scan is a teacher override the Administrator can see
        e.teacher_override = status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE) and not e.scanned
        return e

    def missing(self) -> List[int]:
        return [e.student_id for e in self.entries if e.status is None]

    def submit(self, at: dt.datetime, lesson_day: dt.date) -> None:
        if self.locked:
            raise RuleViolation("This register has already been submitted.")
        if self.missing():
            raise RuleViolation("%d student(s) still have no status. Give every student a status first." % len(self.missing()))
        self.submitted_at = at
        self.late_submission = at.date() > lesson_day          # FR-02.5
        self.lock()

    def lock(self) -> None:
        self.locked = True

    def apply_correction(self, student_id: int, status: AttendanceStatus) -> Optional[AttendanceStatus]:
        """Only called by an approved CorrectionRequest; returns the old value for the audit log."""
        e = self.entry(student_id)
        old, e.status = e.status, status
        return old


@dataclass
class CorrectionRequest:
    id: int = 0
    register_id: int = 0
    student_id: int = 0
    old_status: Optional[AttendanceStatus] = None
    new_status: AttendanceStatus = AttendanceStatus.PRESENT
    reason: str = ""
    requested_by: int = 0
    requested_at: Optional[dt.datetime] = None
    status: CorrectionStatus = CorrectionStatus.PENDING
    decided_by: Optional[int] = None
    decided_at: Optional[dt.datetime] = None

    def decide(self, approve: bool, head_specialist_id: int, at: dt.datetime) -> None:
        if self.status is not CorrectionStatus.PENDING:
            raise RuleViolation("This request has already been decided.")
        self.status = CorrectionStatus.APPROVED if approve else CorrectionStatus.REJECTED
        self.decided_by, self.decided_at = head_specialist_id, at


# --------------------------------------------------------------------------- retention (Figure 2.4)
@dataclass
class DropoutRisk:
    id: int = 0
    student_id: int = 0
    score: int = 0
    level: RiskLevel = RiskLevel.LOW
    factors: List[str] = field(default_factory=list)
    calculated_at: Optional[dt.datetime] = None

    HIGH_FROM: ClassVar[int] = 60
    MEDIUM_FROM: ClassVar[int] = 30

    @classmethod
    def from_factors(cls, student_id: int, weighted: List[Tuple[str, float]], at: dt.datetime) -> "DropoutRisk":
        score = int(round(min(100, sum(w for _, w in weighted))))
        level = RiskLevel.HIGH if score >= cls.HIGH_FROM else RiskLevel.MEDIUM if score >= cls.MEDIUM_FROM else RiskLevel.LOW
        return cls(student_id=student_id, score=score, level=level, factors=[f for f, _ in weighted], calculated_at=at)

    def is_high(self) -> bool:
        return self.level is RiskLevel.HIGH


@dataclass
class FollowUpAction:
    """Part of a RiskAlert (composition)."""
    description: str = ""
    assignee_id: int = 0
    due: Optional[dt.date] = None
    done: bool = False
    created_by: int = 0

    def is_overdue(self, today: dt.date) -> bool:
        return not self.done and self.due is not None and self.due < today


@dataclass
class RiskAlert:
    id: int = 0
    student_id: int = 0
    reasons: List[str] = field(default_factory=list)
    status: AlertStatus = AlertStatus.OPEN
    raised_at: Optional[dt.datetime] = None
    follow_ups: List[FollowUpAction] = field(default_factory=list)
    closed_note: str = ""

    def is_open(self) -> bool:
        return self.status in (AlertStatus.OPEN, AlertStatus.IN_PROGRESS)

    def add_reason(self, reason: str) -> None:
        """FR-05.2: a new reason joins the open alert instead of creating a duplicate."""
        if reason not in self.reasons:
            self.reasons.append(reason)

    def assign(self, action: FollowUpAction) -> None:
        if not self.is_open():
            raise RuleViolation("This alert is already closed.")
        if not action.description.strip():
            raise RuleViolation("Describe the follow-up action, for example 'Call parent'.")
        if action.due is None:
            raise RuleViolation("Choose a due date for the follow-up.")
        self.follow_ups.append(action)
        self.status = AlertStatus.IN_PROGRESS

    def close(self, status: AlertStatus, note: str = "") -> None:
        if status not in (AlertStatus.RESOLVED, AlertStatus.CLOSED_WITHDRAWN):
            raise RuleViolation("An alert can only be closed as Resolved or Closed - withdrawn.")
        if not self.is_open():
            raise RuleViolation("This alert is already closed.")
        self.status, self.closed_note = status, note
        for f in self.follow_ups:
            f.done = True


@dataclass
class StudentCircumstance:
    id: int = 0
    student_id: int = 0
    category: str = "Financial"
    note: str = ""
    recorded_on: Optional[dt.date] = None
    recorded_by: int = 0

    CATEGORIES: ClassVar[Tuple[str, ...]] = ("Financial", "Social", "Health", "Other")

    def is_financial_or_social(self) -> bool:
        return self.category in ("Financial", "Social")


@dataclass
class Withdrawal:
    id: int = 0
    student_id: int = 0
    date: Optional[dt.date] = None
    reason: WithdrawalReason = WithdrawalReason.OTHER
    note: str = ""
    recorded_by: int = 0

    def counts_in_dropout_rate(self, month: str) -> bool:
        return self.date is not None and month_of(self.date) == month


@dataclass
class TeacherFeedback:
    id: int = 0
    student_id: int = 0
    teacher_id: int = 0
    month: str = ""
    rating: int = 0
    comment: str = ""

    def __post_init__(self):
        if not 1 <= int(self.rating) <= 5:
            raise RuleViolation("Choose a rating from 1 to 5.")

    def is_negative(self) -> bool:
        return self.rating <= 2


# --------------------------------------------------------------------------- KPI (Figure 2.4)
INDICATORS: List[Tuple[str, str]] = [
    ("results", "Test & mock results"),
    ("attendance", "Student attendance"),
    ("punctuality", "Punctuality"),
    ("retention", "Retention"),
    ("feedback", "Student feedback"),
    ("rating", "Head Specialist rating"),
    ("achievements", "Achievements"),
]
INDICATOR_LABELS: Dict[str, str] = dict(INDICATORS)


@dataclass
class MonthlyRating:
    id: int = 0
    teacher_id: int = 0
    month: str = ""
    score: float = 0.0

    def __post_init__(self):
        if not self.is_valid():
            raise RuleViolation("A rating must be between 0 and 10 (FR-04.2).")

    def is_valid(self) -> bool:
        return 0 <= float(self.score) <= 10


@dataclass
class Achievement:
    id: int = 0
    teacher_id: int = 0
    title: str = ""
    kind: str = "Other"
    proof_file: str = ""
    points: int = 0
    submitted_on: Optional[dt.date] = None
    verified: bool = False
    verified_by: Optional[int] = None
    verified_on: Optional[dt.date] = None

    POINTS: ClassVar[Dict[str, int]] = {"Language certificate (e.g. IELTS)": 40, "Professional certificate": 30,
                                       "Student competition result": 50, "Other": 20}

    def verify(self, head_specialist_id: int, today: dt.date) -> None:
        if self.verified:
            raise RuleViolation("This achievement is already verified.")
        self.verified, self.verified_by, self.verified_on = True, head_specialist_id, today


@dataclass
class KpiWeights:
    id: int = 0
    effective_from: str = ""
    results: int = 25
    attendance: int = 10
    punctuality: int = 10
    retention: int = 20
    feedback: int = 20
    rating: int = 10
    achievements: int = 5
    set_by: Optional[int] = None

    def as_dict(self) -> Dict[str, int]:
        return {k: getattr(self, k) for k, _ in INDICATORS}

    def total(self) -> int:
        return sum(self.as_dict().values())

    def is_valid(self) -> bool:
        return self.total() == 100 and all(v >= 0 for v in self.as_dict().values())

    @classmethod
    def from_dict(cls, values: Dict[str, object], effective_from: str, set_by: int) -> "KpiWeights":
        try:
            clean = {k: int(values.get(k, 0)) for k, _ in INDICATORS}
        except (TypeError, ValueError):
            raise RuleViolation("Weights must be whole numbers.")
        w = cls(effective_from=effective_from, set_by=set_by, **clean)
        if any(v < 0 for v in clean.values()):
            raise RuleViolation("Weights cannot be negative.")
        if not w.is_valid():
            raise RuleViolation("Weights must add up to 100%% before they can be saved (now %d%%)." % w.total())
        return w


@dataclass
class KpiIndicator:
    """Part of a KpiSnapshot (composition)."""
    name: str = ""
    value: Optional[float] = None
    applicable: bool = True

    def counts(self) -> bool:
        return self.applicable and self.value is not None

    def weighted_value(self, weight: float) -> float:
        return (self.value or 0) * weight / 100.0


@dataclass
class KpiSnapshot:
    id: int = 0
    teacher_id: int = 0
    month: str = ""
    indicators: List[KpiIndicator] = field(default_factory=list)
    overall: float = 0.0
    label: KpiLabel = KpiLabel.PROVISIONAL
    locked: bool = False
    weights_used: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def compose(cls, teacher_id: int, month: str, indicators: List[KpiIndicator],
                weights: KpiWeights, provisional: bool) -> "KpiSnapshot":
        """FR-04.5: score each indicator 0-100, weight, sum; share the weight of indicators that do not count."""
        w = weights.as_dict()
        counted = [i for i in indicators if i.counts()]
        total_w = sum(w[i.name] for i in counted)
        overall = round(sum(i.weighted_value(w[i.name]) for i in counted) * 100.0 / total_w, 1) if total_w else 0.0
        partial = any(not i.applicable for i in indicators)
        label = KpiLabel.PROVISIONAL if provisional else KpiLabel.PARTIAL if partial else KpiLabel.FINAL
        return cls(teacher_id=teacher_id, month=month, indicators=indicators, overall=overall,
                   label=label, weights_used=w)

    def effective_weights(self) -> Dict[str, float]:
        counted = [i.name for i in self.indicators if i.counts()]
        tw = sum(self.weights_used.get(n, 0) for n in counted) or 1
        return {n: round(self.weights_used.get(n, 0) * 100.0 / tw, 1) for n in counted}

    def lock(self) -> None:
        if self.label is KpiLabel.PROVISIONAL:
            raise RuleViolation("A provisional KPI cannot be locked (FR-04.6).")
        self.locked = True


# --------------------------------------------------------------------------- other records
@dataclass
class MarketingSpend:
    id: int = 0
    subject: Subject = Subject.CHEMISTRY
    month: str = ""
    amount: float = 0.0
    entered_by: Optional[int] = None


@dataclass
class Notification:
    id: int = 0
    user_id: int = 0
    message: str = ""
    created_at: Optional[dt.datetime] = None
    read: bool = False
    page: str = ""
