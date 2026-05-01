"""
Playwright-based operator site pre-crawler for fact-checking.

Crawls operator websites and extracts structured content for
verification against article claims. Uses caching to avoid
re-crawling the same site multiple times per day.
"""

import asyncio
import hashlib
import json
import re
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Page, Browser


# -----------------------------------------------------------------------------
# Data Classes
# -----------------------------------------------------------------------------

@dataclass
class CrawledPage:
    """A single crawled page with extracted content."""
    url: str
    title: str
    content: str              # Cleaned text content
    page_type: str            # bonus_terms, payments, promotions, vip, rg, legal, faq, games, general
    content_tokens: int       # Approximate token count for budget management

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'CrawledPage':
        return cls(**data)


@dataclass
class CrawlResult:
    """Result of crawling an operator site."""
    domain: str
    pages: list = field(default_factory=list)  # List of CrawledPage
    total_tokens: int = 0
    crawl_timestamp: str = ""
    crawl_log: list = field(default_factory=list)   # URLs visited
    errors: list = field(default_factory=list)      # URLs that failed
    geo_locale: str = ""

    def to_dict(self) -> dict:
        return {
            'domain': self.domain,
            'pages': [p.to_dict() for p in self.pages],
            'total_tokens': self.total_tokens,
            'crawl_timestamp': self.crawl_timestamp,
            'crawl_log': self.crawl_log,
            'errors': self.errors,
            'geo_locale': self.geo_locale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'CrawlResult':
        pages = [CrawledPage.from_dict(p) for p in data.get('pages', [])]
        return cls(
            domain=data.get('domain', ''),
            pages=pages,
            total_tokens=data.get('total_tokens', 0),
            crawl_timestamp=data.get('crawl_timestamp', ''),
            crawl_log=data.get('crawl_log', []),
            errors=data.get('errors', []),
            geo_locale=data.get('geo_locale', ''),
        )


# -----------------------------------------------------------------------------
# Fact-Check Categories (from Sheldon)
# -----------------------------------------------------------------------------

FACT_CHECK_CATEGORIES = {
    1: "Welcome Bonus & Promotions",
    2: "VIP/Loyalty Program",
    3: "Payment Methods",
    4: "Wagering Requirements & Bonus Terms",
    5: "Game Providers & Software",
    6: "Game Selection & Categories",
    7: "Mobile Compatibility",
    8: "Customer Support",
    9: "Licensing & Security",
    10: "Responsible Gambling",
    11: "Country/Currency Availability",
    12: "Withdrawal Limits & Processing Times",
    13: "General Brand Information",
}


# -----------------------------------------------------------------------------
# Priority Crawl Paths
# -----------------------------------------------------------------------------

# Ordered by authority (legal/terms first, then features)
PRIORITY_PATHS = [
    # Legal & Terms (highest authority)
    ("/bonus-terms", "bonus_terms"),
    ("/bonusterms", "bonus_terms"),
    ("/terms-and-conditions", "legal"),
    ("/terms", "legal"),
    ("/terms-conditions", "legal"),
    ("/privacy-policy", "legal"),
    ("/privacy", "legal"),

    # Payments
    ("/payments", "payments"),
    ("/banking", "payments"),
    ("/deposit", "payments"),
    ("/withdrawal", "payments"),
    ("/payment-methods", "payments"),

    # Promotions
    ("/promotions", "promotions"),
    ("/bonuses", "promotions"),
    ("/offers", "promotions"),
    ("/welcome-bonus", "promotions"),

    # VIP/Loyalty
    ("/vip", "vip"),
    ("/loyalty", "vip"),
    ("/rewards", "vip"),
    ("/vip-program", "vip"),

    # Responsible Gambling
    ("/responsible-gaming", "rg"),
    ("/responsible-gambling", "rg"),
    ("/responsible-play", "rg"),
    ("/safe-gambling", "rg"),

    # Support
    ("/support", "faq"),
    ("/faq", "faq"),
    ("/help", "faq"),
    ("/contact", "faq"),

    # Games
    ("/providers", "games"),
    ("/games", "games"),
    ("/slots", "games"),
    ("/live-casino", "games"),
    ("/table-games", "games"),

    # About
    ("/about", "general"),
    ("/about-us", "general"),
]


# -----------------------------------------------------------------------------
# Cache Configuration
# -----------------------------------------------------------------------------

CACHE_DIR = Path.home() / ".pitboss" / "crawl_cache"
CACHE_TTL_HOURS = 24


def get_cache_path(domain: str, geo_locale: str = "") -> Path:
    """Get cache file path for a domain + date combination."""
    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"{domain}_{geo_locale}_{today}" if geo_locale else f"{domain}_{today}"
    cache_hash = hashlib.md5(cache_key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{cache_hash}.json"


def get_cached_crawl(domain: str, geo_locale: str = "") -> Optional[CrawlResult]:
    """Load cached crawl result if available and not expired."""
    cache_path = get_cache_path(domain, geo_locale)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Check TTL
        timestamp = datetime.fromisoformat(data.get('crawl_timestamp', ''))
        if datetime.now() - timestamp > timedelta(hours=CACHE_TTL_HOURS):
            cache_path.unlink()  # Delete expired cache
            return None

        return CrawlResult.from_dict(data)
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def save_crawl_cache(result: CrawlResult) -> None:
    """Save crawl result to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = get_cache_path(result.domain, result.geo_locale)

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Content Extraction Helpers
# -----------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimation (words * 1.3)."""
    return int(len(text.split()) * 1.3)


def clean_text(text: str) -> str:
    """Clean extracted text: normalize whitespace, remove artifacts."""
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove common navigation artifacts
    text = re.sub(r'(Skip to content|Back to top|Cookie settings)', '', text, flags=re.IGNORECASE)
    # Remove excessive punctuation runs
    text = re.sub(r'[•·|]{2,}', ' ', text)
    return text.strip()


def classify_page_type(url: str, title: str, content: str) -> str:
    """Classify page type based on URL, title, and content."""
    url_lower = url.lower()
    title_lower = title.lower()
    content_lower = content[:500].lower()  # First 500 chars

    # Check URL patterns
    for path, page_type in PRIORITY_PATHS:
        if path.strip('/') in url_lower:
            return page_type

    # Check title/content keywords
    if any(kw in title_lower for kw in ['terms', 'conditions', 't&c', 'legal']):
        return 'legal'
    if any(kw in title_lower for kw in ['bonus terms', 'wagering', 'bonus rules']):
        return 'bonus_terms'
    if any(kw in title_lower for kw in ['payment', 'deposit', 'withdraw', 'banking']):
        return 'payments'
    if any(kw in title_lower for kw in ['promotion', 'bonus', 'welcome', 'offer']):
        return 'promotions'
    if any(kw in title_lower for kw in ['vip', 'loyalty', 'reward']):
        return 'vip'
    if any(kw in title_lower for kw in ['responsible', 'safe gambling', 'gamble aware']):
        return 'rg'
    if any(kw in title_lower for kw in ['faq', 'help', 'support', 'contact']):
        return 'faq'
    if any(kw in title_lower for kw in ['games', 'slots', 'casino', 'provider']):
        return 'games'

    return 'general'


# -----------------------------------------------------------------------------
# Playwright Crawler
# -----------------------------------------------------------------------------

async def dismiss_popups(page: Page) -> None:
    """Try to dismiss cookie banners and popups."""
    popup_selectors = [
        # Cookie banners
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("Accept Cookies")',
        'button:has-text("I Accept")',
        'button:has-text("Got it")',
        'button:has-text("OK")',
        'button:has-text("Agree")',
        '[id*="cookie"] button',
        '[class*="cookie"] button',
        '[id*="consent"] button',
        '[class*="consent"] button',
        # Age verification
        'button:has-text("I am 18")',
        'button:has-text("I\'m 18")',
        'button:has-text("Enter")',
        'button:has-text("Yes")',
        '[class*="age-gate"] button',
        '[id*="age-verification"] button',
    ]

    for selector in popup_selectors:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=500):
                await button.click(timeout=1000)
                await page.wait_for_timeout(300)
        except Exception:
            pass


async def expand_accordions(page: Page) -> None:
    """Expand accordions and 'show more' sections."""
    expand_selectors = [
        'button:has-text("Show More")',
        'button:has-text("Read More")',
        'button:has-text("See More")',
        'button:has-text("View All")',
        'button:has-text("Expand")',
        '[class*="accordion"] button',
        '[class*="expand"] button',
        '[class*="collapse"] button',
        'details summary',
    ]

    for selector in expand_selectors:
        try:
            buttons = page.locator(selector)
            count = await buttons.count()
            for i in range(min(count, 10)):  # Limit to 10 expansions
                try:
                    btn = buttons.nth(i)
                    if await btn.is_visible(timeout=300):
                        await btn.click(timeout=500)
                        await page.wait_for_timeout(200)
                except Exception:
                    pass
        except Exception:
            pass


async def extract_page_content(page: Page) -> tuple[str, str]:
    """Extract title and main content from page."""
    title = await page.title()

    # Try to get main content area first
    main_selectors = [
        'main',
        '[role="main"]',
        'article',
        '.content',
        '#content',
        '.main-content',
        '#main-content',
    ]

    content = ""
    for selector in main_selectors:
        try:
            elem = page.locator(selector).first
            if await elem.is_visible(timeout=500):
                content = await elem.inner_text(timeout=2000)
                break
        except Exception:
            pass

    # Fallback to body if no main content found
    if not content:
        try:
            content = await page.locator('body').inner_text(timeout=3000)
        except Exception:
            content = ""

    return title, clean_text(content)


def is_valid_locale(locale: str) -> bool:
    """Check if locale is a valid BCP-47 code (e.g., en-US, en-CA)."""
    if not locale or len(locale) > 10:
        return False
    # Valid locales contain hyphen or underscore: en-US, en_CA, etc.
    return bool(re.match(r'^[a-zA-Z]{2}[-_][a-zA-Z]{2}$', locale))


async def crawl_single_page(
    browser: Browser,
    url: str,
    geo_locale: str = ""
) -> Optional[CrawledPage]:
    """Crawl a single page and extract content."""
    # Only use geo_locale for Playwright if it's a valid BCP-47 code
    # Invalid values like "International" fall back to en-US
    playwright_locale = geo_locale if is_valid_locale(geo_locale) else "en-US"

    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 720},
        locale=playwright_locale,
    )

    try:
        page = await context.new_page()

        # Navigate with timeout
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)

        if not response or response.status >= 400:
            status = response.status if response else "no response"
            print(f"[CRAWL ERROR] {url}: HTTP {status}")
            return None

        # Wait for content to stabilize
        await page.wait_for_timeout(1000)

        # Dismiss popups
        await dismiss_popups(page)

        # Expand accordions
        await expand_accordions(page)

        # Extract content
        title, content = await extract_page_content(page)

        if not content or len(content) < 100:
            print(f"[CRAWL ERROR] {url}: Empty or insufficient content ({len(content) if content else 0} chars)")
            return None

        page_type = classify_page_type(url, title, content)
        tokens = estimate_tokens(content)

        return CrawledPage(
            url=url,
            title=title,
            content=content,
            page_type=page_type,
            content_tokens=tokens,
        )

    except Exception as e:
        print(f"[CRAWL ERROR] {url}: {type(e).__name__}: {e}")
        return None

    finally:
        await context.close()


async def crawl_operator_site(
    root_url: str,
    max_pages: int = 20,
    max_total_tokens: int = 50000,
    geo_locale: str = "",
) -> CrawlResult:
    """
    Crawl an operator site starting from root_url.

    Strategy:
    1. Hit the root URL to verify the site is accessible
    2. Visit priority paths that exist on this domain
    3. If geo_locale is set, prefer locale-prefixed paths
    4. For each page: expand accordions, extract clean text
    5. Stop when max_pages or max_total_tokens is reached
    6. Return structured content grouped by page_type

    Args:
        root_url: Root URL of the operator site (e.g., https://20bet.com/)
        max_pages: Maximum number of pages to crawl
        max_total_tokens: Token budget to stay within
        geo_locale: Optional locale code (e.g., "en-CA")

    Returns:
        CrawlResult with all crawled pages
    """
    parsed = urlparse(root_url)
    domain = parsed.netloc
    base_url = f"{parsed.scheme}://{domain}"

    result = CrawlResult(
        domain=domain,
        crawl_timestamp=datetime.now().isoformat(),
        geo_locale=geo_locale,
    )

    crawled_urls = set()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            print("[CRAWL] Browser launched successfully")

            try:
                # Build URL list: root + priority paths
                urls_to_try = [root_url]

                for path, _ in PRIORITY_PATHS:
                    urls_to_try.append(urljoin(base_url, path))

                # Crawl pages
                for url in urls_to_try:
                    if url in crawled_urls:
                        continue

                    if len(result.pages) >= max_pages:
                        break

                    if result.total_tokens >= max_total_tokens:
                        break

                    page = await crawl_single_page(browser, url, geo_locale)

                    crawled_urls.add(url)
                    result.crawl_log.append(url)

                    if page:
                        result.pages.append(page)
                        result.total_tokens += page.content_tokens
                    else:
                        result.errors.append(url)

            finally:
                await browser.close()

    except Exception as e:
        print(f"[CRAWL ERROR] Playwright/browser failure: {type(e).__name__}: {e}")
        result.errors.append(f"BROWSER_FAILURE: {e}")

    return result


# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

def get_or_crawl(
    root_url: str,
    geo_locale: str = "",
    max_pages: int = 20,
    max_total_tokens: int = 50000,
) -> CrawlResult:
    """
    Get cached crawl result or perform new crawl.

    This is the main entry point for the fact-checker.
    Uses a separate thread with its own event loop for Playwright compatibility.

    Args:
        root_url: Root URL of the operator site
        geo_locale: Optional locale code
        max_pages: Maximum pages to crawl
        max_total_tokens: Token budget

    Returns:
        CrawlResult (from cache or fresh crawl)
    """
    parsed = urlparse(root_url)
    domain = parsed.netloc

    # Check cache
    cached = get_cached_crawl(domain, geo_locale)
    if cached:
        return cached

    # Run async crawl in separate thread with dedicated event loop
    result = [None]
    exception_holder = [None]
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result[0] = loop.run_until_complete(crawl_operator_site(
                root_url=root_url,
                max_pages=max_pages,
                max_total_tokens=max_total_tokens,
                geo_locale=geo_locale,
            ))
        except Exception as e:
            print(f"[CRAWL ERROR] Thread exception: {type(e).__name__}: {e}")
            exception_holder[0] = e
        finally:
            loop.close()

    t = threading.Thread(target=_run)
    t.start()
    t.join()

    # Save to cache
    if result[0] and result[0].pages:
        save_crawl_cache(result[0])

    return result[0]
