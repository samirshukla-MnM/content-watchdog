"""End-to-end verification without hitting the network."""
import os, sys
os.environ["WATCHDOG_DB"] = "/tmp/test.db"
if os.path.exists("/tmp/test.db"): os.remove("/tmp/test.db")

from core import db
from core.extractor import extract
from core.differ import diff
from core.notifier import build_email_html, build_plaintext

V1 = """<!DOCTYPE html><html><head><title>Global EV Battery Market Report</title>
<meta name="description" content="EV battery market analysis"></head><body>
<nav><a href="/x">Home</a></nav>
<div class="cookie-banner">We use cookies</div>
<div class="advert">Buy now! Random ad #48812</div>
<main>
<h1>Global EV Battery Market Report</h1>
<p>The market was valued at USD 45.2 billion in 2024.</p>
<p>Expected CAGR of 18.4% through 2030.</p>
<h2>Key Players</h2>
<li>CATL</li><li>LG Energy Solution</li><li>Panasonic</li>
<p>Report published 14 March 2025. Last updated 3 minutes ago.</p>
<p>Page views: 1,204,551 views</p>
</main>
<script>var token="a3f9c2b81d4e7f60a3f9c2b81d4e7f60";</script>
<footer>Copyright 2025</footer></body></html>"""

# V2: ad changed, timestamp changed, view count changed (should be IGNORED)
# but price, CAGR and a player changed (should be CAUGHT)
V2 = """<!DOCTYPE html><html><head><title>Global EV Battery Market Report</title>
<meta name="description" content="EV battery market analysis"></head><body>
<nav><a href="/x">Home</a></nav>
<div class="cookie-banner">We use cookies</div>
<div class="advert">Buy now! Random ad #99327</div>
<main>
<h1>Global EV Battery Market Report</h1>
<p>The market was valued at USD 52.8 billion in 2025.</p>
<p>Expected CAGR of 21.7% through 2030.</p>
<h2>Key Players</h2>
<li>CATL</li><li>LG Energy Solution</li><li>Panasonic</li><li>BYD</li>
<p>Report published 14 March 2025. Last updated 9 minutes ago.</p>
<p>Page views: 1,209,882 views</p>
</main>
<script>var token="ff11bb22cc33dd44ff11bb22cc33dd44";</script>
<footer>Copyright 2025</footer></body></html>"""

print("=== TEST 1: noise filtering (identical content, different noise) ===")
NOISY = V1.replace("#48812", "#77777").replace("3 minutes ago", "51 minutes ago") \
          .replace("1,204,551", "1,999,999").replace("a3f9c2b81d4e7f60a3f9c2b81d4e7f60","zz")
a, b = extract(V1), extract(NOISY)
print(f"  hash match: {a['hash'] == b['hash']}  <- must be True")
assert a["hash"] == b["hash"], "FAIL: noise leaked into hash"

print("\n=== TEST 2: real change detection ===")
e1, e2 = extract(V1), extract(V2)
print(f"  hash differs: {e1['hash'] != e2['hash']}  <- must be True")
assert e1["hash"] != e2["hash"]
d = diff(e1["text"], e2["text"])
print(f"  changed={d['changed']}  pct={d['change_pct']}%")
print(f"  counts: {d['counts']}")
for o, n in d["modified"]:
    print(f"    WAS: {o}")
    print(f"    NOW: {n}")
for x in d["added"]:
    print(f"    +  : {x}")
assert d["changed"] and d["counts"]["modified"] >= 2 and d["counts"]["added"] >= 1

print("\n=== TEST 3: SPA / Cloudflare detection ===")
from core.fetcher import _looks_blocked, _looks_empty_spa
assert _looks_blocked("<html><title>Just a moment...</title></html>")
assert _looks_empty_spa('<html><body><div id="root"></div></body></html>')
assert not _looks_blocked(V1) and not _looks_empty_spa(V1)
print("  cloudflare + spa markers detected correctly")

print("\n=== TEST 4: database round-trip ===")
db.init()
db.upsert_page("EV Battery", "own", "Our page", "https://example.com/ev")
db.upsert_page("EV Battery", "competitor", "Competitor 1", "https://comp-a.com/ev")
pages = db.get_pages()
p = pages[0]
s1 = db.save_snapshot(p["id"], e1, "requests")
back = db.latest_snapshot(p["id"])
assert back["text"] == e1["text"], "FAIL: compression round-trip broken"
s2 = db.save_snapshot(p["id"], e2, "requests")
cid = db.record_change(p["id"], s1, s2, d)
assert len(db.recent_changes()) == 1
det = db.change_detail(cid)
assert det["detail"]["modified"]
print(f"  {len(pages)} pages, snapshot round-trip OK, change #{cid} stored")

print("\n=== TEST 5: excel import auto-detection ===")
import pandas as pd
from core import importer
df = importer.sample_template()
nc, oc, cc = importer.analyze(df)
print(f"  name='{nc}'  own='{oc}'  competitors={cc}")
assert nc == "Report Name" and oc == "Our URL" and len(cc) == 3
n, sk = importer.import_df(df, nc, oc, cc, replace=True)
print(f"  imported {n} urls (expect 12), skipped {sk}")
assert n == 12

print("\n=== TEST 6: email rendering ===")
items = [{"group":"EV Battery","label":"Competitor 1","role":"competitor",
          "url":"https://comp-a.com/ev","title":"EV Battery Report","diff":d}]
summary = {"timestamp":"24 Jul 2026, 03:00","schedule":"daily","checked":12}
html_out = build_email_html(summary, items, ["https://x.com: timeout"])
txt = build_plaintext(summary, items)
open("/tmp/preview.html","w").write(html_out)
assert "class=\"ins\"" in html_out and "class=\"del\"" in html_out
assert "52.8" in html_out and "45.2" in html_out
print(f"  html {len(html_out)} chars, word-level ins/del present")
print(f"  text preview:\n    " + "\n    ".join(txt.splitlines()[:7]))

print("\nALL TESTS PASSED")
