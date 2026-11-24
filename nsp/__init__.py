"""NSP Learning Centre - Teacher Workload, KPI and Retention module (high-fidelity prototype).

Packages follow the architecture diagram (report Figure 2.1):
  presentation -> static/        controllers -> nsp/controllers.py
  services     -> nsp/services.py   domain   -> nsp/domain.py
  persistence  -> nsp/persistence.py   integration -> nsp/integration.py
"""
import threading
from pathlib import Path

from . import domain as d
from .controllers import Router
from .integration import ExportImportGateway
from .persistence import AuditLog, Repository
from .services import Clock, Services

ROOT = Path(__file__).resolve().parent.parent


class App:
    """Wires the packages together (the only place that knows the concrete classes)."""

    def __init__(self, db_path: Path = ROOT / "nsp_data.db"):
        self.lock = threading.RLock()
        self.outbox = ROOT / "data" / "edutizim_outbox.jsonl" if db_path == ROOT / "nsp_data.db" else db_path.with_suffix(".outbox.jsonl")
        self.clock = Clock()
        self.repo = Repository(str(db_path))
        fresh = not self.repo.schema_ok()
        if fresh:
            self.repo.drop_all()
        self._register_tables()
        self.audit = AuditLog(self.repo)
        self.gateway = ExportImportGateway(ROOT / "data" / "edutizim_export.json",
                                           self.outbox, self.clock.today())
        self.services = Services(self.repo, self.gateway, self.clock, self.audit)
        self.router = Router(self)
        # demo data is tied to "today", so it is rebuilt on a new day or a new schema
        if fresh or self.repo.get_meta("seeded_on") != self.clock.today().isoformat():
            self.reset()
        else:
            self.services.attendance.nightly_close()

    def _register_tables(self) -> None:
        r = self.repo
        r.register(d.User, d.Student, d.MainTeacher, d.SupportTeacher, d.Administrator, d.HeadSpecialist, d.Director)
        for cls in (d.Room, d.Group, d.Lesson, d.AttendanceRecord, d.WorkSession, d.LessonRegister, d.CorrectionRequest,
                    d.DropoutRisk, d.RiskAlert, d.StudentCircumstance, d.Withdrawal, d.TeacherFeedback, d.MonthlyRating,
                    d.Achievement, d.KpiWeights, d.KpiSnapshot, d.MarketingSpend, d.Notification):
            r.register(cls)

    def reset(self) -> None:
        from .seed import seed
        with self.lock:
            self.repo.drop_all()
            self._register_tables()
            self.audit = AuditLog(self.repo)
            self.services.audit = self.audit
            for svc in vars(self.services).values():
                if hasattr(svc, "audit"):
                    svc.audit = self.audit
            if self.outbox.exists():
                self.outbox.unlink()
            seed(self)
