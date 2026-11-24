"""Demo data. Imports the EduTizim export through the gateway, then builds two months of
module history (lessons, QR check-ins, work sessions, feedback, ratings) so every screen
has realistic data. Alerts and the closed month's KPI snapshots are produced by the real
services, not typed in.
"""
import datetime as dt
import random
import secrets

from .domain import (
    Achievement, AttendanceMethod, AttendanceRecord, AttendanceStatus, CorrectionRequest, Director, Group,
    HeadSpecialist, KpiIndicator, KpiSnapshot, KpiWeights, Lesson, LessonRegister, MainTeacher, MarketingSpend,
    MonthlyRating, RegisterEntry, Room, SessionFlag, Student, StudentCircumstance, StudentStatus, Subject,
    SupportTeacher, TeacherFeedback, Administrator, Withdrawal, WithdrawalReason, WorkSession, month_of,
    month_start, shift_month,
)
from .persistence import AuditEntry

DEMO_GROUP, DEMO_TEACHER = 1, 1
LATE_P = {1: .03, 2: .22, 3: 0, 4: .07, 5: .02, 6: .15}
OVERTIME = {1: 13, 2: 2, 3: 9, 4: 4, 5: 6, 6: 1}
FEEDBACK_MEAN = {1: 4.3, 2: 3.6, 3: 4.7, 4: 4.1, 5: 4.2, 6: 3.7}
RATING = {1: 8, 2: 7, 3: 9, 4: 8, 5: 8, 6: 6}
HISTORY = {1: (96, 95, 4.2, 8), 2: (80, 88, 3.6, 7), 3: (100, 97, 4.7, 9), 4: (92, 93, 4.1, 8), 5: (98, 95, 4.2, 8), 6: (85, 89, 3.7, 6)}
COMMENTS = ["Explains difficult topics clearly", "Homework feedback is very useful", "Lessons are well organised",
            "More practice tests please", "Very patient with questions", "Sometimes too fast"]


def seed(app) -> None:
    repo, svc, gw = app.repo, app.services, app.gateway
    rnd = random.Random(42)
    now = app.clock.now()
    today = now.date()
    cur = month_of(today)
    data = gw.fetch_export()
    at = lambda d, hm: dt.datetime.combine(d, dt.time.fromisoformat(hm))

    # ---- import from EduTizim (NFR-05.1)
    for r in data["rooms"]:
        repo.save(Room(id=r["id"], name=r["name"], qr_secret=secrets.token_hex(16)))
    for t in data["teachers"]:
        cls = MainTeacher if t["type"] == "Main" else SupportTeacher
        repo.save(cls(id=t["id"], full_name=t["name"], phone=t["phone"], subject=Subject(t["subject"])))
    roles = {"Administrator": Administrator, "HeadSpecialist": HeadSpecialist, "Director": Director}
    for s in data["staff"]:
        repo.save(roles[s["role"]](id=s["id"], full_name=s["name"]))
    groups = {}
    for g in data["groups"]:
        groups[g["id"]] = g
        repo.save(Group(id=g["id"], name=g["name"], subject=Subject(g["subject"]), capacity=g["capacity"],
                        teacher_id=g["teacher_id"], support_teacher_id=g["support_teacher_id"], room_id=g["room_id"]))
    students = []
    for s in data["students"]:
        st = Student(id=s["id"], full_name=s["name"], level=s["level"], group_id=s["group_id"],
                     enrolled_on=gw.resolve_date(s["enrolled"]), consecutive_absences=s["consecutive_absences"],
                     tuition_debt=s["debt"])
        if "withdrawn" in s:
            w = s["withdrawn"]
            st.status = StudentStatus.WITHDRAWN
            repo.save(st)
            repo.save(Withdrawal(student_id=st.id, date=gw.resolve_date(w), reason=WithdrawalReason(w["reason"]),
                                 note=w["note"], recorded_by=7))
        else:
            repo.save(st)
        students.append((st, s))

    repo.save(KpiWeights(effective_from=shift_month(cur, -8), set_by=8))
    for subj, values in gw.marketing_history().items():
        for i, v in enumerate(values):
            repo.save(MarketingSpend(subject=Subject(subj), month=shift_month(cur, i - len(values) + 1), amount=v, entered_by=7))

    # ---- timetable: last month and this month, plus today's demo lesson
    first = month_start(shift_month(cur, -1))
    day = first
    while day <= today:
        for g in groups.values():
            if day.weekday() in g["days"] or (day == today and g["id"] == DEMO_GROUP):
                if day == today and g["teacher_id"] == DEMO_TEACHER and g["id"] != DEMO_GROUP:
                    continue        # keep the demo teacher's day to one group lesson
                repo.save(Lesson(group_id=g["id"], room_id=g["room_id"], teacher_id=g["teacher_id"],
                                 start=at(day, g["start"]), end=at(day, g["end"])))
            sup = g.get("support")
            if sup and day.weekday() in sup["days"]:
                repo.save(Lesson(group_id=g["id"], room_id=sup["room_id"], teacher_id=g["support_teacher_id"],
                                 start=at(day, sup["start"]), end=at(day, sup["end"]), is_support_session=True))
        day += dt.timedelta(days=1)

    # ---- teacher check-ins and work sessions for lessons already delivered
    lessons = repo.find(Lesson, order="start")
    demo_lesson = next(l for l in lessons if l.group_id == DEMO_GROUP and l.start.date() == today)
    missing_plan = {2: 1, 6: 2}
    recent = {t: [l for l in lessons if l.teacher_id == t and month_of(l.start) == cur and l.start.date() < today][-6:]
              for t in missing_plan}
    missing_ids = {l.id for t, n in missing_plan.items() for l in recent[t][-n - 1:-1] if l}
    for l in lessons:
        if l.id == demo_lesson.id or l.end > now:
            continue
        t = l.teacher_id
        late = rnd.random() < LATE_P[t]
        ci = l.start + dt.timedelta(minutes=rnd.randint(11, 24) if late else -rnd.randint(3, 14))
        manual = rnd.random() < .04
        repo.save(AttendanceRecord(user_id=t, lesson_id=l.id, timestamp=ci,
                                   method=AttendanceMethod.MANUAL if manual else AttendanceMethod.QR,
                                   minutes_late=l.minutes_late(ci), reason="Phone battery dead" if manual else "",
                                   confirmed_by=7 if manual else None))
        ws = WorkSession(teacher_id=t, lesson_id=l.id, check_in=ci, planned_start=l.start, planned_end=l.end)
        if l.id in missing_ids:
            ws.auto_close(l.end + dt.timedelta(hours=2))
        else:
            if rnd.random() < .03:
                ws.close(l.end - dt.timedelta(minutes=rnd.randint(10, 20)))
            else:
                ws.close(l.end + dt.timedelta(minutes=max(0, int(rnd.gauss(OVERTIME[t], 5)))))
        repo.save(ws)

    # ---- today's demo lesson: two students already scanned in, Timur and the teacher have not
    for name, hm in (("Madina Qodirova", "14:55"), ("Shahzod Matyaqubov", "14:57")):
        st = next(s for s, _ in students if s.full_name == name)
        repo.save(AttendanceRecord(user_id=st.id, lesson_id=demo_lesson.id, timestamp=at(today, hm),
                                   method=AttendanceMethod.QR, minutes_late=0))

    # ---- feedback (last month complete, this month ~70%)
    for st, raw in students:
        g = groups[st.group_id]
        for month, share in ((shift_month(cur, -1), 1.0), (cur, .7)):
            if not st.enrolled_on or st.enrolled_on >= month_start(month) or not st.is_active():
                continue
            if month == cur and (st.full_name in ("Timur Olimov", "Kamola Ergasheva") or rnd.random() > share):
                continue
            for tid in (g["teacher_id"], g["support_teacher_id"]):
                if tid:
                    r = max(3, min(5, round(rnd.gauss(FEEDBACK_MEAN[tid], .7))))
                    repo.save(TeacherFeedback(student_id=st.id, teacher_id=tid, month=month, rating=r,
                                              comment=rnd.choice(COMMENTS) if rnd.random() < .3 else ""))
    for t, r in RATING.items():
        repo.save(MonthlyRating(teacher_id=t, month=shift_month(cur, -1), score=r))

    # ---- achievements (FR-04.3)
    for t, title, kind, days, verified in [
            (3, "IELTS 8.5 (Academic)", "Language certificate (e.g. IELTS)", 100, True),
            (3, "Student won regional English olympiad", "Student competition result", 40, True),
            (1, "Coached two regional Chemistry olympiad winners", "Student competition result", 60, True),
            (4, "CELTA certificate", "Professional certificate", 150, True),
            (4, "IELTS 8.0 (Academic)", "Language certificate (e.g. IELTS)", 3, False),
            (2, "Biology teaching methods certificate", "Professional certificate", 200, True),
            (5, "Laboratory safety certificate", "Professional certificate", 90, True)]:
        d = today - dt.timedelta(days=days)
        repo.save(Achievement(teacher_id=t, title=title, kind=kind, proof_file=title.split(" (")[0].lower().replace(" ", "_") + ".pdf",
                              points=Achievement.POINTS[kind], submitted_on=d - dt.timedelta(days=2), verified=verified,
                              verified_by=8 if verified else None, verified_on=d if verified else None))

    # ---- KPI history: months before go-live back-filled from Excel and EduTizim
    weights = svc.kpi.weights_for(cur)
    for off in range(-6, -1):
        m = shift_month(cur, off)
        for t in svc.repo.find(MainTeacher) + svc.repo.find(SupportTeacher):
            punct, ret, fb, rating = HISTORY[t.id]
            agg = gw.monthly_aggregates(t.id, m)
            j = lambda v, s: round(v + rnd.uniform(-s, s), 1)
            inds = [KpiIndicator("results", agg["results"], t.records_tests() and agg["results"] is not None),
                    KpiIndicator("attendance", agg["attendance"]), KpiIndicator("punctuality", min(100, j(punct, 3))),
                    KpiIndicator("retention", min(100, j(ret, 3))), KpiIndicator("feedback", min(100, j(fb * 20, 3))),
                    KpiIndicator("rating", min(100, j(rating * 10, 5))),
                    KpiIndicator("achievements", float(min(100, svc.kpi.achievements_score(t, m))))]
            snap = KpiSnapshot.compose(t.id, m, inds, weights, provisional=False)
            snap.lock()
            repo.save(snap)
    svc.kpi.close_month(shift_month(cur, -1))        # last month: real month-end job on the data above

    # ---- a locked register from the last Chem DTM-B lesson, with a correction waiting for approval
    last_b = [l for l in lessons if l.group_id == 2 and not l.is_support_session and l.start.date() < today][-1]
    entries = []
    for st, _ in students:
        if st.group_id == 2 and st.is_active() and st.enrolled_on <= last_b.start.date():
            absent = st.full_name == "Ulugbek Sobirov"
            entries.append(RegisterEntry(student_id=st.id, scanned=not absent, scan_time="" if absent else "09:5%d" % rnd.randint(0, 9),
                                         status=AttendanceStatus.ABSENT if absent else AttendanceStatus.PRESENT))
    reg = repo.save(LessonRegister(lesson_id=last_b.id, teacher_id=DEMO_TEACHER, entries=entries, locked=True,
                                   submitted_at=last_b.end + dt.timedelta(minutes=5)))
    ulug = next(s for s, _ in students if s.full_name == "Ulugbek Sobirov")
    repo.save(CorrectionRequest(register_id=reg.id, student_id=ulug.id, old_status=AttendanceStatus.ABSENT,
                                new_status=AttendanceStatus.PRESENT, requested_by=DEMO_TEACHER,
                                reason="He was in the room; his phone battery was dead so he could not scan.",
                                requested_at=last_b.end + dt.timedelta(hours=3)))

    # ---- alerts come from the real rules
    for st, raw in students:
        if raw.get("circumstance"):
            repo.save(StudentCircumstance(student_id=st.id, category="Financial", note=raw["circumstance"],
                                          recorded_on=today - dt.timedelta(days=6), recorded_by=7))
        if st.is_active() and st.consecutive_absences >= 3:
            svc.risk.evaluate_alerts(st, "%d consecutive absences" % st.consecutive_absences)
            if raw.get("circumstance"):
                svc.risk.evaluate_alerts(st, "Financial circumstance recorded: " + raw["circumstance"])
    kamola = next(s for s, _ in students if s.full_name == "Kamola Ergasheva")
    svc.feedback.submit(kamola, groups[kamola.group_id]["teacher_id"], 2, "Lessons are too fast for me")
    hs = repo.first(HeadSpecialist)
    svc.risk.assign_follow_up(repo.first(Director), kamola.id, "Meet Kamola and her parent about lesson pace", hs.id,
                              today.isoformat())
    alert = svc.risk.open_alert(kamola.id)
    alert.follow_ups[-1].due = today - dt.timedelta(days=2)          # shows the overdue highlight (FR-05.4)
    repo.save(alert)
    svc.risk.daily_refresh()

    # ---- start the demo with a clean audit log and read notifications
    repo.con.execute('DELETE FROM "audit_entry"')
    app.audit.record(None, "Initial import", "EduTizim export", "", "%d students, %d teachers, %d lessons"
                     % (len(students), len(data["teachers"]), len(lessons)))
    repo.con.execute('UPDATE "notification" SET "read"=1')
    repo.set_meta("seeded_on", today.isoformat())
