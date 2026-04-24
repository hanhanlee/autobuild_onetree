# Autobuild Onetree

FastAPI + Jinja2 service for internal Yocto build operations, including job submission, log streaming, artifact access, token management, and shared workspace maintenance. The current deployment target is Ubuntu 24.04 with app code under `/opt/autobuild` and shared workspace data under `/work/autobuild_workspace`.

## Docs

- `DEPLOY_GUIDE.md`: full deployment, redeployment, systemd, nginx, `.env`, and verification.
- `USER_GUIDE.md`: day-to-day operator workflow, jobs, tokens, cleanup, and common usage issues.

## Quick Start (Ubuntu 24.04)

1) **Create user, group, and directories**
   ```bash
   sudo adduser --system --group autobuild
   getent group scm-bmc >/dev/null || sudo groupadd scm-bmc
   sudo usermod -aG scm-bmc autobuild
   sudo usermod -aG scm-bmc <your-linux-login-user>
   sudo mkdir -p /opt/autobuild /work/autobuild_workspace/jobs /work/autobuild_workspace/data
   sudo chown -R autobuild:scm-bmc /opt/autobuild /work/autobuild_workspace
   ```

2) **Copy code**
   ```bash
   sudo rsync -a ./ /opt/autobuild/
   sudo chown -R autobuild:scm-bmc /opt/autobuild
   ```

3) **Python env**
   ```bash
   sudo -u autobuild python3 -m venv /opt/autobuild/venv
   sudo -u autobuild /opt/autobuild/venv/bin/pip install --upgrade pip
   sudo -u autobuild /opt/autobuild/venv/bin/pip install -r /opt/autobuild/requirements.txt
   ```

4) **Environment file**
   ```bash
   sudo install -o autobuild -g scm-bmc -m 600 /dev/null /opt/autobuild/.env
   sudoedit /opt/autobuild/.env
   ```
   Recommended minimum content:
   ```env
   AUTOBUILD_SECRET_KEY=replace-with-a-long-random-secret
   AUTOBUILD_ALLOWED_GROUP=scm-bmc
   AUTOBUILD_GIT_HOST=gitlab.example.com
   AUTOBUILD_TIMEZONE=Asia/Taipei
   ```
   Note: `AUTOBUILD_WORKSPACE_ROOT`, `AUTOBUILD_JOBS_ROOT`, and `AUTOBUILD_DB` are set by the systemd unit. Changing only `.env` will not override those values.

5) **Per-user GitLab token**
   - Default token root is `/work/autobuild_workspace/secrets/gitlab/` and files are stored as `<username>.token`.
   - Tokens are saved via the UI at `/profile` (preferred), or by writing a JSON payload to the token file.

6) **Systemd service**
   ```bash
   sudo cp /opt/autobuild/systemd/autobuild.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now autobuild.service
   ```
   - The repo service file is now a conservative internal-use version.
   - It keeps `RequiresMountsFor=/work`, `RuntimeMaxSec=86400`, `RestartSec=3`, and `TimeoutStopSec=30`.
   - It intentionally does not enable aggressive sandboxing such as `ProtectSystem=strict` or `NoNewPrivileges=yes` until Yocto workspace access, git access, and reverse proxy behavior have been validated under those restrictions.

7) **Nginx reverse proxy**
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
2. Background task runs `/opt/autobuild/runner/run_job.sh <id>` as the `autobuild` service user.
3. Runner logs to `/work/autobuild_workspace/jobs/<id>/logs/build.log`, updates `status.json` and `exit_code`.
4. Runner workspace is `/work/autobuild_workspace/jobs/<id>/work`.
5. Runner collects artifacts into `/work/autobuild_workspace/jobs/<id>/artifacts/`.

## Notes
- Service uses environment vars:
   - `AUTOBUILD_DB` / `AUTO_BUILD_DB` (default `/work/autobuild_workspace/data/jobs.db`)
   - `AUTOBUILD_JOBS_ROOT` / `AUTO_BUILD_JOBS_ROOT` (default `/work/autobuild_workspace/jobs`)
   - `AUTOBUILD_WORKSPACE_ROOT` / `AUTO_BUILD_WORKSPACE_ROOT` (default `/work/autobuild_workspace`)
   - `AUTOBUILD_SECRET_KEY` / `AUTO_BUILD_SECRET_KEY` (session signing; set to a strong value)
   - `AUTOBUILD_ALLOWED_GROUP` (default `scm-bmc`; Linux users in this group can log in)
- Job execution behavior is driven by stored recipes and the runner script under `runner/run_job.sh`; verify recipe content and environment-specific build commands during deployment.
- Linux files use LF; runner/systemd/nginx files are ready for deployment.
- Recent runtime fixes already deployed internally:
   - POST forms now use CSRF protection.
   - Jobs pin toggle no longer uses nested forms.
   - Settings cleanup now shows feedback near the cleanup controls.
   - New jobs should no longer duplicate every log line in `build.log`.
