"""
The run loop. Called by GitHub Actions on schedule, or manually from the UI.
"""
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from . import db
from .differ import diff
from .extractor import extract
from .fetcher import fetch
from .notifier import build_email_html, build_plaintext, send_email

# Ignore changes below this % — set to 0.0 to catch literally every character.
DEFAULT_THRESHOLD = 0.0


def check_page(page):
    """Fetch + extract + compare a single page. Thread-safe."""
    result = {"page": page, "error": None, "diff": None,
              "extracted": None, "tier": None}
    try:
        html_doc, tier = fetch(page["url"], preferred_tier=page.get("preferred_tier"))
        ex = extract(html_doc, page.get("css_selector"))
        result["extracted"] = ex
        result["tier"] = tier

        if ex["word_count"] < 10:
            result["error"] = f"{page['url']}: page returned almost no text"
            return result

        prev = db.latest_snapshot(page["id"])
        if prev and prev["hash"] != ex["hash"]:
            result["diff"] = diff(prev["text"], ex["text"])
            result["prev_id"] = prev["id"]
        result["is_first"] = prev is None
    except Exception as e:
        result["error"] = f"{page['url']}: {type(e).__name__}: {e}"
    return result


def run(schedule_label="manual", send_mail=True, threshold=None,
        recipients=None, max_workers=4):
    db.init()
    threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    pages = db.get_pages(active_only=True)
    run_id = db.start_run()

    log, errors, changed_items, change_ids = [], [], [], []
    baselines = 0

    if not pages:
        db.finish_run(run_id, 0, 0, 0, "No active pages configured.")
        return {"checked": 0, "changed": 0, "errors": 0,
                "log": "No active pages configured.", "emailed": False}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check_page, p): p for p in pages}
        for fut in as_completed(futures):
            page = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                errors.append(f"{page['url']}: worker crashed: {e}")
                continue

            if res["error"]:
                errors.append(res["error"])
                log.append(f"ERROR  {page['label']} — {res['error']}")
                continue

            ex = res["extracted"]

            # Remember the tier that worked so next run skips failed attempts.
            if res["tier"] and res["tier"] != page.get("preferred_tier"):
                db.set_preferred_tier(page["id"], res["tier"])

            if res.get("is_first"):
                db.save_snapshot(page["id"], ex, res["tier"])
                baselines += 1
                log.append(f"BASE   {page['label']} — baseline saved "
                           f"({ex['word_count']} words, via {res['tier']})")
                continue

            d = res["diff"]
            if d and d["change_pct"] >= threshold:
                new_id = db.save_snapshot(page["id"], ex, res["tier"])
                cid = db.record_change(page["id"], res["prev_id"], new_id, d)
                change_ids.append(cid)
                db.prune_snapshots(page["id"])
                changed_items.append({
                    "group": page["group_name"],
                    "label": page["label"] or page["role"],
                    "role": page["role"],
                    "url": page["url"],
                    "title": ex["title"],
                    "diff": d,
                })
                log.append(f"CHANGE {page['label']} — {d['change_pct']}% "
                           f"(+{d['counts']['added']} −{d['counts']['removed']} "
                           f"~{d['counts']['modified']})")
            else:
                log.append(f"SAME   {page['label']} — no change")

    emailed = False
    if changed_items and send_mail:
        summary = {
            "timestamp": datetime.utcnow().strftime("%d %b %Y, %H:%M"),
            "schedule": schedule_label,
            "checked": len(pages),
        }
        groups = {i["group"] for i in changed_items}
        subject = (
            f"[Watchdog] {len(changed_items)} page"
            f"{'s' if len(changed_items) != 1 else ''} changed across "
            f"{len(groups)} report{'s' if len(groups) != 1 else ''}"
        )
        try:
            send_email(
                subject,
                build_email_html(summary, changed_items, errors),
                build_plaintext(summary, changed_items),
                recipients,
            )
            db.mark_notified(change_ids)
            emailed = True
            log.append(f"EMAIL  alert sent ({len(changed_items)} changes)")
        except Exception as e:
            errors.append(f"Email send failed: {e}")
            log.append(f"ERROR  email failed: {e}")

    log_text = "\n".join(log)
    db.finish_run(run_id, len(pages), len(changed_items), len(errors), log_text)

    return {
        "checked": len(pages),
        "changed": len(changed_items),
        "baselines": baselines,
        "errors": len(errors),
        "error_list": errors,
        "log": log_text,
        "emailed": emailed,
    }


if __name__ == "__main__":
    label = os.environ.get("SCHEDULE_LABEL", "scheduled")
    try:
        out = run(schedule_label=label, send_mail=True)
        print(f"Checked {out['checked']} | Changed {out['changed']} | "
              f"Baselines {out.get('baselines',0)} | Errors {out['errors']} | "
              f"Emailed {out['emailed']}")
        print(out["log"])
    except Exception:
        traceback.print_exc()
        raise
