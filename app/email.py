import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Optional


def send_job_notification(job: Dict[str, object], status: str, owner_profile: Dict[str, object]) -> None:
    smtp_server = os.environ.get("SMTP_SERVER")
    smtp_port = int(os.environ.get("SMTP_PORT") or 0)
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    if not (smtp_server and smtp_port and smtp_user and smtp_password):
        return

    to_addrs = []
    owner_email = (owner_profile.get("email") or "").strip() if owner_profile else ""
    if owner_email:
        to_addrs.append(owner_email)
    cc_raw = (job.get("cc_emails") or "").strip() if job else ""
    if cc_raw:
        for addr in cc_raw.split(","):
            addr_clean = addr.strip()
            if addr_clean:
                to_addrs.append(addr_clean)
    # Deduplicate recipients
    to_addrs = list(dict.fromkeys(to_addrs))
    if not to_addrs:
        return

    job_id = job.get("id") if job else None
    project_name = job.get("recipe_id") or job.get("note") or "job"
    subject = f"[BuildServer] Job #{job_id} {status}: {project_name}"
    link = f"/jobs/{job_id}" if job_id else "#"
    body = (
        f"Job #{job_id} finished with status: {status}.\n"
        f"View details: {link}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_addrs, msg.as_string())
    except Exception:
        # Do not raise; email failures should not break the app
        return
