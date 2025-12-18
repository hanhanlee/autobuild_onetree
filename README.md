# Yocto Auto Build Web (MVP)

Minimal FastAPI + Jinja2 service to submit Yocto build jobs, stream logs, and fetch artifacts. Designed for Ubuntu 24.04 deployment under `/opt/autobuild` with jobs stored at `/srv/autobuild/jobs/<job_id>/`.

## Quick Start (Ubuntu 24.04)

1) **Create user and directories**
   ```bash
   sudo adduser --system --group autobuild
   sudo mkdir -p /opt/autobuild /srv/autobuild/jobs /srv/autobuild/data
   sudo chown -R autobuild:autobuild /opt/autobuild /srv/autobuild
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
   - For each Linux user allowed to run jobs, create `~username/.autobuild` (700) and `gitlab_token` inside (600) containing the PAT (repo read/clone scope).
   ```bash
   sudo -u <user> mkdir -p ~/.autobuild
   sudo -u <user> sh -c 'echo "<PAT>" > ~/.autobuild/gitlab_token'
   sudo chmod 700 ~/.autobuild
   sudo chmod 600 ~/.autobuild/gitlab_token
   ```
   - Alternatively, users can log in to `/settings` and save the token themselves.

6) **Systemd service**
   ```bash
   sudo cp /opt/autobuild/systemd/autobuild.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now autobuild.service
   ```

7) **Nginx reverse proxy**
   ```bash
   sudo cp /opt/autobuild/nginx/autobuild.conf /etc/nginx/sites-available/
   sudo ln -s /etc/nginx/sites-available/autobuild.conf /etc/nginx/sites-enabled/autobuild.conf
   sudo nginx -t && sudo systemctl reload nginx
   ```

## Usage
- Web app: `http://<host>/`
- Login uses PAM (Linux only). Session stored in signed cookie.
- Set GitLab PAT in `/settings` (saved to `~/.autobuild/gitlab_token`).
- Submit jobs via `/new` with repo URL, ref, machine, target.
- Job page (`/jobs/<id>`) streams log via SSE and lists artifacts.

## Job flow
1. Web inserts job into SQLite at `/srv/autobuild/data/jobs.db` (override with `AUTO_BUILD_DB` env).
2. Background task runs `sudo -u <owner> /opt/autobuild/runner/run_job.sh <id> <repo> <ref> <machine> <target>`.
3. Runner logs to `/srv/autobuild/jobs/<id>/logs/build.log`, updates `status.json` and `exit_code`.
4. Runner copies artifacts from `build/tmp/deploy/images/<machine>/*.bin|*.mtd` into `/srv/autobuild/jobs/<id>/artifacts/` if present.

## Notes
- Service uses environment vars:
  - `AUTO_BUILD_DB` (default `/srv/autobuild/data/jobs.db`)
  - `AUTO_BUILD_JOBS_ROOT` (default `/srv/autobuild/jobs`)
  - `AUTO_BUILD_SECRET_KEY` (session signing; set to a strong value)
- Runner currently contains a placeholder build step; replace with real Yocto build command.
- Linux files use LF; runner/systemd/nginx files are ready for deployment.
