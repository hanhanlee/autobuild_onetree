Autobuild Onetree - Deployment Guide
====================================

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
sudo apt-get install -y python3-venv libpam0g-dev
```

Section 2: User & Group Setup (CRITICAL)
----------------------------------------
The system relies on a shared group for permission management.

```bash
sudo groupadd scm-bmc
sudo usermod -aG scm-bmc $USER
```

Log out and back in so group membership takes effect.

Section 3: Directory Structure & Permissions
---------------------------------------------
Required paths:
- App directory: /opt/autobuild
- Work directory: /work (mount a large SSD/NVMe volume here)
- Workspace root: /work/autobuild_workspace
- Jobs root: /work/autobuild_workspace/jobs
- DB path: /work/autobuild_workspace/data/jobs.db
- Token root: /work/autobuild_workspace/secrets/gitlab

Set ownership, permissions, and SetGID (g+s):
```bash
sudo mkdir -p /opt/autobuild /work/autobuild_workspace/jobs /work/autobuild_workspace/data /work/autobuild_workspace/secrets/gitlab
sudo chown -R autobuild:scm-bmc /opt/autobuild /work/autobuild_workspace
sudo find /opt/autobuild /work/autobuild_workspace -type d -exec chmod 2775 {} +
sudo find /opt/autobuild /work/autobuild_workspace -type f -exec chmod 664 {} +
```

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
```

Section 4.1: Optional - Deployment Script (tools/)
--------------------------------------------------
The repo includes `tools/deploy_autobuild.sh` for guided deploy and updates.
It uses:
- DEST_DIR=/opt/autobuild
- SERVICE_NAME=autobuild
- TARGET_USER=autobuild
- TARGET_GROUP=scm-bmc

If your service user/name differs, edit the variables at the top of the script.

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
  - do not enable `ProtectSystem=strict`, `ProtectHome=yes`, or `NoNewPrivileges=yes` unless the `sudo` + user-home credential flow is removed first

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

Nginx (Optional)
- Use `nginx/autobuild.conf` as a reverse proxy (port 80 -> 8000).
- HTTPS is not enabled by default in the current internal deployment.

Section 6: Verification
-----------------------
```bash
systemctl status autobuild
systemctl show autobuild -p RuntimeMaxUSec -p RestartUSec -p TimeoutStopUSec -p RequiresMountsFor
```

Access the Web UI:
- http://<server-ip>:8000

Run a Test
- Log in with a local Linux account (PAM user).

Section 7: Troubleshooting
--------------------------
- Permission denied:
  - Confirm the user is in the `scm-bmc` group and re-login.
- Bitbake command not found:
  - Confirm Yocto dependencies were installed in Section 1.
- `.env` still too open:
  - Run `sudo chmod 600 /opt/autobuild/.env && sudo chown autobuild:scm-bmc /opt/autobuild/.env`
- Build log lines appear twice:
  - Confirm `/opt/autobuild/runner/run_job.sh` includes the stdout/log-file duplicate guard and test with a new job.
- Extra port 8080 open:
  - `python3 -m http.server 8080` is not part of the main app; stop it if not needed.
