Project Status Summary
======================

Verified State (2026-04-24)
---------------------------
- Repo HEAD: `7860e1c`
- Documentation has been realigned with current runtime and deployment behavior:
  - `DEPLOY_GUIDE.md`: full installation / redeployment guide
  - `USER_GUIDE.md`: day-to-day operator guide
  - `README.md`: concise project overview + doc entry points
  - `docs/prompt.txt`: updated from stale MVP prompt to current engineering brief
- Current deployment model documented in repo:
  - app root: `/opt/autobuild`
  - workspace root: `/work/autobuild_workspace`
  - jobs root: `/work/autobuild_workspace/jobs`
  - DB path: `/work/autobuild_workspace/data/jobs.db`
  - token root: `/work/autobuild_workspace/secrets/gitlab`
  - service user: `autobuild`
  - default login gate group: `scm-bmc`
- Current runner model documented and verified in code:
  - systemd starts the app as `autobuild`
  - job execution runs `/opt/autobuild/runner/run_job.sh <job_id>`
  - runner is not launched with `sudo -u <owner>` in the current code path

Purpose
-------
- FastAPI + Jinja2 web app for internal Yocto build job submission, monitoring, artifact access, token management, and shared workspace maintenance.

Project Structure
-----------------
- `app/`: FastAPI app, routes, DB access, job orchestration, settings, auth, templates rendering
- `runner/`: bash runner (`run_job.sh`) and patcher helpers
- `templates/`: Jinja2 pages
- `static/`: front-end JS assets
- `systemd/`, `nginx/`: deployment configs
- `docs/`: schema docs and internal engineering notes

Core Flow
---------
1. User logs in via PAM and allowed-group check.
2. User creates a job from the web UI.
3. App writes job metadata and job files under the workspace.
4. Background task starts `runner/run_job.sh <job_id>`.
5. Runner executes recipe-driven steps, writes logs, status, exit code, and artifacts.
6. UI shows status, logs, artifacts, and maintenance actions.

Current Features
----------------
- Login: PAM + allowed group + session cookie
- Profile: GitLab token and user profile storage under token root
- Jobs: create, list, detail, pin, retry, stop, prune, delete
- Logs: job detail polling plus SSE endpoint in backend
- Recipes: create, edit, copy, archive, delete
- Settings: cleanup thresholds, git host, disk threshold
- Dashboard: live jobs, recent jobs, disk usage and host signals
- Codebases: list / archive / delete based on workspace metadata

Verified Fixes / Alignments
---------------------------
- Documentation mismatch on token paths, deployment paths, and runner invocation has been corrected.
- The unused `codebase_id` job-creation path has been removed; reusable workspace flow is now represented by `base_job_id` / `base_job_path` only.
- Workspace pruning uses the canonical job `work/` directory.
- Job detail timestamp rendering no longer has the previously documented double-conversion issue.
- Artifact collection is broader than older docs implied: it now includes `*.bin`, `*.mtd`, `*.mtd.tar`, `*.static.mtd`, and `*.static.mtd.tar`.
- Recent UI/runtime fixes already reflected in docs:
  - cleanup feedback renders near cleanup controls
  - pin toggle no longer depends on nested forms
  - new logs should no longer duplicate every line

Current Open Items
------------------
- Operational hardening items remain environment-specific and may still need to be applied on the live machine:
  - `.env` file permissions on deployed hosts
  - systemd hardening validation before enabling stricter directives
  - review whether any extra port 8080 file server is still needed
- HTTPS is still optional / not enabled by default in the documented internal deployment.

Testing Status
--------------
- No formal automated test suite is present in `tests/`.
- Documentation consistency and deploy-script syntax were rechecked during the 2026-04-24 cleanup.

Notes
-----
- `PROJECT_STATUS.md` should describe verified current state only.
- Historical completed items belong in git history or repo memory, not in the active open-status summary.
