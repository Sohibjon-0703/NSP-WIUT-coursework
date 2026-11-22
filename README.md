# NSP Learning Centre - Teacher Workload, KPI and Retention module (high-fidelity prototype)

Systems Analysis and Design 6BUIS026C, Coursework 2.

## Run
1. Install Python 3.8 or newer.
2. In this folder run: `python run.py`
3. The browser opens at http://localhost:8000. Stop with Ctrl+C.

No packages are needed. Data is saved in `nsp_data.db` (SQLite) and survives restarts.
Use **Reset demo data** in the header to return to the starting data.

## Files
- `run.py` - the program: local web server, JSON API and SQLite storage
- `static/index.html` - the user interface (roles: Teacher, Administrator, Head Specialist, Director)

## Simulated
The room QR picture is drawn by the page, and check-in times are typed in to show late and overtime cases. There is no connection to EduTizim.
