#!/usr/bin/env python3
"""
send_email.py - Sends the daily HTML job digest through Gmail (App Password).

Environment variables (GitHub Secrets):
    GMAIL_USER          sender Gmail address
    GMAIL_APP_PASSWORD  16-character Google App Password
    EMAIL_TO            recipient (defaults to GMAIL_USER)
    PAGES_URL           optional; defaults to https://<owner>.github.io/<repo>/

Usage:
    python send_email.py             # send
    python send_email.py --preview   # write email_preview.html, send nothing
"""
from __future__ import annotations

import argparse
import html
import json
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCENT, INK = "#f26a21", "#0f1f33"


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def safe_url(url: str) -> str:
    return url if str(url).lower().startswith(("http://", "https://")) else "#"


def dashboard_url() -> str:
    if os.getenv("PAGES_URL"):
        return os.environ["PAGES_URL"]
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return "#"


def job_block(j: dict) -> str:
    skills = ", ".join(j.get("matched_skills", [])[:6]) or "-"
    badges = ""
    if j.get("reputed"):
        badges += f'<span style="background:#e4f4ec;color:#1f7a50;border-radius:4px;padding:1px 6px;font-size:11px;">Reputed</span> '
    if j.get("type") == "Consultancy":
        badges += '<span style="background:#e8eef7;color:#2c4d7c;border-radius:4px;padding:1px 6px;font-size:11px;">Consultancy</span> '
    salary = esc(j["salary_text"]) if j.get("salary_text") else f'Not disclosed &middot; typical {esc(j.get("market_band", ""))}' if j.get("market_band") else "Not disclosed"
    return f"""
<tr><td style="padding:12px 0;border-bottom:1px solid #e6e1d4;">
  <table width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="vertical-align:top;">
      <a href="{esc(safe_url(j['url']))}" style="font-size:16px;font-weight:700;color:{INK};text-decoration:none;">{esc(j['title'])}</a>
      <div style="font-size:13px;color:#44505c;margin-top:2px;">{esc(j['company'])} &middot; {esc(j['location'])} &middot; posted {esc(j['posted'])}</div>
      <div style="margin-top:4px;">{badges}<span style="font-size:12px;color:#66727f;">{esc(j.get('level',''))}{' &middot; ' + esc(j['experience']) if j.get('experience') else ''}</span></div>
      <div style="font-size:12px;color:#44505c;margin-top:4px;"><b>Matched:</b> {esc(skills)}</div>
      <div style="font-size:12px;color:#44505c;margin-top:2px;"><b>Salary:</b> {salary}</div>
    </td>
    <td style="vertical-align:top;text-align:right;width:110px;">
      <div style="font-size:20px;font-weight:700;color:{ACCENT};">{int(j['score'])}%</div>
      <div style="font-size:11px;color:#66727f;margin-bottom:6px;">match</div>
      <a href="{esc(safe_url(j['url']))}" style="background:{ACCENT};color:#fff;text-decoration:none;font-size:12px;font-weight:700;padding:7px 12px;border-radius:5px;display:inline-block;">Apply Now</a>
    </td>
  </tr></table>
</td></tr>"""


def build_email(data: dict, cfg: dict) -> tuple[str, str, str]:
    run_date = data.get("run_date", "")
    jobs = data.get("jobs", [])
    new = [j for j in jobs if j.get("first_seen") == run_date]
    order = {l["name"]: l["priority"] for l in cfg["locations"]}
    order["Remote"] = 50
    sort_key = lambda j: (order.get(j["region"], 98), -j["score"])
    new.sort(key=sort_key)
    cap = cfg["email"].get("max_jobs", 0)
    if cap:
        new = new[:cap]

    url = dashboard_url()
    show, heading = new, f"{len(new)} new matching jobs"
    if not new:
        heading = "No new matching jobs today"
        show = sorted(jobs, key=lambda j: -j["score"])[: cfg["email"].get("fallback_top_matches", 5)]

    sections, current = [], None
    for j in show:
        if j["region"] != current:
            current = j["region"]
            sections.append(f'<tr><td style="padding:16px 0 4px;font-size:12px;letter-spacing:1px;text-transform:uppercase;color:{ACCENT};font-weight:700;">{esc(current)}</td></tr>')
        sections.append(job_block(j))
    if not new and show:
        sections.insert(0, '<tr><td style="padding:8px 0;font-size:13px;color:#44505c;">Strongest open matches still on the dashboard:</td></tr>')
    body_rows = "\n".join(sections) or '<tr><td style="padding:12px 0;color:#44505c;">Nothing on the dashboard yet.</td></tr>'

    page = f"""<!doctype html><html><body style="margin:0;background:#f5f2ea;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:16px;">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;background:#fffdf8;border-radius:8px;overflow:hidden;">
 <tr><td style="background:{INK};padding:20px 24px;">
   <div style="color:{ACCENT};font-size:12px;letter-spacing:2px;font-weight:700;">SITE DESK &middot; {esc(run_date)}</div>
   <div style="color:#fff;font-size:22px;font-weight:700;margin-top:4px;">{esc(heading)}</div>
   <div style="color:#b8c4d2;font-size:13px;margin-top:2px;">Planning &middot; Billing &middot; QS &middot; updated {esc(data.get('last_updated_display',''))}</div>
 </td></tr>
 <tr><td style="padding:8px 24px 20px;"><table width="100%" cellpadding="0" cellspacing="0">{body_rows}</table>
   <div style="text-align:center;margin-top:20px;"><a href="{esc(url)}" style="background:{INK};color:#fff;text-decoration:none;font-weight:700;padding:11px 22px;border-radius:6px;display:inline-block;">Open full dashboard</a></div>
 </td></tr>
 <tr><td style="padding:12px 24px;background:#efeadc;font-size:11px;color:#66727f;">Salaries shown only where the listing discloses them; location bands are rough guides. Verify every listing before applying.</td></tr>
</table></td></tr></table></body></html>"""

    text_lines = [heading, f"Dashboard: {url}", ""]
    text_lines += [f"- [{j['region']}] {j['title']} | {j['company']} | {j['location']} | {int(j['score'])}% | {j['url']}" for j in show]
    subject = f"Site Desk {run_date}: {len(new)} new job{'s' if len(new) != 1 else ''}" if new else f"Site Desk {run_date}: no new jobs today"
    return subject, page, "\n".join(text_lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "jobs.json").read_text(encoding="utf-8"))
    subject, page, text = build_email(data, cfg)

    new_count = sum(1 for j in data.get("jobs", []) if j.get("first_seen") == data.get("run_date"))
    if args.preview:
        (ROOT / "email_preview.html").write_text(page, encoding="utf-8")
        print(f"Wrote email_preview.html ({subject})")
        return 0
    if new_count == 0 and not cfg["email"].get("send_when_empty", True):
        print("No new jobs and send_when_empty is false - not sending.")
        return 0

    user, password = os.getenv("GMAIL_USER"), os.getenv("GMAIL_APP_PASSWORD")
    to = os.getenv("EMAIL_TO") or user
    if not (user and password and to):
        print("Email skipped: GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_TO secrets are not set.")
        return 0

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(page, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as server:
            server.login(user, password)
            server.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Email FAILED: {type(exc).__name__}. Check the App Password and that 2-Step Verification is on.")
        return 1
    print(f"Email sent: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
