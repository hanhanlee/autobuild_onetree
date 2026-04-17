# Yocto Auto Build Web (MVP)

FastAPI + Jinja2 service to submit Yocto build jobs, stream logs, and fetch artifacts. The current internal deployment target is Ubuntu 24.04 with app code under `/opt/autobuild` and shared workspace data under `/work/autobuild_workspace`.

## Quick Start (Ubuntu 24.04)

1) **Create user and directories**
   ```bash
   sudo adduser --system --group autobuild
   sudo groupadd scm-bmc
   sudo usermod -aG scm-bmc autobuild
   sudo mkdir -p /opt/autobuild /work/autobuild_workspace/jobs /work/autobuild_workspace/data
   sudo chown -R autobuild:scm-bmc /opt/autobuild /work/autobuild_workspace
   ```

2) **Copy code**
   ```bash
   sudo rsync -a ./ /opt/autobuild/
   sudo chown -R autobuild:autobuild /opt/autobuild
   ```

3) **Python env**
   ```bash
   sudo -u autobuild python3 -m venv /opt/autobuild/venv
   sudo -u autobuild /opt/autobuild/venv/bin/pip install --upgrade pip
   sudo -u autobuild /opt/autobuild/venv/bin/pip install -r /opt/autobuild/requirements.txt
   ```

4) **Sudoers (allow run_job.sh only)**
   ```bash
   sudo visudo -f /etc/sudoers.d/autobuild
   # content:
   autobuild ALL=(ALL) NOPASSWD: /opt/autobuild/runner/run_job.sh
   ```

5) **Per-user GitLab token**
   - Default token root is `/work/autobuild_workspace/secrets/gitlab/` and files are stored as `<username>.token`.
   - Tokens are saved via the UI at `/profile` (preferred), or by writing a JSON payload to the token file.

6) **Environment file**
   - Production uses `/opt/autobuild/.env`.
   - The deploy script now enforces mode `0600`, but verify the file permissions on the machine after deployment.

7) **Systemd service**
   ```bash
   sudo cp /opt/autobuild/systemd/autobuild.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now autobuild.service
   ```
   - The repo service file is now a conservative internal-use version.
   - It keeps `RequiresMountsFor=/work`, `RuntimeMaxSec=86400`, `RestartSec=3`, and `TimeoutStopSec=30`.
   - It intentionally does not enable aggressive sandboxing such as `ProtectSystem=strict` or `NoNewPrivileges=yes` because the current app still uses `sudo` and per-user home directory credential flows.

8) **Nginx reverse proxy**
   ```bash
   sudo cp /opt/autobuild/nginx/autobuild.conf /etc/nginx/sites-available/
   sudo ln -s /etc/nginx/sites-available/autobuild.conf /etc/nginx/sites-enabled/autobuild.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

## Usage
- Web app: `http://<host>/`
- Login uses PAM (Linux only). Session stored in signed cookie.
- Set GitLab PATs in `/profile` (stored under the token root).
- Submit jobs via `/new` with repo URL, ref, machine, target.
- Job page (`/jobs/<id>`) polls logs via chunked JSON and lists artifacts (SSE endpoint also exists at `/api/jobs/<id>/log/stream`).
- This system is currently intended for internal network use. HTTPS is not enabled by default.

## Job flow
1. Web inserts job into SQLite at `/work/autobuild_workspace/data/jobs.db` (override with `AUTOBUILD_DB` or `AUTO_BUILD_DB`).
2. Background task runs `sudo -u <owner> /opt/autobuild/runner/run_job.sh <id> <repo> <ref> <machine> <target>`.
3. Runner logs to `/work/autobuild_workspace/jobs/<id>/logs/build.log`, updates `status.json` and `exit_code`.
4. Runner workspace is `/work/autobuild_workspace/jobs/<id>/work`.
5. Runner collects artifacts into `/work/autobuild_workspace/jobs/<id>/artifacts/`.

## Notes
- Service uses environment vars:
   - `AUTOBUILD_DB` / `AUTO_BUILD_DB` (default `/work/autobuild_workspace/data/jobs.db`)
   - `AUTOBUILD_JOBS_ROOT` / `AUTO_BUILD_JOBS_ROOT` (default `/work/autobuild_workspace/jobs`)
   - `AUTOBUILD_WORKSPACE_ROOT` / `AUTO_BUILD_WORKSPACE_ROOT` (default `/work/autobuild_workspace`)
   - `AUTOBUILD_SECRET_KEY` / `AUTO_BUILD_SECRET_KEY` (session signing; set to a strong value)
- Runner currently contains a placeholder build step; replace with real Yocto build command.
- Linux files use LF; runner/systemd/nginx files are ready for deployment.
- Recent runtime fixes already deployed internally:
   - POST forms now use CSRF protection.
   - Jobs pin toggle no longer uses nested forms.
   - Settings cleanup now shows feedback near the cleanup controls.
   - New jobs should no longer duplicate every log line in `build.log`.
