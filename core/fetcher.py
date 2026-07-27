"""
Three-tier fetch escalation:
  1. requests        - fast, static HTML
  2. Playwright      - renders React/Vue/Angular SPAs
  3. Playwright+stealth - passes Cloudflare "Checking your browser" interstitials

Escalates automatically. Records which tier succeeded so subsequent runs
start at the tier that worked last time (cached in the DB).
"""
import asyncio
import random
import re
import time

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Markers that mean "we got the challenge page, not the real page"
CF_MARKERS = [
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "ddos protection by cloudflare",
]

# Markers that mean "this is an empty SPA shell, JS hasn't run"
SPA_MARKERS = [
    'id="root"></div>',
    'id="app"></div>',
    'id="__next"></div>',
    "you need to enable javascript to run this app",
]


def _looks_blocked(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in CF_MARKERS)


def _looks_empty_spa(html: str) -> bool:
    low = html.lower()
    if any(m in low for m in SPA_MARKERS):
        return True

    # Measure *visible text*, not markup length. A shell page can ship 200KB of
    # inlined JS bundle while rendering nothing; a lean static page can be 1KB
    # of real content. Only the text tells them apart.
    stripped = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", stripped)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text) < 200


def fetch_requests(url: str, timeout: int = 30):
    """Tier 1 - plain HTTP. Handles the majority of sites."""
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    html = r.text
    if _looks_blocked(html):
        raise RuntimeError("cloudflare_challenge")
    if _looks_empty_spa(html):
        raise RuntimeError("spa_shell")
    return html


async def _playwright_fetch(url: str, stealth: bool, timeout: int):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )

        if stealth:
            # Strip the automation tells Cloudflare fingerprints on.
            await ctx.add_init_script(
                """
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
                window.chrome = {runtime:{}, loadTimes:function(){}, csi:function(){}};
                const q = window.navigator.permissions.query;
                window.navigator.permissions.query = (p) => (
                    p.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : q(p)
                );
                Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>8});
                Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
                """
            )

        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            if stealth:
                # Wait out the Cloudflare interstitial (it self-redirects).
                for _ in range(20):
                    html = await page.content()
                    if not _looks_blocked(html):
                        break
                    await asyncio.sleep(1.5)
                # Light human-ish signals
                await page.mouse.move(random.randint(200, 900), random.randint(150, 600))
                await asyncio.sleep(0.4)

            # Let client-side rendering settle.
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # Trigger lazy-loaded / below-fold content.
            await page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)"
            )
            await asyncio.sleep(1.2)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.5)

            html = await page.content()
            if _looks_blocked(html):
                raise RuntimeError("cloudflare_challenge")
            return html
        finally:
            await ctx.close()
            await browser.close()


def fetch_playwright(url: str, stealth: bool = False, timeout: int = 45):
    """Tier 2 / 3 - real browser. stealth=True adds anti-bot evasion."""
    return asyncio.run(_playwright_fetch(url, stealth, timeout))


def fetch(url: str, preferred_tier: str = None, timeout: int = 45):
    """
    Returns (html, tier_used).
    preferred_tier: start here instead of tier 1 (saves time on known-hard sites).
    """
    tiers = ["requests", "playwright", "stealth"]
    start = tiers.index(preferred_tier) if preferred_tier in tiers else 0

    last_err = None
    for tier in tiers[start:]:
        try:
            if tier == "requests":
                return fetch_requests(url, timeout=min(timeout, 30)), tier
            if tier == "playwright":
                return fetch_playwright(url, stealth=False, timeout=timeout), tier
            return fetch_playwright(url, stealth=True, timeout=timeout), tier
        except Exception as e:
            last_err = e
            time.sleep(1)
            continue

    raise RuntimeError(f"All fetch tiers failed for {url}: {last_err}")
