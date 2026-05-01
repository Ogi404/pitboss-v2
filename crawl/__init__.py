"""
Crawl package for operator site pre-crawling.

Provides Playwright-based crawling with caching for fact-checking
operator websites against article claims.
"""

from .site_crawler import (
    CrawledPage,
    CrawlResult,
    crawl_operator_site,
    get_or_crawl,
    FACT_CHECK_CATEGORIES,
)

__all__ = [
    'CrawledPage',
    'CrawlResult',
    'crawl_operator_site',
    'get_or_crawl',
    'FACT_CHECK_CATEGORIES',
]
