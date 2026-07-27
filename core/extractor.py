"""
Turns raw HTML into stable, comparable text.

This module is the difference between a useful monitor and one that emails you
every single morning. Raw HTML changes constantly for reasons that have nothing
to do with the content: rotating ad slots, CSRF tokens, cache-busting query
strings, "Last updated 3 minutes ago", session IDs, build hashes. We strip all
of it before hashing or diffing.
"""
import hashlib
import re

from bs4 import BeautifulSoup, Comment

# Structural / non-content elements
DROP_TAGS = [
    "script", "style", "noscript", "iframe", "svg", "canvas", "template",
    "nav", "footer", "header", "aside", "form", "button", "video", "audio",
]

# Elements whose class/id smells like chrome rather than content
# Matched against whole class/id *tokens*, not as loose substrings. A substring
# match is dangerous: "share" appears inside GitHub's README wrapper class and
# silently deletes the entire article, which would make the monitor report a
# huge phantom change. Tokens are split on -, _ and camelCase boundaries.
NOISE_TOKENS = {
    "cookie", "cookies", "consent", "gdpr", "banner", "advert", "advertisement",
    "ad", "ads", "adslot", "sponsor", "sponsored", "promo", "promotion",
    "popup", "modal", "overlay", "newsletter", "subscribe", "signup",
    "social", "share", "sharing", "comment", "comments", "disqus",
    "sidebar", "breadcrumb", "breadcrumbs", "related", "recommended",
    "trending", "carousel", "chat", "chatbot", "widget", "toolbar",
    "skiplink", "backtotop", "pagination", "pager", "tracking", "analytics",
    "gtm", "recaptcha", "captcha", "cta", "paywall", "notification",
    "notifications", "menu", "navbar", "navigation",
}

_TOKEN_SPLIT = re.compile(r"[^a-zA-Z0-9]+|(?<=[a-z])(?=[A-Z])")


def _is_noise_attr(value) -> bool:
    """True if any whole token in a class/id list is a known chrome token."""
    if not value:
        return False
    if isinstance(value, (list, tuple)):
        value = " ".join(value)
    tokens = {t.lower() for t in _TOKEN_SPLIT.split(str(value)) if t}
    return bool(tokens & NOISE_TOKENS)

# Where the real content usually lives, best guess first
CONTENT_SELECTORS = [
    "article", "main", '[role="main"]', "#content", ".content",
    "#main-content", ".main-content", ".post-content", ".entry-content",
    ".article-body", ".report-content", "#report", ".page-content",
]

# Volatile substrings normalized away before comparison
VOLATILE_SUBS = [
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(am|pm)?\b", re.I), "«TIME»"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}T[\d:.+Z-]+\b"), "«TIMESTAMP»"),
    (re.compile(r"\b(updated|published|posted|modified|as of|last reviewed)"
                r"[:\s]+[^.\n]{0,45}\b", re.I), "«UPDATED»"),
    (re.compile(r"\b\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago\b", re.I),
     "«RELTIME»"),
    (re.compile(r"\b[0-9a-f]{16,64}\b", re.I), "«HASH»"),
    (re.compile(r"\b\d{1,3}(,\d{3})+\s*(views?|reads?|downloads?|shares?)\b", re.I),
     "«COUNTER»"),
    (re.compile(r"©\s*\d{4}"), "«COPYRIGHT»"),
    (re.compile(r"[?&](utm_[a-z]+|sid|sessionid|_t|cb|v|ver|rev)=[^\s&\"']+", re.I), ""),
]


def _text_len(node) -> int:
    return len(node.get_text(" ", strip=True))


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup(DROP_TAGS):
        tag.decompose()

    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    body = soup.body or soup
    page_text = _text_len(body)

    for el in body.find_all(True):
        if el.decomposed:
            continue
        if not (_is_noise_attr(el.get("class")) or _is_noise_attr(el.get("id"))):
            continue
        # Never delete a block that holds a large share of the page. Real chrome
        # (cookie bars, share buttons, nav) is small; if a "share"-classed
        # wrapper contains half the page, it is the content wrapper.
        if page_text and _text_len(el) > 0.30 * page_text:
            continue
        el.decompose()

    for el in body.find_all(attrs={"aria-hidden": "true"}):
        if not el.decomposed and _text_len(el) <= 0.30 * page_text:
            el.decompose()

    for el in body.select('[style*="display:none"], [style*="display: none"], [hidden]'):
        if not el.decomposed:
            el.decompose()


def _pick_main(soup: BeautifulSoup):
    """
    Choose the container holding the actual article body.

    Named selectors are only trusted if they hold a real share of the page's
    text — many sites put <main> or .content around a sidebar or a nav shell,
    which is how you end up monitoring 13 words of "Notifications" chrome and
    getting an alert every time a counter ticks. We therefore score every
    candidate by paragraph-text density and compare against the whole body
    before committing.
    """
    body = soup.body or soup
    total = max(_text_len(body), 1)

    def score(node):
        # Text inside real content tags, not link/menu soup.
        content = sum(
            _text_len(el)
            for el in node.find_all(["p", "li", "td", "h1", "h2", "h3", "h4",
                                     "blockquote", "pre", "dd"])
        )
        links = sum(_text_len(a) for a in node.find_all("a"))
        # Penalise link-dense blocks (navs, footers, related-post lists)
        return content - 0.5 * links

    candidates = []
    for sel in CONTENT_SELECTORS:
        for node in soup.select(sel):
            candidates.append(node)

    for div in body.find_all(["div", "section", "article"], recursive=True):
        if _text_len(div) > 400:
            candidates.append(div)

    best, best_score = body, score(body)
    for node in candidates:
        s = score(node)
        # Require the candidate to hold a meaningful slice of the page, so we
        # never trade the full body for a small chrome container.
        if s > best_score and _text_len(node) >= 0.25 * total:
            best, best_score = node, s

    return best if best_score > 0 else body


def normalize(text: str) -> str:
    for pattern, repl in VOLATILE_SUBS:
        text = pattern.sub(repl, text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract(html: str, css_selector: str = None) -> dict:
    """
    Returns dict with: title, text (normalized, line-per-block), hash, headings, links.
    css_selector overrides auto-detection when the user pins a region in the UI.
    """
    soup = BeautifulSoup(html, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else ""
    meta_desc = ""
    md = soup.find("meta", attrs={"name": "description"})
    if md and md.get("content"):
        meta_desc = md["content"].strip()

    _strip_noise(soup)

    if css_selector:
        root = soup.select_one(css_selector) or _pick_main(soup)
    else:
        root = _pick_main(soup)

    headings = [
        f"{h.name.upper()}: {h.get_text(' ', strip=True)}"
        for h in root.find_all(["h1", "h2", "h3", "h4"])
        if h.get_text(strip=True)
    ]

    links = sorted({
        a.get_text(" ", strip=True)[:80] + " → " + a["href"]
        for a in root.find_all("a", href=True)
        if a.get_text(strip=True) and not a["href"].startswith(("#", "javascript:"))
    })

    # One logical block per line -> diffs land on meaningful units, not
    # arbitrary wrap points.
    blocks = []
    for el in root.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th",
         "dt", "dd", "blockquote", "pre", "figcaption"]
    ):
        t = el.get_text(" ", strip=True)
        if t and len(t) > 1:
            blocks.append(t)

    if not blocks:
        blocks = [
            ln.strip() for ln in root.get_text("\n").splitlines() if ln.strip()
        ]

    # De-dupe consecutive repeats (common in sloppy templates)
    deduped = [b for i, b in enumerate(blocks) if i == 0 or b != blocks[i - 1]]

    text = normalize("\n".join(deduped))

    return {
        "title": title,
        "meta_description": meta_desc,
        "text": text,
        "headings": headings,
        "links": links[:400],
        "word_count": len(text.split()),
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
