"""
Style check module.

Hybrid check combining Python pattern matching with optional LLM tone analysis:
- Python layer (always runs): Voice consistency, superlative overuse, stop words
- LLM layer (optional): Tone matching against brief's style notes

Now includes exact location tracking for all issues.
"""

import re
import json
from typing import List, Dict, Optional, Union, Tuple
from .base import CheckModule, CheckResult

# Import BriefData for type hints
try:
    from brief_parser import BriefData
except ImportError:
    BriefData = None


# -----------------------------------------------------------------------------
# Heading/Section Tracking
# -----------------------------------------------------------------------------

def parse_headings_with_positions(text: str) -> List[Dict]:
    """Parse all headings from text with their character positions."""
    headings = []

    # Markdown-style headings
    for match in re.finditer(r'^(#{1,3})\s+(.+)$', text, re.MULTILINE):
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        headings.append({
            "text": heading_text,
            "level": level,
            "start_pos": match.start(),
            "end_pos": match.end(),
        })

    # HTML-style headings
    for match in re.finditer(r'<h([1-3])[^>]*>([^<]+)</h\1>', text, re.IGNORECASE):
        level = int(match.group(1))
        heading_text = match.group(2).strip()
        headings.append({
            "text": heading_text,
            "level": level,
            "start_pos": match.start(),
            "end_pos": match.end(),
        })

    headings.sort(key=lambda h: h["start_pos"])
    return headings


def find_nearest_heading(headings: List[Dict], position: int) -> Optional[str]:
    """Find the nearest preceding heading for a given position."""
    nearest = None
    for h in headings:
        if h["start_pos"] <= position:
            nearest = h
        else:
            break
    if nearest:
        return f"H{nearest['level']}: {nearest['text'][:50]}"
    return None


def get_context_around_match(text: str, match_start: int, match_end: int, context_chars: int = 40) -> str:
    """Get surrounding context around a match, with the match highlighted."""
    start = max(0, match_start - context_chars)
    end = min(len(text), match_end + context_chars)

    prefix = text[start:match_start]
    matched = text[match_start:match_end]
    suffix = text[match_end:end]

    # Clean up whitespace
    prefix = re.sub(r'\s+', ' ', prefix).lstrip()
    suffix = re.sub(r'\s+', ' ', suffix).rstrip()

    return f"...{prefix}**{matched}**{suffix}..."


# Voice detection patterns
SECOND_PERSON_PATTERNS = [
    r'\byou\b',
    r'\byour\b',
    r'\byou\'re\b',
    r'\byou\'ll\b',
    r'\byou\'ve\b',
    r'\byourself\b',
]

THIRD_PERSON_PATTERNS = [
    r'\bplayers\b',
    r'\bthey\b',
    r'\bthem\b',
    r'\btheir\b',
    r'\bgamblers\b',
    r'\busers\b',
    r'\bcustomers\b',
    r'\bmembers\b',
]

# Superlative patterns (often overused in promotional content)
SUPERLATIVE_PATTERNS = [
    (r'\bbest\b', 'best'),
    (r'\btop\b', 'top'),
    (r'\bgreatest\b', 'greatest'),
    (r'\bamazing\b', 'amazing'),
    (r'\bincredible\b', 'incredible'),
    (r'\bfantastic\b', 'fantastic'),
    (r'\bunbeatable\b', 'unbeatable'),
    (r'\bultimate\b', 'ultimate'),
    (r'\bexclusive\b', 'exclusive'),
    (r'\bunique\b', 'unique'),
]

# Thresholds
VOICE_MIX_THRESHOLD = 5  # Flag if both voices appear > this many times
SUPERLATIVE_DENSITY_THRESHOLD = 2.0  # Flag if superlatives > X% of words


STYLE_TONE_PROMPT = """
You are reviewing an iGaming article's tone and style.

The content brief specified these style guidelines:
{style_notes}

Review the article and assess whether the tone matches these guidelines.

Consider:
1. Is the tone informational, promotional, casual, or formal?
2. Does it match what the brief requested?
3. Are there specific phrases or sections that don't fit the requested style?

Output JSON:
{{
  "tone_assessment": "matches" | "partially_matches" | "does_not_match",
  "detected_tone": "informational" | "promotional" | "casual" | "formal" | "mixed",
  "confidence": "high" | "medium" | "low",
  "findings": [
    {{
      "type": "tone_mismatch",
      "severity": "warning",
      "description": "Description of the mismatch",
      "examples": ["quoted example 1", "quoted example 2"],
      "suggestion": "How to improve"
    }}
  ],
  "summary": "Brief assessment summary"
}}

If the tone matches well, return an empty findings array.
"""


class StyleCheck(CheckModule):
    """
    Style and tone check for iGaming content.

    Hybrid approach:
    - Python layer (always runs): Voice consistency, superlative density
    - LLM layer (optional): Tone matching if brief has style notes

    Uses GPT-4.1-mini for the LLM layer since it's a classification task.
    """
    name = "style"
    requires_llm = True       # For optional LLM tone check
    requires_brief = True     # Needs style notes from brief
    model_tier = "mini"       # Classification task

    def run(self, article_text: str, brief_data: Union['BriefData', str] = None, **kwargs) -> CheckResult:
        """
        Check article style and tone with location tracking.

        Args:
            article_text: Full article text
            brief_data: BriefData object (for style notes) or None
            brand_config: Brand style guide configuration (optional)

        Returns:
            CheckResult with style findings including precise locations
        """
        # Extract brand-specific settings (Phase 5)
        brand_config = kwargs.get('brand_config', {})
        voice_rules = brand_config.get('voice', {})
        preferred_voice = voice_rules.get('person', None)  # 'second' or 'third'

        findings = []

        # Parse headings for location tracking
        headings = parse_headings_with_positions(article_text)

        # Python layer - always runs
        voice_findings = self._check_voice_consistency(article_text, headings, preferred_voice=preferred_voice)
        findings.extend(voice_findings)

        superlative_findings = self._check_superlatives(article_text)
        findings.extend(superlative_findings)

        # Stop words check (AI-generated content markers from brand config)
        stop_words = brand_config.get('stop_words', [])
        if stop_words:
            stop_word_findings = self._check_stop_words(article_text, stop_words, headings)
            findings.extend(stop_word_findings)

        # LLM layer - only if style notes exist and we have a client
        style_notes = None
        tone_assessment = None

        if brief_data:
            # Try to get style_notes from BriefData
            if hasattr(brief_data, 'style_notes') and brief_data.style_notes:
                style_notes = brief_data.style_notes
            elif hasattr(brief_data, 'raw_text') and brief_data.raw_text:
                # Check if raw text contains style-related keywords
                raw_lower = brief_data.raw_text.lower()
                if any(kw in raw_lower for kw in ['tone', 'style', 'voice', 'formal', 'casual', 'informational']):
                    # Extract style notes from raw text
                    style_notes = self._extract_style_hints(brief_data.raw_text)

        if style_notes and self.client:
            tone_findings, tone_assessment = self._check_tone_with_llm(article_text, style_notes)
            findings.extend(tone_findings)

        # Calculate stats
        warning_count = sum(1 for f in findings if f.get("severity") == "warning")
        info_count = sum(1 for f in findings if f.get("severity") == "info")

        # Check for stop word findings
        stop_word_findings = [f for f in findings if f.get("type") == "stop_words"]

        # Determine status
        if warning_count > 0:
            status = "warn"
            summary_parts = []
            if voice_findings:
                summary_parts.append("mixed voice")
            if superlative_findings:
                summary_parts.append("superlative overuse")
            if stop_word_findings:
                summary_parts.append("AI stop words")
            if tone_assessment and tone_assessment != "matches":
                summary_parts.append("tone mismatch")
            summary = f"Style issues: {', '.join(summary_parts)}" if summary_parts else f"{warning_count} warning(s)"
        else:
            status = "pass"
            summary = "Style consistent"
            if tone_assessment == "matches":
                summary += ", tone matches brief"

        # Extract stop words found for details
        stop_words_found = {}
        for f in stop_word_findings:
            if "stop_words_found" in f:
                stop_words_found = f["stop_words_found"]
                break

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "findings": findings,
                "voice_analysis": self._get_voice_stats(article_text),
                "superlative_analysis": self._get_superlative_stats(article_text),
                "stop_words_analysis": {
                    "found": stop_words_found,
                    "total_occurrences": sum(stop_words_found.values()) if stop_words_found else 0,
                    "unique_terms": len(stop_words_found),
                },
                "tone_assessment": tone_assessment,
                "style_notes_found": style_notes is not None,
                "stats": {
                    "warnings": warning_count,
                    "info": info_count
                }
            }
        )

    def _check_voice_consistency(self, text: str, headings: List[Dict], preferred_voice: str = None) -> List[Dict]:
        """
        Check for mixed second/third person voice with location tracking.

        Args:
            text: Article text to check
            headings: Parsed headings for location context
            preferred_voice: 'second' or 'third' from brand config, or None for auto-detect
        """
        # Find all pronoun matches with positions
        second_matches = []
        for pattern in SECOND_PERSON_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                heading = find_nearest_heading(headings, match.start())
                context = get_context_around_match(text, match.start(), match.end())
                second_matches.append({
                    "pronoun": match.group(),
                    "position": match.start(),
                    "heading": heading,
                    "context": context,
                    "location": f"under {heading}" if heading else "near start",
                })

        third_matches = []
        for pattern in THIRD_PERSON_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                heading = find_nearest_heading(headings, match.start())
                context = get_context_around_match(text, match.start(), match.end())
                third_matches.append({
                    "pronoun": match.group(),
                    "position": match.start(),
                    "heading": heading,
                    "context": context,
                    "location": f"under {heading}" if heading else "near start",
                })

        second_count = len(second_matches)
        third_count = len(third_matches)
        findings = []

        # Determine which pronouns to flag based on preference or majority
        if preferred_voice == 'second' and third_count > VOICE_MIX_THRESHOLD:
            # Flag third-person usage (the minority/wrong voice)
            occurrences = [
                {"context": m["context"], "location": m["location"], "pronoun": m["pronoun"]}
                for m in third_matches[:8]  # First 8 examples
            ]
            findings.append({
                "type": "voice_mismatch",
                "severity": "warning",
                "message": f"Brand requires second person (you/your), but found {third_count} third-person references",
                "occurrences": occurrences,
                "suggestion": "Replace third-person references with second person. E.g., 'players can' → 'you can'.",
            })
        elif preferred_voice == 'third' and second_count > VOICE_MIX_THRESHOLD:
            # Flag second-person usage (the minority/wrong voice)
            occurrences = [
                {"context": m["context"], "location": m["location"], "pronoun": m["pronoun"]}
                for m in second_matches[:8]
            ]
            findings.append({
                "type": "voice_mismatch",
                "severity": "warning",
                "message": f"Brand requires third person (players/they), but found {second_count} second-person references",
                "occurrences": occurrences,
                "suggestion": "Replace second-person references with third person. E.g., 'you can' → 'players can'.",
            })
        elif second_count > VOICE_MIX_THRESHOLD and third_count > VOICE_MIX_THRESHOLD:
            # Mixed voice - flag the minority
            if second_count > third_count:
                dominant = "second person (you/your)"
                minority_matches = third_matches
                minority_count = third_count
            else:
                dominant = "third person (players/they)"
                minority_matches = second_matches
                minority_count = second_count

            occurrences = [
                {"context": m["context"], "location": m["location"], "pronoun": m["pronoun"]}
                for m in minority_matches[:8]
            ]
            findings.append({
                "type": "voice_inconsistency",
                "severity": "warning",
                "message": f"Mixed voice detected: {second_count} second-person, {third_count} third-person references",
                "description": f"Article uses {dominant} predominantly",
                "occurrences": occurrences,
                "suggestion": "Use consistent voice throughout. For engagement, prefer 'you/your'. For formality, use 'players/members'.",
            })

        return findings

    def _check_superlatives(self, text: str) -> List[Dict]:
        """Check for excessive superlative/promotional language."""
        total_words = len(text.split())
        if total_words == 0:
            return []

        superlative_counts = {}
        total_superlatives = 0

        for pattern, word in SUPERLATIVE_PATTERNS:
            count = len(re.findall(pattern, text, re.IGNORECASE))
            if count > 0:
                superlative_counts[word] = count
                total_superlatives += count

        density = (total_superlatives / total_words) * 100

        findings = []

        if density > SUPERLATIVE_DENSITY_THRESHOLD:
            top_superlatives = sorted(superlative_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            top_list = ", ".join(f"'{w}' ({c}x)" for w, c in top_superlatives)

            findings.append({
                "type": "superlative_overuse",
                "severity": "warning",
                "message": f"High superlative density: {density:.1f}% of words are promotional terms",
                "description": f"Most used: {top_list}",
                "suggestion": "Reduce promotional language for a more informational tone. Use specific facts instead of superlatives.",
            })
        elif total_superlatives > 10:
            # Even if density is OK, flag if absolute count is high
            findings.append({
                "type": "superlative_usage",
                "severity": "info",
                "message": f"{total_superlatives} superlative/promotional terms found",
                "description": f"Density: {density:.1f}%",
                "suggestion": "Consider if all superlatives are necessary",
            })

        return findings

    def _check_tone_with_llm(self, article_text: str, style_notes: str) -> tuple:
        """Use LLM to check if article tone matches brief's style notes."""
        findings = []
        tone_assessment = None

        prompt = STYLE_TONE_PROMPT.format(style_notes=style_notes)

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"Article to review:\n\n{article_text[:8000]}"},  # Limit length
                ],
            )

            raw_text = completion.choices[0].message.content
            data = json.loads(raw_text)

            tone_assessment = data.get("tone_assessment", "unknown")
            llm_findings = data.get("findings", [])

            # Add LLM findings to our list
            for f in llm_findings:
                findings.append({
                    "type": f.get("type", "tone_issue"),
                    "severity": f.get("severity", "warning"),
                    "message": f.get("description", "Tone mismatch detected"),
                    "description": f.get("description", ""),
                    "examples": f.get("examples", []),
                    "suggestion": f.get("suggestion", ""),
                })

        except Exception as e:
            # LLM check failed, but Python checks still valid
            findings.append({
                "type": "tone_check_error",
                "severity": "info",
                "message": f"Could not perform LLM tone check: {str(e)}",
                "description": "Style notes were found but tone analysis failed",
                "suggestion": "Review article tone manually against brief requirements",
            })

        return findings, tone_assessment

    def _get_voice_stats(self, text: str) -> Dict:
        """Get detailed voice usage statistics."""
        second_count = sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in SECOND_PERSON_PATTERNS
        )
        third_count = sum(
            len(re.findall(pattern, text, re.IGNORECASE))
            for pattern in THIRD_PERSON_PATTERNS
        )

        total = second_count + third_count
        return {
            "second_person_count": second_count,
            "third_person_count": third_count,
            "second_person_ratio": round(second_count / total * 100, 1) if total > 0 else 0,
            "third_person_ratio": round(third_count / total * 100, 1) if total > 0 else 0,
            "dominant_voice": "second" if second_count > third_count else "third" if third_count > second_count else "balanced",
        }

    def _get_superlative_stats(self, text: str) -> Dict:
        """Get detailed superlative usage statistics."""
        total_words = len(text.split())
        superlative_counts = {}
        total_superlatives = 0

        for pattern, word in SUPERLATIVE_PATTERNS:
            count = len(re.findall(pattern, text, re.IGNORECASE))
            if count > 0:
                superlative_counts[word] = count
                total_superlatives += count

        return {
            "total_superlatives": total_superlatives,
            "density_percent": round((total_superlatives / total_words) * 100, 2) if total_words > 0 else 0,
            "by_word": superlative_counts,
        }

    def _extract_style_hints(self, raw_text: str) -> Optional[str]:
        """Try to extract style-related content from raw brief text."""
        # Look for style-related sections
        style_keywords = ['tone', 'style', 'voice', 'writing style', 'brand voice']
        lines = raw_text.split('\n')

        style_section = []
        in_style_section = False

        for line in lines:
            line_lower = line.lower()

            # Start capturing if we hit a style keyword
            if any(kw in line_lower for kw in style_keywords):
                in_style_section = True
                style_section.append(line)
            elif in_style_section:
                # Stop if we hit a new section header (short line followed by content)
                if line.strip() and len(line.strip()) < 50 and not line.strip().endswith(':'):
                    # Might be a new section, check if it looks like a header
                    if line.strip().isupper() or (line.strip()[0].isupper() and ':' not in line):
                        break
                style_section.append(line)

        if style_section:
            return '\n'.join(style_section[:20])  # Limit to first 20 lines

        return None

    def _check_stop_words(self, text: str, stop_words: List[str], headings: List[Dict]) -> List[Dict]:
        """
        Check for AI-generated content markers (stop words) with location tracking.

        These are words/phrases commonly overused in AI-generated content
        that should be avoided per the General Writing Requirements §14.
        """
        findings = []
        found_stop_words = {}
        all_occurrences = []

        for stop_word in stop_words:
            # Find all matches with positions
            if ' ' in stop_word:
                # Multi-word phrase - find with regex
                pattern = re.escape(stop_word)
            else:
                # Single word - use word boundaries
                pattern = rf'\b{re.escape(stop_word)}\b'

            for match in re.finditer(pattern, text, re.IGNORECASE):
                if stop_word not in found_stop_words:
                    found_stop_words[stop_word] = 0
                found_stop_words[stop_word] += 1

                # Get location and context for this occurrence
                heading = find_nearest_heading(headings, match.start())
                context = get_context_around_match(text, match.start(), match.end())
                all_occurrences.append({
                    "stop_word": stop_word,
                    "context": context,
                    "location": f"under {heading}" if heading else "near start",
                })

        if found_stop_words:
            total_occurrences = sum(found_stop_words.values())
            top_offenders = sorted(found_stop_words.items(), key=lambda x: x[1], reverse=True)[:10]
            offender_list = ", ".join(f"'{w}' ({c}x)" for w, c in top_offenders)

            # Include first 10 occurrences with context
            occurrences_sample = all_occurrences[:10]

            findings.append({
                "type": "stop_words",
                "severity": "warning",
                "message": f"Found {total_occurrences} AI stop word occurrence(s) across {len(found_stop_words)} terms",
                "description": f"These words are commonly overused in AI-generated content: {offender_list}",
                "occurrences": occurrences_sample,
                "suggestion": "Replace with more natural, specific alternatives. E.g., 'seamless' → 'smooth', 'leverage' → 'use', 'ensure' → 'make sure'.",
                "stop_words_found": found_stop_words,
            })

        return findings
