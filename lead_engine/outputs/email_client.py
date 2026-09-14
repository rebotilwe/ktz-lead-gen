"""
Sends an email digest summarizing a lead-collection run, with the CSV attached.

Works with any SMTP provider (Gmail, Outlook, a custom domain like KTZ's own
mail server) — just set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD in .env.

Two connection styles are handled automatically based on the port:
- Port 465: SSL from the start (used by most custom/hosted mail servers)
- Port 587 (or anything else): plain connection upgraded via STARTTLS (Gmail, etc.)

For Gmail specifically, SMTP_PASSWORD must be an "app password" generated at
https://myaccount.google.com/apppasswords — not your normal account password.
For a custom domain (e.g. KTZ's own mail server), SMTP_PASSWORD is just the
normal mailbox password for that email account.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import pandas as pd


def send_email_digest(df: pd.DataFrame, csv_path: str, smtp_host: str, smtp_port: int,
                       smtp_user: str, smtp_password: str, email_to: str,
                       business_type: str, location: str, source: str) -> None:
    """
    Send an email with a short summary of the run and the CSV attached.
    """
    priority_counts = df["lead_priority"].value_counts()
    hot_count = int(priority_counts.get("HOT", 0))
    high_count = int(priority_counts.get("HIGH", 0))
    medium_count = int(priority_counts.get("MEDIUM", 0))
    low_count = int(priority_counts.get("LOW", 0))

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg["Subject"] = f"KTZ Lead Engine: {business_type} in {location} ({source}) — {hot_count} HOT leads"

    body = (
        f"Lead collection run complete.\n\n"
        f"Business type: {business_type}\n"
        f"Location: {location}\n"
        f"Source: {source}\n\n"
        f"Total leads scored: {len(df)}\n"
        f"HOT: {hot_count}\n"
        f"HIGH: {high_count}\n"
        f"MEDIUM: {medium_count}\n"
        f"LOW: {low_count}\n\n"
        f"Full results attached as CSV, sorted by lead score (highest first)."
    )
    msg.attach(MIMEText(body, "plain"))

    with open(csv_path, "rb") as f:
        attachment = MIMEApplication(f.read(), Name=csv_path.split("/")[-1])
    attachment["Content-Disposition"] = f'attachment; filename="{csv_path.split("/")[-1]}"'
    msg.attach(attachment)

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
