Project Status Summary (for AI helper)
======================================

Purpose
-------
- FastAPI + Jinja2 web app for Yocto build job submission, logs, artifacts.
- Target deployment: Ubuntu 24.04, /opt/autobuild, jobs under workspace root.

Project Structure
-----------------
- app/: FastAPI app, routes, DB, job orchestration, templates rendering.
- runner/: bash runner (run_job.sh) + patcher.py for file edits.
- templates/: Jinja2 pages.
- static/: JS assets.
- systemd/, nginx/: deployment configs.
- docs/: recipe schema docs.
- tests/: empty.

Core Flow
---------
1) User logs in via PAM group auth.
2) Create job -> DB insert + job.json/raw_recipe.yaml.
3) Background task starts runner /opt/autobuild/runner/run_job.sh <job_id>.
4) Runner creates work dir, executes recipe blocks, writes status.json/exit_code/log.
5) Server polls status, updates DB, collects artifacts, sends email (if SMTP configured).
6) UI polls log/status; artifacts listed on job page.

Key Features (Current)
----------------------
- Login: PAM + allowed group; session cookie.
- Profile: GitLab tokens + email stored under token root.
- Jobs: create, list, detail, pin, stop, retry, prune, delete.
- Logs: JSON chunk polling (/jobs/{id}/log/stream) + SSE endpoint (unused in UI).
- Recipes: list, edit, create, copy, archive, delete (filesystem-based).
- Projects (legacy API): deprecated endpoints return 410.
- Settings: prune/delete days, gitlab host, disk min free.
- Dashboard: live jobs, recent jobs, disk usage, sensors (sensors -j).
- Codebases: list/archive/delete based on workspace metadata.

Data Stores / Paths
-------------------
- SQLite DB: AUTO_BUILD_DB or {root}/data/jobs.db (app/config.py).
- Jobs root: AUTOBUILD_JOBS_ROOT or {workspace_root}/jobs.
- Workspace root: AUTOBUILD_WORKSPACE_ROOT or {root}/workspace.
- Token root: AUTOBUILD_TOKEN_ROOT or {workspace_root}/secrets/gitlab.
- Runner work dir: {job_dir}/work (runner/run_job.sh).

Known Issues / Gaps (from review)
---------------------------------
- [High] workspace vs work mismatch:
  - Prune/housekeeping and UI copy paths use "workspace".
  - Runner actually uses "work".
  - Result: prune does not free real build dir; UI path is wrong.
- [High] Timezone double-conversion on job detail:
  - Handler formats to Taipei, then template filter converts again.
  - Result: +8 hours shift.
- [Medium] codebase_id is saved but not used by runner:
  - UI supports codebase selection, runner ignores it.
- [Medium] Artifact collection mismatch:
  - README says deploy/images/*.bin|*.mtd.
  - Code only collects *.static.mtd and *.static.mtd.tar.
- [Low] Token path inconsistencies:
  - README/UI mention ~/.autobuild or /opt/autobuild/workspace.
  - Actual code uses {workspace_root}/secrets/gitlab/{user}.token.
- [Low] SSE log stream exists but UI uses JSON polling; static/app.js unused.

Consistency Notes
-----------------
- Timestamps are stored as ISO UTC strings (jobs.now_iso()).
- Multiple display conversions exist (app/web.py filter, jobs route custom formatter).
- Suggest single formatting path to avoid inconsistency.

Testing Status
--------------
- tests/ is empty; no automated coverage.

Open Questions
--------------
- Should "work" be the canonical job workspace dir? If yes, update prune/UI paths.
- Should timezone be fixed to Asia/Taipei or configurable?
- Should codebase_id be wired into runner behavior or removed?

