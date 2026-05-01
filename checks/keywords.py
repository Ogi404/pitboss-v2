"""
Keyword extraction and counting check module.

Uses structured brief parsing when available, with LLM as fallback
for unrecognized brief formats.
"""

import re
import json
from typing import Union
from .base import CheckModule, CheckResult, WriterComment
from brief_parser import BriefData


KEYWORDS_SYSTEM_PROMPT = """
You are a careful parser of iGaming task briefs.

Goal: extract ONLY the explicit keyword lists from the brief and return them in JSON.
Do NOT invent or drop keywords. If a keyword appears in a list or table, it must appear in the JSON.

Look for sections labelled things like:
- "Primary keywords", "Primary:", "Main keywords"
- "Secondary keywords", "Support keywords"
- "LSI keywords", "LSI terms", "Single-word LSI keys"
- Tables with columns like "Keyword | Volume | How much to use in the text"

Rules:
- Each row or bullet becomes ONE keyword entry.
- Use the keyword text exactly as written (keep spaces and order, ignore casing).
- If a table has columns like "Keyword  Volume  How much to use in the text":
    - Take the keyword from the first column.
    - Parse the last numeric column into:
        - required_min and required_max:
            - If it is a single integer (e.g. "3") -> min = max = 3
            - If it is a range like "0-10" -> min = 0, max = 10
    - Ignore the "Volume" column (this is search volume, not usage).
- If no explicit usage count is given for a keyword:
    - Set required_min = null, required_max = null.
- For LSI / "single-word LSI keys" lists without counts:
    - Use required_min = 1, required_max = null (we just want them to appear at least once).

Output JSON with this exact shape:

{
  "main": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ],
  "support": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ],
  "lsi": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ]
}

- Put "primary"/"main" keywords into "main".
- Put "secondary"/"support" keywords into "support".
- Put LSI/single-word LSI into "lsi".
- If a category does not exist in the brief, return an empty list for it.
"""


def count_keyword_occurrences(text: str, phrase: str) -> int:
    """Count case-insensitive occurrences of a phrase in text."""
    if not phrase or not phrase.strip():
        return 0
    pattern = re.escape(phrase.strip())
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def build_keyword_report(article_text: str, kw_data: dict) -> list:
    """
    Build keyword audit report from extracted keywords and article text.

    Args:
        article_text: Full article text
        kw_data: Dict with "main", "support", "lsi" keyword lists

    Returns:
        List of keyword audit dicts for template rendering
    """
    seo_keywords = []

    for group_name in ["main", "support", "lsi"]:
        items = kw_data.get(group_name, []) or []
        for item in items:
            kw = (item.get("keyword") or "").strip()
            if not kw:
                continue
            required_min = item.get("required_min")
            required_max = item.get("required_max")
            found = count_keyword_occurrences(article_text, kw)

            if required_min is None and required_max is None:
                # No explicit count - just presence
                status = "missing" if found == 0 else "present"
            else:
                if found == 0 and (required_min or 0) > 0:
                    status = "missing"
                elif required_min is not None and found < required_min:
                    status = "underused"
                elif required_max is not None and found > required_max:
                    status = "overused"
                else:
                    status = "compliant"

            seo_keywords.append({
                "keyword": kw,
                "group": group_name,
                "found": found,
                "required_min": required_min,
                "required_max": required_max,
                "status": status,
            })

    return seo_keywords


class KeywordCheck(CheckModule):
    """
    Keyword extraction and counting check.

    Uses structured brief parsing when BriefData has keywords.
    Falls back to LLM extraction when no keywords found in structure.
    """
    name = "keywords"
    requires_llm = True
    requires_brief = True
    model_tier = "mini"  # LLM fallback uses mini tier

    def run(self, article_text: str, brief_data: Union[BriefData, str] = None, **kwargs) -> CheckResult:
        """
        Extract keywords from brief and count occurrences in article.

        Args:
            article_text: Full article text
            brief_data: BriefData object or raw brief text (legacy support)
            max_brief_chars: Optional char limit for LLM fallback (default 40000)

        Returns:
            CheckResult with keyword audit data
        """
        max_brief_chars = kwargs.get('max_brief_chars', 40000)

        # Handle legacy string input for backwards compatibility
        if isinstance(brief_data, str):
            brief_data = BriefData(raw_text=brief_data, parse_method="none")

        if not brief_data:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No brief data provided",
                details={"error": "Brief data required for keyword extraction"}
            )

        # Check if we have parsed keywords
        parse_method = brief_data.parse_method
        used_llm = False

        if brief_data.has_keywords:
            # Use structured keywords - no LLM needed
            keywords_json = brief_data.get_keywords_by_group()
        else:
            # Fall back to LLM extraction
            used_llm = True
            parse_method = "llm_fallback"
            keywords_json = {"main": [], "support": [], "lsi": []}

            if self.client and brief_data.raw_text:
                brief_for_llm = brief_data.raw_text[:max_brief_chars]
                try:
                    kw_completion = self.client.chat.completions.create(
                        model=self.model,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": KEYWORDS_SYSTEM_PROMPT},
                            {"role": "user", "content": f"Task brief:\n\n\"\"\"\n{brief_for_llm}\n\"\"\""},
                        ],
                    )
                    keywords_json = json.loads(kw_completion.choices[0].message.content)
                except Exception as e:
                    print(f"Keyword extraction failed: {e}")
                    keywords_json = {"main": [], "support": [], "lsi": []}

        # Count keywords in article (Python-based)
        seo_keywords = build_keyword_report(article_text, keywords_json)

        # Calculate status summary and generate comments
        total = len(seo_keywords)
        missing = sum(1 for k in seo_keywords if k["status"] == "missing")
        underused = sum(1 for k in seo_keywords if k["status"] == "underused")
        overused = sum(1 for k in seo_keywords if k["status"] == "overused")
        compliant = sum(1 for k in seo_keywords if k["status"] in ["compliant", "present"])

        # Phase 6: Generate WriterComment for keyword issues
        comments = []
        for kw in seo_keywords:
            if kw["status"] == "missing":
                comments.append(WriterComment(
                    id=f"keywords_{len(comments)}",
                    check_name="keywords",
                    severity="error",
                    anchor_text="",  # Document-level
                    comment_text=f"MISSING KEYWORD: '{kw['keyword']}' ({kw['group']}) was not found in the article. BRIEF REQUIRES: min {kw['required_min'] or 1}x usage. ACTION: Add this keyword to the content.",
                    context=f"Group: {kw['group']}, Required: {kw['required_min']}-{kw['required_max']}"
                ))
            elif kw["status"] == "underused":
                comments.append(WriterComment(
                    id=f"keywords_{len(comments)}",
                    check_name="keywords",
                    severity="warning",
                    anchor_text=kw["keyword"],
                    comment_text=f"UNDERUSED KEYWORD: '{kw['keyword']}' appears {kw['found']}x but brief requires min {kw['required_min']}x. ACTION: Add {kw['required_min'] - kw['found']} more occurrence(s).",
                    context=f"Found: {kw['found']}, Required: {kw['required_min']}-{kw['required_max']}"
                ))
            elif kw["status"] == "overused":
                comments.append(WriterComment(
                    id=f"keywords_{len(comments)}",
                    check_name="keywords",
                    severity="warning",
                    anchor_text=kw["keyword"],
                    comment_text=f"OVERUSED KEYWORD: '{kw['keyword']}' appears {kw['found']}x but brief allows max {kw['required_max']}x. ACTION: Reduce by {kw['found'] - kw['required_max']} occurrence(s) to avoid keyword stuffing.",
                    context=f"Found: {kw['found']}, Max allowed: {kw['required_max']}"
                ))

        if total == 0:
            status = "warn"
            summary = "No keywords found in brief"
        elif missing > 0 or underused > 0:
            status = "warn"
            summary = f"{compliant}/{total} compliant, {missing} missing, {underused} underused"
        else:
            status = "pass"
            summary = f"All {total} keywords compliant"

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "keywords": seo_keywords,
                "extracted_json": keywords_json,
                "parse_method": parse_method,
                "used_llm_fallback": used_llm,
                "parse_warnings": brief_data.parse_warnings,
                "stats": {
                    "total": total,
                    "compliant": compliant,
                    "missing": missing,
                    "underused": underused,
                    "overused": overused
                }
            },
            comments=comments  # Phase 6: Writer comments
        )
