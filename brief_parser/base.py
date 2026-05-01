"""
Base dataclasses for brief parsing.

Provides structured representations of brief data extracted from
Excel sheets or DOCX documents.
"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class KeywordSpec:
    """A single keyword specification from the brief."""
    keyword: str
    group: str                          # "main", "support", "lsi"
    required_min: Optional[int] = None
    required_max: Optional[int] = None
    source: str = "parsed"              # "parsed" or "llm_fallback"

    def to_dict(self) -> dict:
        """Convert to dict format expected by build_keyword_report()."""
        return {
            "keyword": self.keyword,
            "required_min": self.required_min,
            "required_max": self.required_max,
        }


@dataclass
class BriefData:
    """
    Structured data extracted from a task brief.

    Contains keywords parsed from the brief, plus the raw text
    for checks that need to analyze the full brief content.
    """
    keywords: List[KeywordSpec] = field(default_factory=list)
    raw_text: str = ""                  # Full brief text for SEO check
    parse_method: str = "none"          # "excel", "docx", "llm_fallback"
    parse_warnings: List[str] = field(default_factory=list)

    def get_keywords_by_group(self) -> dict:
        """
        Return keywords organized by group for compatibility with
        existing keyword checking code.

        Returns:
            Dict with "main", "support", "lsi" lists
        """
        result = {"main": [], "support": [], "lsi": []}
        for kw in self.keywords:
            group = kw.group if kw.group in result else "support"
            result[group].append(kw.to_dict())
        return result

    @property
    def has_keywords(self) -> bool:
        """Check if any keywords were parsed."""
        return len(self.keywords) > 0

    @property
    def used_llm_fallback(self) -> bool:
        """Check if LLM fallback was used for keyword extraction."""
        return self.parse_method == "llm_fallback"
