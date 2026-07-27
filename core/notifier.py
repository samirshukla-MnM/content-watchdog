"""
Builds and sends the change-alert email.

One digest email per run containing every changed page, so a busy day doesn't
produce 40 separate emails. Changes are grouped by report so you can see your
page and its competitors side by side.
"""
import html
import os
import smtplib
from email.message import EmailMessage

from .differ import render_diff_html

CSS = """
body{margin:0;padding:0;background:#eef1f5;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;}
.wrap{max-width:820px;margin:0 auto;padding:24px 14px;}
.card{background:#fff;border-radius:10px;overflow:hidden;
      box-shadow:0 1px 3px rgba(16,24,40,.09);margin-bottom:20px;}
.hd{background:#0f2b46;color:#fff;padding:22px 26px;}
.hd h1{margin:0;font-size:19px;font-weight:600;letter-spacing:.2px;}
.hd p{margin:6px 0 0;font-size:13px;color:#a9c2d8;}
.stats{display:block;padding:16px 26px;background:#f7f9fc;
       border-bottom:1px solid #e4e9f0;font-size:13px;color:#425466;}
.stat{display:inline-block;margin-right:26px;}
.stat b{display:block;font-size:20px;color:#0f2b46;font-weight:600;}
.grp{padding:6px 26px 0;font-size:11px;font-weight:700;color:#7b8794;
     text-transform:uppercase;letter-spacing:1.1px;margin-top:14px;}
.pg{padding:18px 26px;border-bottom:1px solid #edf1f6;}
.pg:last-child{border-bottom:none;}
.badge{display:inline-block;font-size:10px;font-weight:700;padding:3px 9px;
       border-radius:11px;text-transform:uppercase;letter-spacing:.6px;
       vertical-align:middle;}
.b-own{background:#dcf3e4;color:#12683a;}
.b-comp{background:#fde9d9;color:#9a4b12;}
.pg h3{margin:9px 0 3px;font-size:15px;color:#12263f;font-weight:600;}
.url{font-size:12px;color:#5b7085;word-break:break-all;text-decoration:none;}
.pct{float:right;font-size:12px;font-weight:700;color:#b42318;
     background:#fee4e2;padding:3px 10px;border-radius:11px;}
.sec-title{font-size:11px;font-weight:700;color:#525f70;text-transform:uppercase;
           letter-spacing:.8px;margin:16px 0 7px;padding-bottom:5px;
           border-bottom:1px solid #e9edf3;}
.cmp{width:100%;border-collapse:collapse;margin-bottom:9px;
     border:1px solid #e4e9f0;border-radius:6px;}
.cmp td{padding:8px 11px;font-size:13px;line-height:1.55;vertical-align:top;}
.lbl{width:74px;font-size:10px;font-weight:700;color:#7b8794;
     text-transform:uppercase;letter-spacing:.5px;background:#f7f9fc;
     border-right:1px solid #e4e9f0;}
td.old{background:#fff8f8;color:#6b2019;}
td.new{background:#f6fdf8;color:#12432a;}
.row{padding:7px 11px;font-size:13px;line-height:1.55;margin-bottom:5px;
     border-radius:5px;}
.row.old{background:#fff1f0;color:#6b2019;border-left:3px solid #f04438;}
.row.new{background:#eefaf2;color:#12432a;border-left:3px solid #12b76a;}
.del{background:#ffcdc9;color:#912018;text-decoration:line-through;
     padding:1px 3px;border-radius:3px;}
.ins{background:#b9f0cf;color:#05603a;padding:1px 3px;border-radius:3px;
     font-weight:600;}
.more{font-size:12px;color:#7b8794;font-style:italic;margin:6px 0 0;}
.ft{padding:16px 26px;font-size:11px;color:#8a97a6;background:#f7f9fc;
    text-align:center;line-height:1.7;}
.err{padding:12px 26px;background:#fffaeb;color:#93370d;font-size:12px;
     border-top:1px solid #fef0c7;}
"""


def build_email_html(run_summary, items, errors=None):
    """items: list of dicts -> group, label, role, url, diff, title"""
    groups = {}
    for it in items:
        groups.setdefault(it["group"], []).append(it)

    body = [
        '<div class="wrap"><div class="card">',
        '<div class="hd"><h1>Content Change Alert</h1>',
        f'<p>{html.escape(run_summary["timestamp"])} UTC &nbsp;·&nbsp; '
        f'{run_summary["schedule"]} check</p></div>',
        '<div class="stats">',
        f'<span class="stat"><b>{run_summary["checked"]}</b>pages checked</span>',
        f'<span class="stat"><b>{len(items)}</b>pages changed</span>',
        f'<span class="stat"><b>{len(groups)}</b>reports affected</span>',
        "</div>",
    ]

    for group, rows in groups.items():
        body.append(f'<div class="grp">{html.escape(group)}</div>')
        rows.sort(key=lambda r: (r["role"] != "own", r["label"]))
        for it in rows:
            badge = "b-own" if it["role"] == "own" else "b-comp"
            badge_txt = "Our page" if it["role"] == "own" else it["label"]
            d = it["diff"]
            body.append(
                f'<div class="pg"><span class="pct">{d["change_pct"]}% changed</span>'
                f'<span class="badge {badge}">{html.escape(badge_txt)}</span>'
                f'<h3>{html.escape(it["title"] or it["url"])}</h3>'
                f'<a class="url" href="{html.escape(it["url"])}">{html.escape(it["url"])}</a>'
                + render_diff_html(d)
                + "</div>"
            )

    if errors:
        body.append(
            '<div class="err"><b>Fetch issues this run</b> — these pages could not be '
            'checked, so a change on them would have been missed:<br>'
            + "<br>".join(html.escape(e) for e in errors[:12])
            + "</div>"
        )

    body.append(
        '<div class="ft">Competitor Content Watchdog · automated monitoring<br>'
        "Timestamps, counters and ad slots are filtered out to avoid false alerts."
        "</div></div></div>"
    )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>{CSS}</style></head><body>" + "".join(body) + "</body></html>"
    )


def build_plaintext(run_summary, items):
    out = [
        f"CONTENT CHANGE ALERT — {run_summary['timestamp']} UTC",
        f"{run_summary['checked']} checked, {len(items)} changed",
        "",
    ]
    for it in items:
        d = it["diff"]
        out.append(f"[{it['group']}] {it['label']} — {d['change_pct']}% changed")
        out.append(it["url"])
        for old, new in d["modified"][:6]:
            out.append(f"  WAS: {old[:200]}")
            out.append(f"  NOW: {new[:200]}")
        for a in d["added"][:6]:
            out.append(f"  + {a[:200]}")
        for r in d["removed"][:6]:
            out.append(f"  - {r[:200]}")
        out.append("")
    return "\n".join(out)


def send_email(subject, html_body, text_body, recipients=None):
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    recips = recipients or [
        r.strip() for r in os.environ.get("ALERT_RECIPIENTS", "").split(",") if r.strip()
    ]

    if not (user and pwd and recips):
        raise RuntimeError(
            "Email not configured. Need SMTP_USER, SMTP_PASSWORD, ALERT_RECIPIENTS."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recips)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host, port, timeout=45) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)

    return recips
