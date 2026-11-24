# NSP Learning Centre – Teacher Workload, KPI and Retention module

High-fidelity prototype for Systems Analysis and Design (6BUIS026C), Coursework 2.

## Run

1. Install Python 3.8 or newer. No other packages are needed.
2. In this folder, run `python run.py`.
3. The browser opens at http://localhost:8000. Stop the server with Ctrl+C.

Data is stored in `nsp_data.db` (SQLite). The demo data is rebuilt each day, because the timetable is relative to today. **Reset demo data** (sidebar or sign-in screen) restores the starting state at any time.

Run the tests with `python -m unittest -v`.

## Demo walkthrough (about 1 minute)

| # | Sign in as | What to show | Requirement |
|---|---|---|---|
| 1 | Timur Olimov (Student) | *Test: scan an old photo* → rejected. *Scan room code* at 14:58 → Present | FR-01.1, UC-01 |
| 2 | Dilnoza Karimova (Teacher) | Check in at 15:14 → Late. Check out at 16:44 → hours and overtime | FR-01.3, FR-01.5 |
| 3 | Dilnoza → Lesson register | Timur is pre-filled. Mark Aziza *Absent*, then *Submit* → dropout alert | FR-02, FR-05.2 |
| 4 | Otabek Maksudov (Director) | Dropout risk: open Aziza → assign follow-up. Teacher KPI → breakdown and trend | UC-03, UC-04 |
| 5 | Rustam Saidov (Head Specialist) | Weights at 110% are refused. Approve the correction. Verify the achievement | FR-04.1, FR-02.7 |
| 6 | Director → Audit log | Every change above: who, when, old value → new value | NFR-01.4 |

## Structure (follows the architecture diagram, report Figure 2.1)

| Package | File | Contents |
|---|---|---|
| presentation | `static/index.html`, `static/app.js`, `static/style.css` | Screens for the five roles. They call only the JSON API and hold no business rules. |
| controllers | `nsp/controllers.py` | `AttendanceController`, `RegisterController`, `RetentionController`, `KpiController`, `SessionController`. Each one checks the role, validates input and calls a service. |
| services | `nsp/services.py` | `QrTokenService`, `AttendanceService`, `WorkloadService`, `RegisterService`, `RiskService`, `FeedbackService`, `KpiService`, `RetentionService`, plus `NotificationService`, which realises `Notifier`. |
| domain | `nsp/domain.py` | The entities from class diagrams 2.2–2.4 and the rules each one owns. |
| persistence | `nsp/persistence.py` | `Repository`: one SQLite table per class, with columns taken from the class fields. `AuditLog`: who, what, when, old and new value. |
| integration | `nsp/integration.py` | The `EduTizimGateway` interface, realised by `ExportImportGateway` (reads `data/edutizim_export.json`) and `ApiGateway` (a stub until the vendor confirms an API, NFR-05.3). |
| — | `nsp/seed.py`, `run.py`, `tests/` | Demo data, the entry point, and the unit tests. |

### Where the UML relationships appear in the code

| UML | Code |
|---|---|
| Generalisation | `User` (abstract) → `Student`, `Teacher` (abstract) → `MainTeacher` / `SupportTeacher`, `Administrator`, `HeadSpecialist`, `Director` |
| Polymorphism | `Teacher.records_tests()` decides whether the results KPI indicator applies. `User.can()` uses each subclass's `PERMISSIONS`. |
| Composition | `LessonRegister` ◆ `RegisterEntry`, `RiskAlert` ◆ `FollowUpAction`, `KpiSnapshot` ◆ `KpiIndicator`. Parts are stored inside the owner's row, so they are saved and deleted with it. |
| Realisation | `NotificationService` ▷ `Notifier`; `ExportImportGateway` / `ApiGateway` ▷ `EduTizimGateway` |
| Encapsulation | `consecutive_absences` changes only through `Student.update_absence_run()`; a locked register rejects `set_status()`; only `QrToken.is_valid()` validates a code. |

## What is simulated

- The classroom QR picture is drawn by the page. The value it encodes is real: an HMAC-signed room id and time stamp, replaced every 60 seconds and checked by the server.
- Scan times are typed in, to demonstrate the late, overtime and early-leave cases.
- EduTizim is represented by a daily export file. Submitted registers are written to `data/edutizim_outbox.jsonl` for re-import.
- Sign-in is by choosing a user. The real module uses EduTizim accounts (NFR-01.2).
