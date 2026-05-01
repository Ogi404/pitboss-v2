"""
Consistency check module.

Detects internal contradictions within an article using GPT-4.1-mini:
- Bonus value inconsistencies
- Wagering requirement conflicts
- Casino/operator name spelling variations
- Date/timeframe conflicts
- Contradicting claims
"""

import json
from .base import CheckModule, CheckResult, WriterComment


CONSISTENCY_SYSTEM_PROMPT = """
You are a fact-checker reviewing an iGaming article for internal consistency.

Your job: Find contradictions WITHIN the article. Do NOT compare against external sources.
Only flag issues where the article contradicts ITSELF.

Check for these types of inconsistencies:

1. BONUS VALUE CONSISTENCY (type: "bonus_value")
   - Is the same bonus described with different percentages or amounts?
   - Example issue: "200% up to £500" in intro, but "200% up to £200" in a table
   - This is a common copy-paste error

2. WAGERING REQUIREMENT CONSISTENCY (type: "wagering")
   - Are playthrough/rollover requirements stated consistently?
   - Example issue: "35x wagering" in one place, "30x rollover" in another
   - Watch for: different numbers, different terminology for same thing

3. NAME/SPELLING CONSISTENCY (type: "name_spelling")
   - Is the operator/casino name spelled the same way throughout?
   - Example issue: "BetWinner" vs "Bet Winner" vs "betwinner"
   - Also check game provider names, payment method names

4. DATE/TIME CONSISTENCY (type: "dates")
   - Are expiry dates, validity periods stated consistently?
   - Example issue: "Valid until March 2024" vs "Expires in April 2024"
   - Watch for conflicting timeframes

5. CONTRADICTING CLAIMS (type: "contradiction")
   - Does the article make claims that directly contradict each other?
   - Example issue: "No wagering requirements" then later "35x playthrough applies"
   - Example issue: "Instant withdrawals" then "3-5 day processing time"

Assign severity:
- "error" for clear numerical/factual contradictions (different bonus amounts, wagering numbers)
- "warning" for spelling variations and potential ambiguities

If you find NO inconsistencies, return an empty findings array.

Output JSON in this exact format:
{
  "findings": [
    {
      "type": "bonus_value" | "wagering" | "name_spelling" | "dates" | "contradiction",
      "severity": "error" | "warning",
      "description": "Brief description of the inconsistency",
      "instances": [
        {"text": "exact quoted text from article", "location": "where it appears (intro, table, section name, etc.)"},
        {"text": "conflicting quoted text", "location": "where it appears"}
      ],
      "suggestion": "How to resolve this inconsistency"
    }
  ],
  "summary": "X inconsistencies found" | "No inconsistencies found"
}

IMPORTANT:
- Only flag REAL inconsistencies, not stylistic variations
- Quote the EXACT text from the article in "instances"
- Be specific about where each instance appears
- Don't flag things that aren't actually contradictions
"""


class ConsistencyCheck(CheckModule):
    """
    Internal consistency check for iGaming content.

    Uses GPT-4.1-mini to detect contradictions within an article:
    - Bonus values stated differently in different places
    - Wagering requirements that don't match
    - Inconsistent spelling of names
    - Conflicting dates/timeframes
    - Claims that contradict each other
    """
    name = "consistency"
    requires_llm = True
    requires_brief = False  # Only analyzes the article itself
    model_tier = "mini"     # Pattern matching task, not nuanced language

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Check article for internal consistency issues.

        Args:
            article_text: Full article text to check

        Returns:
            CheckResult with any inconsistencies found
        """
        if not self.client:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No OpenAI client provided",
                details={"error": "OpenAI client required for consistency check"}
            )

        user_prompt = f"""
Review this iGaming article for internal consistency issues:

\"\"\"{article_text}\"\"\"

Find any contradictions, inconsistent values, or conflicting information WITHIN the article.
"""

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": CONSISTENCY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw_text = completion.choices[0].message.content
            data = json.loads(raw_text)

            findings = data.get("findings", [])
            llm_summary = data.get("summary", "")

            # Count findings by type and generate comments
            findings_by_type = {}
            error_count = 0
            warning_count = 0
            comments = []  # Phase 6: Collect WriterComment objects

            for finding in findings:
                finding_type = finding.get("type", "unknown")
                findings_by_type[finding_type] = findings_by_type.get(finding_type, 0) + 1

                if finding.get("severity") == "error":
                    error_count += 1
                else:
                    warning_count += 1

                # Generate WriterComment for each finding
                instances = finding.get("instances", [])
                anchor_text = instances[0].get("text", "") if instances else ""

                # Build detailed comment text
                instance_details = " vs ".join([
                    f'"{inst.get("text", "")}" ({inst.get("location", "unknown")})'
                    for inst in instances[:2]
                ])

                comments.append(WriterComment(
                    id=f"consistency_{len(comments)}",
                    check_name="consistency",
                    severity=finding.get("severity", "warning"),
                    anchor_text=anchor_text,
                    comment_text=f"INCONSISTENCY ({finding_type}): {finding.get('description', '')}. INSTANCES: {instance_details}. ACTION: {finding.get('suggestion', 'Verify and make consistent.')}",
                    context=f"Type: {finding_type}"
                ))

            total_findings = len(findings)

            # Determine status and summary
            if total_findings == 0:
                status = "pass"
                summary = "No internal inconsistencies found"
            elif error_count > 0:
                status = "fail"
                type_list = ", ".join(findings_by_type.keys())
                summary = f"{total_findings} inconsistency(ies) found: {type_list}"
            else:
                status = "warn"
                type_list = ", ".join(findings_by_type.keys())
                summary = f"{total_findings} potential inconsistency(ies): {type_list}"

            return CheckResult(
                name=self.name,
                status=status,
                summary=summary,
                details={
                    "findings": findings,
                    "stats": {
                        "total_findings": total_findings,
                        "errors": error_count,
                        "warnings": warning_count,
                        "by_type": findings_by_type
                    }
                },
                comments=comments  # Phase 6: Writer comments
            )

        except json.JSONDecodeError as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary="Invalid JSON response from model",
                details={"error": str(e), "raw_response": raw_text[:500] if 'raw_text' in locals() else "N/A"}
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary=f"Consistency check failed: {str(e)}",
                details={"error": str(e)}
            )
