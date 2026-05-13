Autobuild Onetree - Deployment Guide
====================================

This guide reflects the current code path and the internal deployment layout. If you follow it on a clean Ubuntu host, you should be able to redeploy the full Autobuild system without guessing missing steps.

Section 0: One-Command Host Bootstrap (Recommended)
---------------------------------------------------
For a brand-new Ubuntu host, `scripts/prepare_host.sh` performs everything in Sections 1, 2, and 3 below in a single idempotent run:

```bash
git clone <repo-url> /tmp/autobuild-src
sudo bash /tmp/autobuild-src/scripts/prepare_host.sh
```

What it does:
- Installs all OS / Yocto / app dependencies (apt)
- Creates the `scm-bmc` group and the `autobuild` service user
- Creates `/opt/autobuild` and `/work/autobuild_workspace/{jobs,data,secrets/gitlab}` with SetGID `2775` and `secrets/gitlab` at `2770`
- Adds the invoking sudo user to `scm-bmc` (re-login required)

What it intentionally does NOT do:
- It does not place source code under `/opt/autobuild` — clone or rsync after this step, then run `tools/deploy_autobuild.sh`
- It does not create `/opt/autobuild/.env` — create with `sudoedit` and reference `.env.example`
- It does not install the systemd unit — do that once `.env` is in place

Re-running `prepare_host.sh` is safe; it only adds what is missing. Use `--skip-apt` to skip the OS package step on hosts where you cannot run `apt-get install`.

If you prefer doing things by hand, follow Sections 1-3 manually instead.

Section 1: System Prerequisites
-------------------------------
OS
- Ubuntu 22.04 LTS or 24.04 LTS.

Yocto Dependencies (Required for Bitbake)
```bash
sudo apt-get update
sudo apt-get install -y \
  gawk wget git diffstat unzip texinfo gcc-multilib build-essential chrpath \
  socat cpio python3 python3-pip python3-pexpect xz-utils debianutils \
  iputils-ping python3-git python3-jinja2 libegl1-mesa libsdl1.2-dev \
  pylint3 xterm python3-subunit mesa-common-dev zstd liblz4-tool
```

App Dependencies
```bash
sudo apt-get install -y python3-venv libpam0g-dev rsync git nginx
```

Notes
- `simplepam` / `python-pam` require PAM development headers, so `libpam0g-dev` is mandatory.
- `rsync` is required by `tools/deploy_autobuild.sh`.
- `nginx` is optional only if you expose Uvicorn directly on port 8000.

Section 2: User & Group Setup (CRITICAL)
----------------------------------------
The system relies on a shared group for permission management and login authorization.

```bash
getent group scm-bmc >/dev/null || sudo groupadd scm-bmc
id autobuild >/dev/null 2>&1 || sudo adduser --system --group autobuild
sudo usermod -aG scm-bmc autobuild
sudo usermod -aG scm-bmc $USER
```

Log out and back in so group membership takes effect.

Important
- The web UI uses PAM authentication against local Linux accounts.
- A user can log in only if that Linux account is in `AUTOBUILD_ALLOWED_GROUP`.
- If you keep the default value, that group must be `scm-bmc`.

Section 3: Directory Structure & Permissions
---------------------------------------------
Required paths:
- App directory: /opt/autobuild
- Work directory: /work (mount a large SSD/NVMe volume here)
- Workspace root: /work/autobuild_workspace
- Jobs root: /work/autobuild_workspace/jobs
- DB path: /work/autobuild_workspace/data/jobs.db
- Token root: /work/autobuild_workspace/secrets/gitlab

The current service unit also expects `/work` to be mounted before startup.

Set ownership, permissions, and SetGID (g+s):
```bash
sudo mkdir -p /opt/autobuild /work/autobuild_workspace/jobs /work/autobuild_workspace/data /work/autobuild_workspace/secrets/gitlab
sudo chown -R autobuild:scm-bmc /opt/autobuild /work/autobuild_workspace
sudo find /opt/autobuild /work/autobuild_workspace -type d -exec chmod 2775 {} +
sudo find /opt/autobuild /work/autobuild_workspace -type f -exec chmod 664 {} +
sudo chmod 2770 /work/autobuild_workspace/secrets/gitlab
```

Recommended backup targets before redeploy:
- `/opt/autobuild/.env`
- `/work/autobuild_workspace/data/jobs.db`
- `/work/autobuild_workspace/secrets/gitlab/`

Section 4: Installation
-----------------------
Clone the repository and set up the Python virtual environment:
```bash
cd /opt
git clone https://github.com/hanhanlee/autobuild_onetree autobuild
cd /opt/autobuild
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create the environment file:
```bash
sudo install -o autobuild -g scm-bmc -m 600 /dev/null /opt/autobuild/.env
sudoedit /opt/autobuild/.env
```

Minimum recommended `.env`:
```env
AUTOBUILD_SECRET_KEY=replace-with-a-long-random-secret
AUTOBUILD_ALLOWED_GROUP=scm-bmc
AUTOBUILD_GIT_HOST=gitlab.example.com
AUTOBUILD_TIMEZONE=Asia/Taipei
AUTOBUILD_LOG_POLLING_MS=1000
AUTOBUILD_HOUSEKEEPING_INTERVAL=3600
AUTOBUILD_DISK_MIN_FREE_GB=5
# Hostname or IP shown in the jobs UI "Copy SSH command" button.
# If left empty, the app falls back to socket.gethostname(). On a standby
# host, set this to the standby's own hostname so users do not get pointed
# back to the primary.
AUTOBUILD_SSH_HOST=
```

See [`.env.example`](.env.example) for the full annotated list (build timeout, max concurrent jobs, SMTP, etc.).

Optional `.env` values:
- `AUTOBUILD_BASE_PATH` when Autobuild is served below a subpath such as `/autobuild`.
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` if you want email notifications.
- `SSTATE_DIR` and `DL_DIR` if the Settings cleanup page should point to non-default cache paths.

Important
- `app.main` loads `.env` from the working directory, which is `/opt/autobuild` in the shipped systemd unit.
- `AUTOBUILD_WORKSPACE_ROOT`, `AUTOBUILD_JOBS_ROOT`, and `AUTOBUILD_DB` are already set inside `systemd/autobuild.service`.
- Because `load_dotenv()` does not override existing environment variables, changing those three values only in `.env` has no effect. Use a systemd override if you need different paths.

Section 4.1: Optional - Deployment Script (tools/)
--------------------------------------------------
The repo includes `tools/deploy_autobuild.sh` for guided deploy and updates.
It uses:
- DEST_DIR=/opt/autobuild
- SERVICE_NAME=autobuild
- TARGET_USER=autobuild
- TARGET_GROUP=scm-bmc

If your service user/name differs, edit the variables at the top of the script.

What the script does:
- `Full Deploy`: git pull, rsync code, create/update venv, install Python packages, fix permissions, restart systemd.
- `Fast Deploy`: git pull, rsync code, fix permissions, restart systemd.
- `Sync Code Only`: rsync code and fix permissions only.

What the script does not bootstrap for you:
- It does not create the `autobuild` Linux user.
- It does not create or fill `/opt/autobuild/.env`.
- It does not install or enable the systemd unit and nginx for the first time.

Run the script from the repo root:
```bash
cd /opt/autobuild
bash tools/deploy_autobuild.sh
```

You will be prompted to select a deploy action (full deploy, fast deploy, sync only, etc.).

Section 5: Configuration
------------------------
Systemd Service
- Copy the service file from the repo:
```bash
sudo cp /opt/autobuild/systemd/autobuild.service /etc/systemd/system/autobuild.service
```
- Reload and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autobuild
```
- Current recommended unit style for this project is conservative internal-use:
  - keep `RequiresMountsFor=/work`
  - keep `RuntimeMaxSec=86400`
  - keep `RestartSec=3`
  - keep `TimeoutStopSec=30`
  - do not enable `ProtectSystem=strict`, `ProtectHome=yes`, or `NoNewPrivileges=yes` until you have validated that Yocto workspaces, git access, and reverse proxy behavior still work under those restrictions

Optional override file already seen on the current internal host:
```bash
sudo mkdir -p /etc/systemd/system/autobuild.service.d
sudoedit /etc/systemd/system/autobuild.service.d/override.conf
```

Example conservative override:
```ini
[Service]
ReadWritePaths=/work
ProtectSystem=off
ProtectHome=off
PrivateTmp=false

[Unit]
RequiresMountsFor=/work
```

Example override when you need different workspace paths:
```ini
[Service]
Environment="AUTOBUILD_WORKSPACE_ROOT=/srv/autobuild_workspace"
Environment="AUTOBUILD_JOBS_ROOT=/srv/autobuild_workspace/jobs"
Environment="AUTOBUILD_DB=/srv/autobuild_workspace/data/jobs.db"
```

Nginx (Optional)
- Use `nginx/autobuild.conf` as a reverse proxy (port 80 -> 8000).
- HTTPS is not enabled by default in the current internal deployment.

Section 6: Verification
-----------------------
```bash
systemctl status autobuild
systemctl show autobuild -p RuntimeMaxUSec -p RestartUSec -p TimeoutStopUSec -p RequiresMountsFor
journalctl -u autobuild -n 100 --no-pager
```

Access the Web UI:
- http://<server-ip>:8000
- If nginx is enabled: http://<server-ip>/

Run a Test
- Log in with a local Linux account (PAM user).
- Open Profile and save at least one GitLab token.
- Submit one test job and confirm these paths are created:
  - `/work/autobuild_workspace/jobs/<job_id>/logs/build.log`
  - `/work/autobuild_workspace/jobs/<job_id>/artifacts/`
  - `/work/autobuild_workspace/jobs/<job_id>/status.json`

Section 6.1: Redeploy / Upgrade Workflow
----------------------------------------
Recommended zero-surprise flow:
```bash
sudo systemctl stop autobuild
sudo cp /opt/autobuild/.env /opt/autobuild/.env.bak.$(date +%F-%H%M%S)
sudo cp /work/autobuild_workspace/data/jobs.db /work/autobuild_workspace/data/jobs.db.bak.$(date +%F-%H%M%S)
cd /opt/autobuild
sudo bash tools/deploy_autobuild.sh
sudo systemctl status autobuild --no-pager
```

If you are deploying from a developer checkout instead of the server copy, sync the repo to `/opt/autobuild` first and then run the same script.

Section 6.2: Standby Host (A + B Topology)
------------------------------------------
The CEO project owns the cross-machine sync script. To bring up an Autobuild standby host:

1. On the standby (host B), run `sudo bash scripts/prepare_host.sh` from a clone of this repo.
2. Place the source code under `/opt/autobuild` (clone + `tools/deploy_autobuild.sh`).
3. On the primary (host A), push the live secrets:
   ```bash
   cd ~/Project/CEO
   make sync-standby H=user@host-b
   ```
   This copies `/opt/autobuild/.env` and `/work/autobuild_workspace/secrets/gitlab/` to B with the correct ownership and modes. Source code, `jobs.db`, and artifacts are intentionally NOT copied — they are reproducible.
4. On B, edit `/opt/autobuild/.env` and set `AUTOBUILD_SSH_HOST` to B's own hostname so the UI "Copy SSH command" button points at B, not A.
5. Install the systemd unit and start the service on B.

Section 7: Troubleshooting
--------------------------
- Permission denied:
  - Confirm the user is in the `scm-bmc` group and re-login.
  - Confirm `/work/autobuild_workspace` is owned by `autobuild:scm-bmc` and directories remain `2775`.
- Bitbake command not found:
  - Confirm Yocto dependencies were installed in Section 1.
- `.env` still too open:
  - Run `sudo chmod 600 /opt/autobuild/.env && sudo chown autobuild:scm-bmc /opt/autobuild/.env`
- Login fails for a valid Linux user:
  - Confirm `/opt/autobuild/.env` sets the intended `AUTOBUILD_ALLOWED_GROUP`, then restart `autobuild`.
- Changed workspace path but app still uses `/work/autobuild_workspace`:
  - Update the systemd unit or override file, then run `sudo systemctl daemon-reload && sudo systemctl restart autobuild`.
- Build log lines appear twice:
  - Confirm `/opt/autobuild/runner/run_job.sh` includes the stdout/log-file duplicate guard and test with a new job.
- Extra port 8080 open:
  - `python3 -m http.server 8080` is not part of the main app; stop it if not needed.
- Token saved in UI but build still says token missing:
  - Confirm the token file exists under `/work/autobuild_workspace/secrets/gitlab/<username>.token` and is readable by group `scm-bmc`.
