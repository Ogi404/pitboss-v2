"""
Base classes for check modules.

All check modules extend CheckModule and return CheckResult objects.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, List


@dataclass
class WriterComment:
    """
    A comment to be placed in the Google Doc for the writer.

    Phase 6: Writer Comments System - these are editorial notes that require
    writer action, as opposed to auto-corrections that can be applied directly.
    """
    id: str                              # Unique ID for this comment
    check_name: str                      # Which check module generated this
    severity: str                        # "error", "warning", "suggestion"
    anchor_text: str                     # The text this comment refers to
    anchor_start: Optional[int] = None   # Document index (for Google Docs API)
    anchor_end: Optional[int] = None     # Document index
    comment_text: str = ""               # The actual comment content
    context: str = ""                    # Brief excerpt or rule that justifies the comment

    # For the review UI
    approved: bool = False               # User must approve before applying
    edited_text: Optional[str] = None    # User can edit before applying


@dataclass
class CheckResult:
    """Standard output from any check module."""
    name: str                           # e.g. "proofread", "keywords", "seo_structure"
    status: str                         # "pass", "warn", "fail", "error"
    summary: str                        # One-line human summary
    details: Any                        # Module-specific structured data (dict/list)
    corrections: Optional[List] = None  # Optional: list of corrections for Google Docs
    comments: Optional[List] = None     # Optional: list of WriterComment objects (Phase 6)
    score: Optional[float] = None       # Optional: 0-100 score for this check


class CheckModule:
    """
    Base class for all check modules.

    Subclasses must:
    - Set `name` class attribute
    - Set `model_tier` if using LLM ("full", "mini", or "none")
    - Implement `run()` method
    """
    name: str = "unnamed"
    requires_llm: bool = False
    requires_brief: bool = True
    model_tier: str = "none"  # "full" = GPT-4.1, "mini" = GPT-4.1-mini, "none" = Python only

    def __init__(self, client=None, model=None):
        """
        Initialize the check module.

        Args:
            client: OpenAI client instance (required if requires_llm=True)
            model: Model string to use (e.g. "gpt-4.1"). If not provided,
                   orchestrator should pass the appropriate model based on model_tier.
        """
        self.client = client
        self.model = model

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Execute the check and return results.

        Args:
            article_text: The full article text to check
            brief_text: The brief/task description text (optional for some checks)
            **kwargs: Additional arguments (e.g. english_variant, position_map)

        Returns:
            CheckResult with check findings
        """
        raise NotImplementedError("Subclasses must implement run()")
