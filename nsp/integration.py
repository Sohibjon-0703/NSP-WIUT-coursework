"""«package» integration - EduTizimGateway and its two realisations (report Figure 2.1, NFR-05.3).

Whether EduTizim offers an API depends on the vendor. Services only talk to the
EduTizimGateway interface, so ApiGateway can replace ExportImportGateway later
without any change to the services. The prototype uses ExportImportGateway,
which reads a daily EduTizim export file (data/edutizim_export.json).
"""
import abc
import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain import LessonRegister, month_of, shift_month


class EduTizimGateway(abc.ABC):
    """What the module needs from EduTizim (NFR-05.1: students, groups, timetable, balances are never re-typed)."""

    @abc.abstractmethod
    def fetch_export(self) -> Dict[str, Any]: ...

    @abc.abstractmethod
    def monthly_aggregates(self, teacher_id: int, month: str) -> Dict[str, Optional[float]]:
        """Average test & mock result and student attendance % for a teacher's groups (EduTizim owns both)."""

    @abc.abstractmethod
    def push_register(self, register: LessonRegister) -> None:
        """NFR-05.2: submitted registers go back so EduTizim's own attendance dashboards stay correct."""

    @abc.abstractmethod
    def last_sync(self) -> Optional[dt.datetime]: ...


class ApiGateway(EduTizimGateway):
    """Used if the vendor confirms an API in the Sprint 0 spike (report Section 3.3). Not available yet."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url, self.api_key = base_url, api_key

    def _unavailable(self):
        raise NotImplementedError("EduTizim API not confirmed by the vendor - use ExportImportGateway (NFR-05.3).")

    def fetch_export(self):
        self._unavailable()

    def monthly_aggregates(self, teacher_id, month):
        self._unavailable()

    def push_register(self, register):
        self._unavailable()

    def last_sync(self):
        return None


class ExportImportGateway(EduTizimGateway):
    """Reads the daily EduTizim export and writes submitted registers to an outbox file for re-import."""

    def __init__(self, export_path: Path, outbox_path: Path, today: dt.date):
        self.export_path, self.outbox_path, self.today = export_path, outbox_path, today
        self._data: Optional[Dict[str, Any]] = None
        self._synced: Optional[dt.datetime] = None

    def fetch_export(self) -> Dict[str, Any]:
        if self._data is None:
            self._data = json.loads(self.export_path.read_text(encoding="utf-8"))
            self._synced = dt.datetime.combine(self.today, dt.time(7, 0))
        return self._data

    def monthly_aggregates(self, teacher_id: int, month: str) -> Dict[str, Optional[float]]:
        offset = self._offset(month)
        row = self.fetch_export()["monthly_aggregates"].get(str(teacher_id), {}).get(str(offset))
        return row or {"results": None, "attendance": None}

    def marketing_history(self) -> Dict[str, List[float]]:
        return self.fetch_export()["marketing_spend"]["months_back_to_current"]

    def push_register(self, register: LessonRegister) -> None:
        self.outbox_path.parent.mkdir(exist_ok=True)
        line = {"lesson_id": register.lesson_id, "submitted_at": register.submitted_at.isoformat(),
                "entries": [{"student_id": e.student_id, "status": e.status.value} for e in register.entries]}
        with self.outbox_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def last_sync(self) -> Optional[dt.datetime]:
        self.fetch_export()
        return self._synced

    # relative dates in the export -> real dates
    def _offset(self, month: str) -> int:
        cur = month_of(self.today)
        y1, m1 = int(cur[:4]), int(cur[5:])
        y2, m2 = int(month[:4]), int(month[5:])
        return (y2 * 12 + m2) - (y1 * 12 + m1)

    def resolve_date(self, spec: Dict[str, int]) -> dt.date:
        if "days_ago" in spec:
            return self.today - dt.timedelta(days=spec["days_ago"])
        m = shift_month(month_of(self.today), spec["month_offset"])
        day = spec.get("day", 1)
        if spec["month_offset"] == 0:
            day = min(day, max(1, self.today.day - 1))
        return dt.date(int(m[:4]), int(m[5:]), min(day, 28))
