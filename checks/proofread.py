"""
Proofreading check module.

Performs grammar, spelling, punctuation corrections and style suggestions
using GPT-4.1. Enhanced in Phase 3 to catch:
- Grammar/spelling errors (severity: error)
- Awkward phrasing (severity: warning)
- Redundancy (severity: warning)
- Weak transitions (severity: suggestion)
- Passive voice overuse (severity: suggestion)
- Clichés (severity: suggestion)
"""

import json
from .base import CheckModule, CheckResult


PROOFREAD_SYSTEM_PROMPT = """
You are a professional iGaming content editor and proofreader.

Your job:
1. Fix grammar, spelling, punctuation errors (severity: "error", issue_type: "grammar")
2. Flag awkward phrasing that reads poorly (severity: "warning", issue_type: "awkward")
3. Flag redundant phrases (severity: "warning", issue_type: "redundancy")
4. Flag weak/lazy transitions (severity: "suggestion", issue_type: "transition")
5. Flag excessive passive voice (severity: "suggestion", issue_type: "passive")
6. Flag clichés (severity: "suggestion", issue_type: "cliche")

Respect the requested English variant (US, UK, Canadian, Australian, New Zealand, Indian).
Do NOT rewrite factual content, headings structure, or SEO keywords unless clearly misspelled.
For suggestions, provide the corrected version but understand the writer may choose to keep the original.

COMMON REDUNDANCIES to flag:
- "free bonus" (bonuses are inherently free - suggest just "bonus")
- "added bonus" (redundant - suggest "bonus" or "additional benefit")
- "completely free" (free is absolute - suggest "free")
- "very unique" (unique is absolute - suggest "unique")
- "past history" (suggest "history")
- "future plans" (suggest "plans")
- "end result" (suggest "result")
- "free gift" (gifts are free - suggest "gift")
- "advance warning" (suggest "warning")

WEAK TRANSITIONS to flag:
- "Now let's look at..."
- "Moving on to..."
- "Without further ado..."
- "In this section, we will..."
- "Let's dive into..."
- "Let's explore..."
- "As mentioned earlier..."
- "It's worth noting that..."

CLICHÉS to flag:
- "In today's fast-paced world"
- "Look no further"
- "Take your gaming to the next level"
- "Whether you're a beginner or a seasoned pro"
- "At the end of the day"
- "It goes without saying"
- "Last but not least"
- "When it comes to"

PASSIVE VOICE examples to flag (suggest active alternatives):
- "The bonus was given to players" → "Players receive the bonus"
- "Deposits are processed by the casino" → "The casino processes deposits"

Output JSON ONLY in this exact format:

{
  "sentences": [
    {
      "original": "string (the original sentence)",
      "corrected": "string (the corrected sentence, or same as original if no change)",
      "status": "ok" | "corrected",
      "severity": "error" | "warning" | "suggestion" | null,
      "issue_type": "grammar" | "awkward" | "redundancy" | "transition" | "passive" | "cliche" | null,
      "explanation": "string (why you changed it, or 'No change needed.')"
    }
  ],
  "clean_article": "the full corrected article as one block of text (apply only error-level fixes)",
  "stats": {
    "errors": 0,
    "warnings": 0,
    "suggestions": 0
  }
}

IMPORTANT:
- For "ok" status sentences, set severity and issue_type to null
- The "clean_article" should only apply error-level corrections (grammar/spelling), NOT warnings or suggestions
- Count each correction in the appropriate stats field
"""


class ProofreadCheck(CheckModule):
    """
    Grammar, spelling, punctuation, and style check.

    Uses GPT-4.1 for precise language corrections.
    Returns sentence-level corrections with severity levels that can be applied to Google Docs.

    Severity levels:
    - error: Grammar, spelling, punctuation mistakes (should be fixed)
    - warning: Awkward phrasing, redundancy (should probably be fixed)
    - suggestion: Weak transitions, passive voice, clichés (optional improvements)
    """
    name = "proofread"
    requires_llm = True
    requires_brief = False  # Proofreading doesn't need the brief
    model_tier = "full"     # Precision-critical, use GPT-4.1

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Run proofreading check on the article.

        Args:
            article_text: Full article text to proofread
            brief_text: Not used for proofreading
            english_variant: English variant to use (UK, US, Canadian, etc.)
            brand_config: Brand style guide configuration (optional)

        Returns:
            CheckResult with sentence corrections and clean article
        """
        english_variant = kwargs.get('english_variant', 'UK')

        # Extract brand-specific settings (Phase 5)
        brand_config = kwargs.get('brand_config', {})
        voice_rules = brand_config.get('voice', {})
        banned_phrases = brand_config.get('banned_phrases', {})
        transitions_rules = brand_config.get('transitions', {})
        stop_words = brand_config.get('stop_words', [])

        # Build brand-specific instructions
        brand_instructions = []

        # Voice preference
        preferred_voice = voice_rules.get('person')
        if preferred_voice == 'second':
            brand_instructions.append("- This brand uses second person (you/your). Flag any use of third person (players/they) when referring to the reader.")
        elif preferred_voice == 'third':
            brand_instructions.append("- This brand uses third person (players/they). Flag any use of second person (you/your) when referring to the reader.")

        # Additional banned phrases from brand config
        error_phrases = banned_phrases.get('errors', [])
        if error_phrases:
            brand_instructions.append(f"- Flag as ERROR these banned phrases: {', '.join(error_phrases)}")

        warning_phrases = banned_phrases.get('warnings', [])
        if warning_phrases:
            brand_instructions.append(f"- Flag as WARNING these phrases: {', '.join(warning_phrases)}")

        # Banned transitions
        banned_transitions = transitions_rules.get('banned_transitions', [])
        if banned_transitions:
            brand_instructions.append(f"- Additional weak transitions to flag: {', '.join(banned_transitions[:5])}")

        # AI stop words (commonly overused in AI-generated content)
        if stop_words:
            # Include first 20 to keep prompt reasonable
            brand_instructions.append(f"- Flag as WARNING these overused AI words/phrases: {', '.join(stop_words[:20])}")

        if not self.client:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No OpenAI client provided",
                details={"error": "OpenAI client required for proofreading"}
            )

        # Build the user prompt with brand instructions if any
        brand_rules_section = ""
        if brand_instructions:
            brand_rules_section = "\n\nBRAND-SPECIFIC RULES:\n" + "\n".join(brand_instructions)

        user_prompt = f"""
English variant to use: {english_variant}{brand_rules_section}

Here is the article text to proofread:

\"\"\"{article_text}\"\"\"
"""

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": PROOFREAD_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )

            raw_text = completion.choices[0].message.content
            data = json.loads(raw_text)

            sentences = data.get("sentences", [])
            clean_article = data.get("clean_article", "")
            stats = data.get("stats", {"errors": 0, "warnings": 0, "suggestions": 0})

            # Count corrections by severity
            error_count = sum(1 for s in sentences if s.get("severity") == "error")
            warning_count = sum(1 for s in sentences if s.get("severity") == "warning")
            suggestion_count = sum(1 for s in sentences if s.get("severity") == "suggestion")
            total_corrections = sum(1 for s in sentences if s.get("status") == "corrected")

            # Update stats if LLM didn't provide them
            if stats.get("errors", 0) == 0 and error_count > 0:
                stats["errors"] = error_count
            if stats.get("warnings", 0) == 0 and warning_count > 0:
                stats["warnings"] = warning_count
            if stats.get("suggestions", 0) == 0 and suggestion_count > 0:
                stats["suggestions"] = suggestion_count

            # Determine overall status
            if error_count > 0:
                status = "warn"
                summary_parts = []
                if error_count > 0:
                    summary_parts.append(f"{error_count} error(s)")
                if warning_count > 0:
                    summary_parts.append(f"{warning_count} warning(s)")
                if suggestion_count > 0:
                    summary_parts.append(f"{suggestion_count} suggestion(s)")
                summary = ", ".join(summary_parts)
            elif warning_count > 0 or suggestion_count > 0:
                status = "warn"
                summary_parts = []
                if warning_count > 0:
                    summary_parts.append(f"{warning_count} warning(s)")
                if suggestion_count > 0:
                    summary_parts.append(f"{suggestion_count} suggestion(s)")
                summary = "No errors, but " + ", ".join(summary_parts)
            else:
                status = "pass"
                summary = "No corrections needed"

            return CheckResult(
                name=self.name,
                status=status,
                summary=summary,
                details={
                    "sentences": sentences,
                    "clean_article": clean_article,
                    "english_variant": english_variant,
                    "stats": stats
                },
                corrections=sentences  # For Google Docs integration
            )

        except json.JSONDecodeError as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary="Invalid JSON response from model",
                details={"error": str(e), "raw_response": raw_text[:500]}
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary=f"Proofreading failed: {str(e)}",
                details={"error": str(e)}
            )
