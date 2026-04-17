Autobuild Onetree - User Guide
==============================

Getting Started
---------------
- Login: Use your Linux system account (PAM). Only users in the allowed group can sign in.
- Navigation: Use the left sidebar to move between Dashboard, Create Job, Jobs, Recipes, Profile, and Settings.
- This service is intended for internal use on the local network.

Job Management
--------------
Creating a Job
- Go to "Create Job".
- Select a recipe from the list (platform/project).
- Optionally add a note, select a codebase, or choose a base job.
- Submit to create a new job.

Job States
- Pending: job is queued.
- Running: job is executing.
- Success: job finished with exit code 0.
- Failed: job finished with non-zero exit code.

Job Details
- Open a job to view status, timestamps, and exit code.
- Logs: stream in the job detail page.
- Artifacts: listed on the job detail page. Common outputs include:
  - .bin
  - .img
  - .mtd / .static.mtd
- New jobs should no longer show every log line twice; older logs may still contain duplicate lines from before the runner fix.

Pinning (New)
- Click the pin icon in the job list or detail header.
- Pinned jobs:
  - Stay at the top of the Jobs list.
  - Cannot be pruned or deleted until unpinned.
- Pin/unpin now uses the main Jobs form correctly and should update consistently in the list view.

Disk Space Management
---------------------
Workspace Pruning (Individual Job)
- "Prune Workspace" removes the job's work/ directory.
- Artifacts and logs are kept.
- Use pruning after a job succeeds or fails to free disk space.

Storage Maintenance (Global Settings)
- Open Settings and find the "Storage Maintenance" card.
- SState Cache cleanup:
  - Removes files not accessed for X days.
  - Uses access time (atime).
- Downloads cleanup:
  - Deletes only root-level files in /work/downloads.
  - Does not enter subdirectories to protect git repositories.
- Cleanup results are shown directly in the Settings page near the cleanup controls.

Profile / Tokens
----------------
- GitLab tokens are managed in the Profile page.
- Tokens are stored on the server under `/work/autobuild_workspace/secrets/gitlab/<username>.token`.
- The `.env` file for the service is `/opt/autobuild/.env`.

