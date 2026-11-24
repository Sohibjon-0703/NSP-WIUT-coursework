"""«package» controllers - one controller per feature area (report Figure 2.1).

A controller checks the caller's role, validates the input and calls a service.
Screens (static/) never call services or entities directly.
"""
import datetime as dt
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .domain import Group, RuleViolation, Student, Teacher, User
from .persistence import _plain

Route = Tuple[str, str, Optional[str], Callable[..., Any]]


def _at(clock, value: str) -> dt.datetime:
    try:
        return dt.datetime.combine(clock.today(), dt.time.fromisoformat(value or ""))
    except ValueError:
        raise RuleViolation("Enter a time, for example 14:55.")


class Controller:
    def __init__(self, app):
        self.app, self.s, self.repo = app, app.services, app.repo

    def routes(self) -> List[Route]:
        raise NotImplementedError


class SessionController(Controller):
    def routes(self):
        return [("GET", r"/api/users", "public", self.users),
                ("GET", r"/api/me", None, self.me),
                ("GET", r"/api/notifications", None, self.inbox),
                ("POST", r"/api/notifications/read", None, self.read),
                ("GET", r"/api/audit", "audit.view", self.audit),
                ("POST", r"/api/demo/reset", "public", self.reset)]

    def describe(self, u: User) -> Dict[str, Any]:
        d = {"id": u.id, "name": u.full_name, "role": u.ROLE}
        if isinstance(u, Teacher):
            d.update(kind=u.KIND, subject=u.subject.value, detail="%s teacher, %s" % (u.KIND, u.subject.value))
        elif isinstance(u, Student):
            g = self.repo.get(Group, u.group_id)
            d.update(detail="Student, " + (g.name if g else "group %d" % u.group_id))
        else:
            d.update(detail=u.ROLE)
        return d

    def users(self, user, body, q):
        featured = [1, 5, 7, 8, 9]
        timur = self.repo.first(Student, full_name="Timur Olimov")
        out = [self.describe(self.repo.get(User, i)) for i in featured]
        out.insert(1, self.describe(timur))
        others = [self.describe(u) for u in self.repo.find(Teacher, order="id") if u.id not in featured]
        return {"featured": out, "others": others}

    def me(self, user, body, q):
        d = self.describe(user)
        d["permissions"] = sorted(user.PERMISSIONS)
        d["unread"] = sum(1 for n in self.s.notifier.inbox(user) if not n.read)
        return d

    def inbox(self, user, body, q):
        return [{"message": n.message, "at": n.created_at.strftime("%d %b %H:%M"), "read": n.read, "page": n.page}
                for n in self.s.notifier.inbox(user)]

    def read(self, user, body, q):
        self.s.notifier.mark_read(user)
        return {}

    def audit(self, user, body, q):
        return [{"at": e.at.strftime("%d %b %H:%M:%S"), "user": e.user_name, "action": e.action, "entity": e.entity,
                 "old": e.old_value, "new": e.new_value} for e in self.app.audit.recent()]

    def reset(self, user, body, q):
        self.app.reset()
        return {}


class AttendanceController(Controller):
    def routes(self):
        return [("GET", r"/api/rooms/(\d+)/token", "public", self.token),
                ("GET", r"/api/rooms/(\d+)/old-token", "public", self.old_token),
                ("GET", r"/api/attendance/today", "attendance.self", self.today),
                ("POST", r"/api/attendance/check-in", "attendance.self", self.check_in),
                ("POST", r"/api/attendance/check-out", "attendance.self", self.check_out),
                ("GET", r"/api/attendance/desk", "attendance.manual", self.desk),
                ("POST", r"/api/attendance/manual", "attendance.manual", self.manual),
                ("POST", r"/api/attendance/sessions/(\d+)/confirm", "session.confirm", self.confirm),
                ("GET", r"/api/workload", "workload.view", self.workload)]

    def token(self, user, body, q, room_id):
        return self.s.qr.token_for(int(room_id))

    def old_token(self, user, body, q, room_id):
        return {"value": self.s.qr.old_token(int(room_id))}

    def today(self, user, body, q):
        return self.s.attendance.my_day(user)

    def check_in(self, user, body, q):
        return self.s.attendance.check_in(user, body.get("token", ""), _at(self.s.clock, body.get("time")))

    def check_out(self, user, body, q):
        return self.s.attendance.check_out(user, body.get("token", ""), _at(self.s.clock, body.get("time")))

    def desk(self, user, body, q):
        return self.s.attendance.desk(user)

    def manual(self, user, body, q):
        return self.s.attendance.confirm_manually(user, int(body.get("user_id") or 0), _at(self.s.clock, body.get("time")),
                                                  body.get("reason", ""))

    def confirm(self, user, body, q, session_id):
        self.s.attendance.confirm_session(user, int(session_id), body.get("check_out", ""), body.get("reason", ""))
        return {}

    def workload(self, user, body, q):
        return dict(self.s.workload.report(user, q.get("month") or self.s.clock.month()), months=self.s.kpi.months())


class RegisterController(Controller):
    def routes(self):
        return [("GET", r"/api/register", "register.keep", self.get),
                ("POST", r"/api/register/(\d+)/status", "register.keep", self.status),
                ("POST", r"/api/register/(\d+)/submit", "register.keep", self.submit),
                ("POST", r"/api/register/(\d+)/correction", "register.keep", self.correction),
                ("GET", r"/api/corrections", "correction.decide", self.pending),
                ("POST", r"/api/corrections/(\d+)", "correction.decide", self.decide)]

    def get(self, user, body, q):
        return self.s.registers.todays_register(user, int(q["lesson_id"]) if q.get("lesson_id") else None)

    def status(self, user, body, q, rid):
        self.s.registers.set_status(user, int(rid), int(body["student_id"]), body.get("status", ""), body.get("reason", ""))
        return {}

    def submit(self, user, body, q, rid):
        return self.s.registers.submit(user, int(rid))

    def correction(self, user, body, q, rid):
        self.s.registers.request_correction(user, int(rid), int(body["student_id"]), body.get("status", ""), body.get("reason", ""))
        return {}

    def pending(self, user, body, q):
        return {"corrections": self.s.registers.pending_corrections(user),
                "achievements": self.s.kpi.pending_achievements(user)}

    def decide(self, user, body, q, cid):
        self.s.registers.decide(user, int(cid), bool(body.get("approve")))
        return {}


class RetentionController(Controller):
    def routes(self):
        return [("GET", r"/api/risk", "risk.view", self.dashboard),
                ("GET", r"/api/risk/export", "risk.view", self.export),
                ("GET", r"/api/risk/(\d+)", "risk.view", self.detail),
                ("POST", r"/api/risk/(\d+)/follow-up", "risk.followup", self.follow_up),
                ("POST", r"/api/risk/(\d+)/close", "risk.followup", self.close),
                ("POST", r"/api/risk/(\d+)/circumstance", "student.record", self.circumstance),
                ("GET", r"/api/retention", "retention.view", self.retention),
                ("GET", r"/api/marketing", "marketing.edit", self.marketing),
                ("POST", r"/api/marketing", "marketing.edit", self.set_marketing),
                ("GET", r"/api/feedback", "feedback.give", self.my_teachers),
                ("POST", r"/api/feedback", "feedback.give", self.feedback)]

    def dashboard(self, user, body, q):
        return self.s.risk.dashboard(user, q.get("subject", ""), q.get("level", ""))

    def export(self, user, body, q):
        return ("text/csv", self.s.risk.export_csv(user), "dropout_risk.csv")

    def detail(self, user, body, q, sid):
        return self.s.risk.detail(user, int(sid))

    def follow_up(self, user, body, q, sid):
        self.s.risk.assign_follow_up(user, int(sid), body.get("description", ""), body.get("assignee_id"), body.get("due", ""))
        return {}

    def close(self, user, body, q, sid):
        self.s.risk.close_alert(user, int(sid), body.get("outcome", ""), body.get("reason", ""), body.get("note", ""))
        return {}

    def circumstance(self, user, body, q, sid):
        self.s.risk.record_circumstance(user, int(sid), body.get("category", ""), body.get("note", ""))
        return {}

    def retention(self, user, body, q):
        return self.s.retention.dashboard(user, q.get("month") or self.s.clock.month())

    def marketing(self, user, body, q):
        return self.s.retention.marketing(user)

    def set_marketing(self, user, body, q):
        self.s.retention.set_spend(user, body.get("subject", ""), body.get("amount"))
        return {}

    def my_teachers(self, user, body, q):
        return self.s.feedback.my_teachers(user)

    def feedback(self, user, body, q):
        return self.s.feedback.submit(user, int(body.get("teacher_id") or 0), body.get("rating"), body.get("comment", ""))


class KpiController(Controller):
    def routes(self):
        return [("GET", r"/api/kpi", "kpi.view_all", self.table),
                ("GET", r"/api/kpi/export", "kpi.view_all", self.export),
                ("GET", r"/api/kpi/teacher/(\d+)", "kpi.view_all", self.detail),
                ("GET", r"/api/kpi/me", "kpi.own", self.mine),
                ("GET", r"/api/kpi/weights", "kpi.weights", self.weights),
                ("POST", r"/api/kpi/weights", "kpi.weights", self.save_weights),
                ("POST", r"/api/kpi/ratings", "kpi.rate", self.rate),
                ("POST", r"/api/achievements", "achievement.add", self.add_achievement),
                ("POST", r"/api/achievements/(\d+)/verify", "achievement.verify", self.verify)]

    def _month(self, q):
        return q.get("month") or self.s.clock.month()

    def table(self, user, body, q):
        return self.s.kpi.table(user, self._month(q), q.get("subject", ""), q.get("kind", ""))

    def export(self, user, body, q):
        m = self._month(q)
        return ("text/csv", self.s.kpi.export_csv(user, m), "teacher_kpi_%s.csv" % m)

    def detail(self, user, body, q, tid):
        return self.s.kpi.detail(user, int(tid), self._month(q))

    def mine(self, user, body, q):
        return self.s.kpi.detail(user, user.id, self._month(q))

    def weights(self, user, body, q):
        return self.s.kpi.weights_view(user)

    def save_weights(self, user, body, q):
        self.s.kpi.save_weights(user, body.get("weights") or {})
        return {}

    def rate(self, user, body, q):
        self.s.kpi.rate(user, int(body.get("teacher_id") or 0), body.get("score"))
        return {}

    def add_achievement(self, user, body, q):
        self.s.kpi.add_achievement(user, body.get("title", ""), body.get("kind", ""), body.get("proof", ""))
        return {}

    def verify(self, user, body, q, aid):
        self.s.kpi.verify_achievement(user, int(aid))
        return {}


class Router:
    def __init__(self, app):
        self.app = app
        self.table: List[Tuple[str, Any, Optional[str], Callable]] = []
        for c in (SessionController, AttendanceController, RegisterController, RetentionController, KpiController):
            for method, pattern, perm, fn in c(app).routes():
                self.table.append((method, re.compile("^" + pattern + "$"), perm, fn))

    def dispatch(self, method: str, path: str, user_id: Optional[str], body: Dict, query: Dict) -> Any:
        for m, rx, perm, fn in self.table:
            match = rx.match(path)
            if m == method and match:
                user = self.app.repo.get(User, int(user_id)) if (user_id or "").isdigit() else None
                if perm != "public":
                    if user is None:
                        raise PermissionError("Sign in first.")
                    if perm and not user.can(perm):
                        raise PermissionError("Your role (%s) is not allowed to do this (NFR-01.1)." % user.ROLE)
                return fn(user, body, query, *match.groups())
        raise LookupError("No such endpoint")


def make_handler(app, static_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, payload: Any):
            self._send(code, json.dumps(_plain(payload)).encode(), "application/json")

        def _handle(self, method: str):
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                return self._static(url.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                with app.lock:
                    result = app.router.dispatch(method, url.path, self.headers.get("X-User-Id"), body, query)
                if isinstance(result, tuple):
                    ctype, text, filename = result
                    return self._send(200, text.encode("utf-8-sig"), ctype + "; charset=utf-8",
                                      {"Content-Disposition": 'attachment; filename="%s"' % filename})
                self._json(200, result)
            except RuleViolation as e:
                self._json(422, {"error": str(e)})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except LookupError as e:
                self._json(404, {"error": str(e)})
            except (ValueError, KeyError, json.JSONDecodeError) as e:
                self._json(400, {"error": "The request was not valid (%s)." % e})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "Unexpected error - see the console."})

        def _static(self, path: str):
            rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/").replace("static/", "", 1)
            f = (static_dir / rel).resolve()
            if static_dir.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"Not found", "text/plain")
            types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
            self._send(200, f.read_bytes(), types.get(f.suffix, "application/octet-stream") + "; charset=utf-8")

        def log_message(self, *args):
            pass

    return Handler
