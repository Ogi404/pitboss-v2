"""
Formatting consistency check module.

Detects inconsistent formatting within an article for:
- Currency formats
- Number formats (thousands separators)
- Percentage formats
- Wagering formats
- Date formats
- Heading capitalization

Now includes exact location tracking for all issues.
"""

import re
from typing import List, Dict, Tuple, Set, Optional
from collections import Counter
from .base import CheckModule, CheckResult


# -----------------------------------------------------------------------------
# Heading/Section Tracking
# -----------------------------------------------------------------------------

def parse_headings_with_positions(text: str) -> List[Dict]:
    """
    Parse all headings from text with their character positions.

    Returns list of {text, level, start_pos, end_pos} dicts.
    Level: H1=1, H2=2, H3=3
    """
    headings = []

    # Markdown-style headings: # H1, ## H2, ### H3
    for match in re.finditer(r'^(#{1,3})\s+(.+)$', text, re.MULTILINE):
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        headings.append({
            "text": heading_text,
            "level": level,
            "start_pos": match.start(),
            "end_pos": match.end(),
        })

    # HTML-style headings: <h1>, <h2>, <h3>
    for match in re.finditer(r'<h([1-3])[^>]*>([^<]+)</h\1>', text, re.IGNORECASE):
        level = int(match.group(1))
        heading_text = match.group(2).strip()
        headings.append({
            "text": heading_text,
            "level": level,
            "start_pos": match.start(),
            "end_pos": match.end(),
        })

    # Sort by position
    headings.sort(key=lambda h: h["start_pos"])
    return headings


def find_nearest_heading(headings: List[Dict], position: int) -> Optional[str]:
    """
    Find the nearest preceding heading for a given character position.

    Returns formatted string like "H2: Banking Options" or None.
    """
    nearest = None
    for h in headings:
        if h["start_pos"] <= position:
            nearest = h
        else:
            break

    if nearest:
        return f"H{nearest['level']}: {nearest['text'][:50]}"
    return None


def get_paragraph_number(text: str, position: int) -> int:
    """Get paragraph number for a given position (split by double newlines)."""
    text_before = text[:position]
    paragraphs = re.split(r'\n\s*\n', text_before)
    return len(paragraphs)


def find_matches_with_locations(text: str, pattern: str, headings: List[Dict]) -> List[Dict]:
    """
    Find all matches of a pattern with location context.

    Returns list of {match, position, heading, paragraph} dicts.
    """
    results = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        heading = find_nearest_heading(headings, match.start())
        para_num = get_paragraph_number(text, match.start())
        results.append({
            "match": match.group(),
            "position": match.start(),
            "heading": heading,
            "paragraph": para_num,
            "location": f"paragraph {para_num}" + (f" under {heading}" if heading else ""),
        })
    return results


# Currency patterns
CURRENCY_PATTERNS = {
    "symbol_prefix": [
        # £500, $500, €500
        (r'[£$€]\s*\d+(?:,\d{3})*(?:\.\d{1,2})?', "symbol before number"),
    ],
    "code_suffix": [
        # 500 GBP, 500 USD, 500 EUR
        (r'\d+(?:,\d{3})*(?:\.\d{1,2})?\s*(?:GBP|USD|EUR|CAD|AUD|NZD)\b', "code after number"),
    ],
    "code_prefix": [
        # GBP500, USD500, EUR500
        (r'(?:GBP|USD|EUR|CAD|AUD|NZD)\s*\d+(?:,\d{3})*(?:\.\d{1,2})?', "code before number"),
    ],
}

# Number format patterns (for thousands)
NUMBER_PATTERNS = {
    "comma_separator": (r'\b\d{1,3}(?:,\d{3})+\b', "comma thousands separator"),
    "no_separator": (r'\b[1-9]\d{3,}\b', "no thousands separator"),  # 1000+ without commas
}

# Percentage patterns
PERCENTAGE_PATTERNS = {
    "no_space": (r'\d+(?:\.\d+)?%', "no space before %"),
    "with_space": (r'\d+(?:\.\d+)?\s+%', "space before %"),
    "word_percent": (r'\d+(?:\.\d+)?\s+percent\b', "word 'percent'"),
}

# Wagering multiplier patterns
WAGERING_PATTERNS = {
    "x_lower": (r'\d+x\b', "lowercase x"),
    "x_upper": (r'\d+X\b', "uppercase X"),
    "times_word": (r'\d+\s+times\b', "word 'times'"),
}

# Date format patterns (simplified detection)
DATE_PATTERNS = {
    "dmy_slash": (r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', "DD/MM/YYYY format"),
    "mdy_slash": (r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', "MM/DD/YYYY format"),  # Ambiguous
    "month_day": (r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}?\b', "Month Day format"),
    "day_month": (r'\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\b', "Day Month format"),
    "iso": (r'\b\d{4}-\d{2}-\d{2}\b', "ISO format"),
}


def find_all_matches(text: str, pattern: str) -> List[str]:
    """Find all matches of a pattern in text."""
    return re.findall(pattern, text, re.IGNORECASE)


def extract_headings(text: str) -> List[Tuple[str, str]]:
    """
    Extract headings from text and classify their capitalization.

    Returns list of (heading_text, cap_style) tuples where cap_style is:
    - "title_case": Most Words Capitalized
    - "sentence_case": Only first word capitalized
    - "all_caps": ALL CAPS
    - "mixed": Inconsistent or unclassifiable
    """
    headings = []

    # Try markdown-style headings
    md_headings = re.findall(r'^#{1,3}\s+(.+)$', text, re.MULTILINE)
    headings.extend(md_headings)

    # Try to find short lines that look like headings (followed by longer content)
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        # Skip empty lines and very long lines
        if not line or len(line) > 100:
            continue
        # Skip lines that look like regular sentences (end with period, etc.)
        if line.endswith('.') or line.endswith(','):
            continue
        # Check if next non-empty line is longer (content follows heading)
        for j in range(i+1, min(i+3, len(lines))):
            next_line = lines[j].strip()
            if next_line and len(next_line) > len(line) * 1.5:
                # This might be a heading
                if line not in headings:
                    headings.append(line)
                break

    # Classify each heading
    results = []
    for heading in headings:
        # Remove any markdown symbols
        clean = re.sub(r'^#+\s*', '', heading).strip()
        if not clean:
            continue

        words = clean.split()
        if not words:
            continue

        if clean.isupper():
            cap_style = "all_caps"
        else:
            # Count capitalized vs lowercase first letters
            caps = sum(1 for w in words if w[0].isupper())
            # Title case: most words capitalized (allowing for articles/prepositions)
            if caps >= len(words) * 0.7:
                cap_style = "title_case"
            elif caps == 1 and words[0][0].isupper():
                cap_style = "sentence_case"
            else:
                cap_style = "mixed"

        results.append((clean, cap_style))

    return results


def analyze_format_category(text: str, patterns: Dict[str, Tuple[str, str]]) -> Dict:
    """
    Analyze a format category and return counts and examples.

    Args:
        text: Article text
        patterns: Dict of {format_name: (regex_pattern, description)}

    Returns:
        Dict with counts, examples, and dominant format
    """
    analysis = {}
    total = 0

    for format_name, (pattern, description) in patterns.items():
        matches = find_all_matches(text, pattern)
        # Deduplicate
        unique_matches = list(set(matches))
        count = len(matches)
        total += count
        analysis[format_name] = {
            "count": count,
            "description": description,
            "examples": unique_matches[:3],  # First 3 unique examples
        }

    # Determine dominant format (most used)
    if total > 0:
        dominant = max(analysis.keys(), key=lambda k: analysis[k]["count"])
        analysis["_dominant"] = dominant if analysis[dominant]["count"] > 0 else None
    else:
        analysis["_dominant"] = None

    analysis["_total"] = total

    return analysis


class FormattingCheck(CheckModule):
    """
    Formatting consistency check.

    Detects when multiple formatting styles are used inconsistently
    within the same article.
    """

    name = "formatting"
    requires_llm = False
    requires_brief = False
    model_tier = "none"

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Check for formatting inconsistencies with exact location tracking.

        Args:
            article_text: Full article text to check
            brand_config: Brand style guide configuration (optional)

        Returns:
            CheckResult with formatting findings including precise locations
        """
        # Extract brand-specific formatting preferences (Phase 5)
        brand_config = kwargs.get('brand_config', {})
        formatting_rules = brand_config.get('formatting', {})
        preferred_currency = formatting_rules.get('currency_format', None)
        preferred_percentage = formatting_rules.get('percentage_format', None)
        preferred_wagering = formatting_rules.get('wagering_format', None)
        preferred_heading_cap = formatting_rules.get('heading_capitalization', None)

        findings = []
        format_analysis = {}

        # Parse headings for location tracking
        headings = parse_headings_with_positions(article_text)

        # 1. Check currency formats WITH LOCATIONS
        currency_by_style = {}
        for style_name, patterns in CURRENCY_PATTERNS.items():
            for pattern, description in patterns:
                matches = find_matches_with_locations(article_text, pattern, headings)
                if matches:
                    if style_name not in currency_by_style:
                        currency_by_style[style_name] = []
                    currency_by_style[style_name].extend(matches)

        currency_styles_used = [k for k in currency_by_style if currency_by_style[k]]
        if len(currency_styles_used) > 1:
            # Build detailed occurrence list
            occurrences = []
            for style in currency_styles_used:
                for m in currency_by_style[style][:3]:  # First 3 of each style
                    occurrences.append({
                        "text": m["match"],
                        "format": style.replace("_", " "),
                        "location": m["location"],
                    })

            dominant = max(currency_styles_used, key=lambda k: len(currency_by_style[k]))
            if preferred_currency:
                recommendation = f"Brand guide specifies '{preferred_currency.replace('_', ' ')}' format"
            else:
                recommendation = f"Use consistent format ({dominant.replace('_', ' ')} is most common)"

            findings.append({
                "type": "currency_inconsistency",
                "severity": "warning",
                "formats_found": currency_styles_used,
                "occurrences": occurrences,
                "recommendation": recommendation,
            })
        format_analysis["currency"] = {s: len(m) for s, m in currency_by_style.items()}

        # 2. Check number formats WITH LOCATIONS
        comma_matches = find_matches_with_locations(article_text, NUMBER_PATTERNS["comma_separator"][0], headings)
        no_sep_matches = find_matches_with_locations(article_text, NUMBER_PATTERNS["no_separator"][0], headings)

        if comma_matches and no_sep_matches:
            occurrences = []
            for m in comma_matches[:3]:
                occurrences.append({
                    "text": m["match"],
                    "format": "with comma (1,000)",
                    "location": m["location"],
                })
            for m in no_sep_matches[:3]:
                occurrences.append({
                    "text": m["match"],
                    "format": "without comma (1000)",
                    "location": m["location"],
                })

            findings.append({
                "type": "number_format_inconsistency",
                "severity": "warning",
                "formats_found": ["comma_separator", "no_separator"],
                "occurrences": occurrences,
                "recommendation": "Use consistent thousands separator format",
            })
        format_analysis["numbers"] = {
            "comma_separator": len(comma_matches),
            "no_separator": len(no_sep_matches),
        }

        # 3. Check percentage formats WITH LOCATIONS
        pct_by_style = {}
        for style_name, (pattern, desc) in PERCENTAGE_PATTERNS.items():
            matches = find_matches_with_locations(article_text, pattern, headings)
            if matches:
                pct_by_style[style_name] = matches

        pct_styles = [k for k in pct_by_style if pct_by_style[k]]
        if len(pct_styles) > 1:
            occurrences = []
            for style in pct_styles:
                for m in pct_by_style[style][:2]:
                    occurrences.append({
                        "text": m["match"],
                        "format": style.replace("_", " "),
                        "location": m["location"],
                    })

            dominant = max(pct_styles, key=lambda k: len(pct_by_style[k]))
            if preferred_percentage:
                recommendation = f"Brand guide specifies '{preferred_percentage.replace('_', ' ')}' format"
            else:
                recommendation = f"Use consistent percentage format ('{dominant}' is most common)"

            findings.append({
                "type": "percentage_inconsistency",
                "severity": "warning",
                "formats_found": pct_styles,
                "occurrences": occurrences,
                "recommendation": recommendation,
            })
        format_analysis["percentages"] = {s: len(m) for s, m in pct_by_style.items()}

        # 4. Check wagering formats WITH LOCATIONS
        wager_by_style = {}
        for style_name, (pattern, desc) in WAGERING_PATTERNS.items():
            matches = find_matches_with_locations(article_text, pattern, headings)
            if matches:
                wager_by_style[style_name] = matches

        wager_styles = [k for k in wager_by_style if wager_by_style[k]]
        if len(wager_styles) > 1:
            occurrences = []
            for style in wager_styles:
                for m in wager_by_style[style][:3]:
                    occurrences.append({
                        "text": m["match"],
                        "format": style.replace("_", " "),
                        "location": m["location"],
                    })

            if preferred_wagering:
                recommendation = f"Brand guide specifies '{preferred_wagering.replace('_', ' ')}' format"
            else:
                recommendation = "Use consistent wagering multiplier format (e.g., always '35x')"

            findings.append({
                "type": "wagering_format_inconsistency",
                "severity": "warning",
                "formats_found": wager_styles,
                "occurrences": occurrences,
                "recommendation": recommendation,
            })
        format_analysis["wagering"] = {s: len(m) for s, m in wager_by_style.items()}

        # 5. Check date formats WITH LOCATIONS
        date_by_style = {}
        for style_name, (pattern, desc) in DATE_PATTERNS.items():
            matches = find_matches_with_locations(article_text, pattern, headings)
            if matches:
                date_by_style[style_name] = matches

        # Only flag month_day vs day_month conflict
        if "month_day" in date_by_style and "day_month" in date_by_style:
            occurrences = []
            for m in date_by_style.get("month_day", [])[:2]:
                occurrences.append({
                    "text": m["match"],
                    "format": "Month Day (January 15)",
                    "location": m["location"],
                })
            for m in date_by_style.get("day_month", [])[:2]:
                occurrences.append({
                    "text": m["match"],
                    "format": "Day Month (15 January)",
                    "location": m["location"],
                })

            findings.append({
                "type": "date_format_inconsistency",
                "severity": "warning",
                "formats_found": ["month_day", "day_month"],
                "occurrences": occurrences,
                "recommendation": "Use consistent date format throughout",
            })
        format_analysis["dates"] = {s: len(m) for s, m in date_by_style.items()}

        # 6. Check heading capitalization WITH DETAILED FIXES
        extracted_headings = extract_headings(article_text)
        heading_styles = Counter(style for _, style in extracted_headings)
        format_analysis["heading_capitalization"] = dict(heading_styles)

        # Flag if multiple styles used (excluding 'mixed')
        main_styles = [s for s in heading_styles if s != "mixed" and heading_styles[s] > 0]
        if len(main_styles) > 1:
            dominant_style = max(main_styles, key=lambda s: heading_styles[s])
            target_style = preferred_heading_cap if preferred_heading_cap else dominant_style

            # Build list of non-conforming headings with suggested fixes
            non_conforming = []
            for heading_text, style in extracted_headings:
                if style != target_style and style != "mixed":
                    # Generate the corrected version
                    if target_style == "title_case":
                        # Title case: capitalize first letter of each word (except small words)
                        small_words = {'a', 'an', 'the', 'and', 'but', 'or', 'for', 'nor', 'on', 'at', 'to', 'by', 'in', 'of'}
                        words = heading_text.split()
                        corrected = ' '.join(
                            w.capitalize() if i == 0 or w.lower() not in small_words else w.lower()
                            for i, w in enumerate(words)
                        )
                    elif target_style == "sentence_case":
                        # Sentence case: only first word capitalized
                        corrected = heading_text.capitalize()
                    else:
                        corrected = heading_text

                    non_conforming.append({
                        "current": heading_text,
                        "current_style": style.replace("_", " "),
                        "suggested": corrected,
                        "target_style": target_style.replace("_", " "),
                    })

            if non_conforming:
                if preferred_heading_cap:
                    recommendation = f"Brand guide specifies '{preferred_heading_cap.replace('_', ' ')}'"
                else:
                    recommendation = f"Use consistent heading capitalization ({dominant_style.replace('_', ' ')} is most common)"

                findings.append({
                    "type": "heading_capitalization_inconsistency",
                    "severity": "warning",
                    "formats_found": main_styles,
                    "non_conforming_headings": non_conforming,
                    "recommendation": recommendation,
                })

        # Determine overall status
        if findings:
            status = "warn"
            issue_types = list(set(f["type"].replace("_inconsistency", "").replace("_", " ") for f in findings))
            summary = f"Inconsistent formatting: {', '.join(issue_types)}"
        else:
            status = "pass"
            summary = "Formatting is consistent"

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "findings": findings,
                "format_analysis": format_analysis,
            }
        )
