"""
SEO and structure compliance check module.

Analyzes article structure against brief requirements:
- Heading compliance (presence, order, levels)
- Meta information
- Per-heading content quality
"""

import json
from .base import CheckModule, CheckResult


SEO_SYSTEM_PROMPT = """
You are an SEO and structure compliance reviewer for iGaming content.

You receive 2 inputs:

1) A TASK BRIEF (plain text extracted from a DOCX or Excel file). It often contains:
   - A section called things like "Suggested Article Structure".
   - Lines that start with heading labels such as:
       H1: ...
       H2: ...
       H3: ...
     sometimes followed by an approximate word count in parentheses, e.g.:
       H2: Introduction (≈150–180 words)
   - One or more lines or bullet points after each heading that describe what MUST be covered in that section.
   - FAQ / Q&A structure notes, e.g. "FAQ Structure Requirements", where each FAQ item has rules about what the question/answer must contain.
   - In Excel-based briefs, the same information can appear in tables with columns like "Heading", "Section title", "Description", "What to cover", or similar.

2) An ARTICLE (plain text extracted from a DOCX). This includes its H1/H2/H3 headings and body text.

Your job has TWO parts:

PART A — Headings, Meta & Structure

- Infer the heading / structure requirements ONLY from what is explicitly written in the TASK BRIEF
  (e.g., "Suggested Article Structure", heading lists, FAQ structure notes).
- Compare the ARTICLE against those requirements.
- Do NOT invent headings or requirements that are not clearly present in the brief.
- Focus on:
  - Presence, order, and level of headings (H1/H2/H3).
  - Presence of meta title and meta description, if the brief mentions them.
  - Structural issues (missing required sections, wrong heading level, headings merged or split, missing FAQ section, etc.).

PART B — HEADING-LEVEL CONTENT QUALITY

The brief often includes detailed per-heading content guidance, even if it is not literally labelled "Description".
You MUST actively search the TASK BRIEF for any of the following patterns and treat them as heading specifications:

- A section named "Suggested Article Structure" (or similar) followed by lines like:
    H2: Introduction (≈150–180 words)
    H2: What Is a Casino Birthday Bonus? (≈200–300 words)
  and then 1–N lines or bullet points that explain what the writer should cover in that section.

- Lines starting with "H1:", "H2:", "H3:" followed by explanatory text on the next lines.
  All explanation lines belonging to this heading (until the next heading label or a clearly separate section)
  should be treated as the description for that heading.

- Tables (especially from Excel) where one column is "Heading", "Section title", or similar,
  and another column is "Description", "Details", "What to cover", "Notes", or similar.
  Each row in such a table represents one heading specification and its content guidelines.

- FAQ structure notes such as "FAQ Structure Requirements", which may specify:
  - how many FAQ items there should be,
  - what each question/answer must contain,
  - how to use keywords or LSI terms in each FAQ.

For EACH heading specification you find in the TASK BRIEF:

1) Identify:
   - brief_heading: the heading text including its level if present
     (e.g., "H2: Introduction", "H2: What Is a 300% Casino Bonus?", "FAQ", etc.).
   - description text: all associated guidance describing what to cover in that section
     (bullet points, short paragraphs, example sentences, etc.).

2) In the ARTICLE, locate the best-matching heading/section by comparing heading text
   (case-insensitive, allow partial matches, ignore approximate word counts like "(≈150–180 words)").

3) Break the description into concrete sub-requirements as short phrases. Use:
   - individual bullet points,
   - clearly distinct sentences,
   - key items in lists (e.g., "free spins", "bonus funds", "VIP birthday gifts", "wagering requirements", "max cashout").
   Each of these becomes one "requirement" entry.

4) For each sub-requirement, inspect the ARTICLE content under the matched heading and decide:
   - "covered"           → the requirement is clearly present and reasonably explained.
   - "partially_covered" → it is mentioned but shallow, incomplete, or only implied.
   - "missing"           → it is not addressed at all.

5) For each heading specification, assign an overall status:
   - "good"    → all or almost all important requirements are covered.
   - "partial" → a mix of covered and missing / partially_covered requirements.
   - "missing" → most important requirements are missing or only weakly touched.

6) For FAQ-specific guidance:
   - Treat the FAQ section as one or more heading specifications if the brief gives rules for the FAQ block.
   - Create requirements that reflect the FAQ rules, e.g.:
       - "use one main target keyword in the question"
       - "include 1–2 LSI terms in the answer"
       - "begin with a short direct answer"
   - Evaluate whether the ARTICLE FAQ section(s) follow these rules.

IMPORTANT:
- You must TRY to find per-heading content guidance. Briefs like those with a "Suggested Article Structure" section
  almost always contain such guidance.
- ONLY if, after careful inspection, there is genuinely no per-heading content guidance at all (no heading + description pairs,
  no structured article outline, no FAQ rules), then and only then return an empty list for "content_quality".

OUTPUT FORMAT

Return JSON ONLY in this exact top-level shape:

{
  "headings": [
    {
      "level": "H1 or H2 or H3 etc.",
      "text": "the heading text in the article",
      "status": "ok or missing or extra or misordered",
      "notes": "short explanation"
    }
  ],
  "meta": {
    "has_meta_title": true,
    "has_meta_description": false,
    "issues": [
      "short description of any meta-related problems"
    ]
  },
  "structure_issues": [
    "short bullet-style descriptions of structural issues, if any"
  ],
  "content_quality": [
    {
      "brief_heading": "e.g. H2: What Is a 300% Casino Bonus?",
      "article_heading": "the matching heading text in the article, or '' if none",
      "status": "good or partial or missing",
      "requirements": [
        {
          "requirement": "short phrase describing a required subtopic",
          "coverage": "covered or partially_covered or missing",
          "notes": "one-line explanation"
        }
      ],
      "notes": "short overall comment for this heading spec"
    }
  ]
}
"""


class SEOStructureCheck(CheckModule):
    """
    SEO and structure compliance check.

    Uses GPT-4.1 to analyze article structure against brief requirements:
    - Heading compliance
    - Meta information
    - Per-heading content quality assessment
    """
    name = "seo_structure"
    requires_llm = True
    requires_brief = True
    model_tier = "full"  # Requires deep reading comprehension

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Analyze article structure against brief requirements.

        Args:
            article_text: Full article text
            brief_text: Brief/task description text
            max_brief_chars: Optional char limit for brief (default 40000)
            max_article_chars: Optional char limit for article (default 20000)

        Returns:
            CheckResult with heading, meta, and content quality data
        """
        max_brief_chars = kwargs.get('max_brief_chars', 40000)
        max_article_chars = kwargs.get('max_article_chars', 20000)

        if not brief_text:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No brief text provided",
                details={"error": "Brief text required for SEO/structure check"}
            )

        if not self.client:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No OpenAI client provided",
                details={"error": "OpenAI client required for SEO/structure check"}
            )

        # Truncate for LLM call
        brief_for_seo = brief_text[:max_brief_chars]
        article_for_seo = article_text[:max_article_chars]

        user_prompt = f"""
TASK BRIEF (possibly truncated):

\"\"\"{brief_for_seo}\"\"\"

ARTICLE (possibly truncated):

\"\"\"{article_for_seo}\"\"\"
"""

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SEO_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw_text = completion.choices[0].message.content
            data = json.loads(raw_text)

            headings = data.get("headings", [])
            meta = data.get("meta", {})
            structure_issues = data.get("structure_issues", [])
            content_quality = data.get("content_quality", [])

            # Calculate overall status
            heading_issues = sum(1 for h in headings if h.get("status") not in ["ok"])
            content_issues = sum(1 for c in content_quality if c.get("status") != "good")
            meta_issues = len(meta.get("issues", []))
            struct_issues = len(structure_issues)

            total_issues = heading_issues + content_issues + meta_issues + struct_issues

            if total_issues == 0:
                status = "pass"
                summary = "Structure and content fully compliant"
            elif total_issues <= 3:
                status = "warn"
                summary = f"{total_issues} minor issue(s) found"
            else:
                status = "fail"
                summary = f"{total_issues} issue(s) found"

            return CheckResult(
                name=self.name,
                status=status,
                summary=summary,
                details={
                    "headings": headings,
                    "meta": meta,
                    "structure_issues": structure_issues,
                    "content_quality": content_quality
                }
            )

        except json.JSONDecodeError as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary="Invalid JSON response from model",
                details={"error": str(e)}
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary=f"SEO check failed: {str(e)}",
                details={
                    "headings": [],
                    "meta": {
                        "has_meta_title": False,
                        "has_meta_description": False,
                        "issues": [f"SEO check failed: {e}"]
                    },
                    "structure_issues": ["SEO check failed due to an API error."],
                    "content_quality": []
                }
            )
