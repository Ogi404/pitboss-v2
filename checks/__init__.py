"""
Check modules package.

Provides modular checks for article proofreading and SEO analysis.
Each check module extends CheckModule and returns CheckResult objects.
"""

from .base import CheckModule, CheckResult, WriterComment
from .proofread import ProofreadCheck
from .keywords import KeywordCheck, count_keyword_occurrences, build_keyword_report
from .seo_structure import SEOStructureCheck
from .compliance import ComplianceCheck
from .formatting import FormattingCheck
from .readability import ReadabilityCheck
from .word_counts import WordCountCheck
from .consistency import ConsistencyCheck
from .style import StyleCheck
from .fact_check import FactCheckModule

__all__ = [
    # Base classes
    'CheckModule',
    'CheckResult',
    'WriterComment',
    # LLM check modules (full tier)
    'ProofreadCheck',
    'SEOStructureCheck',
    'FactCheckModule',
    # LLM check modules (mini tier)
    'KeywordCheck',
    'ConsistencyCheck',
    'StyleCheck',
    # Python-only check modules (Phase 2)
    'ComplianceCheck',
    'FormattingCheck',
    'ReadabilityCheck',
    'WordCountCheck',
    # Utility functions (for backwards compatibility during refactor)
    'count_keyword_occurrences',
    'build_keyword_report',
]
