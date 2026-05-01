"""
Word count check module.

Splits article into sections by heading and reports word counts
per section. Can compare against brief targets if HeadingSpec data
is available.
"""

import re
from typing import List, Dict, Optional, Union, Tuple
from .base import CheckModule, CheckResult, WriterComment

# Import BriefData for type hints
try:
    from brief_parser import BriefData
except ImportError:
    BriefData = None


# Variance thresholds for flagging
UNDER_THRESHOLD = 0.8   # Flag if <80% of target min
OVER_THRESHOLD = 1.3    # Flag if >130% of target max


def detect_headings(text: str) -> List[Dict]:
    """
    Detect headings in text and return their positions and levels.

    Supports:
    - Markdown headings (# H1, ## H2, ### H3)
    - All-caps lines (likely H1/H2)
    - Title case short lines followed by content

    Returns:
        List of {text, level, position, line_num}
    """
    headings = []
    lines = text.split('\n')
    line_pos = 0  # Track character position

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not stripped:
            line_pos += len(line) + 1
            continue

        heading_info = None

        # Check for markdown headings
        md_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if md_match:
            level_num = len(md_match.group(1))
            heading_text = md_match.group(2).strip()
            heading_info = {
                "text": heading_text,
                "level": f"H{level_num}",
                "position": line_pos,
                "line_num": i + 1,
            }
        # Check for ALL CAPS lines (potential H1/H2)
        elif stripped.isupper() and len(stripped) > 3 and len(stripped) < 80:
            # Avoid matching ALL CAPS words in regular sentences
            # by checking if next line is longer/different
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.isupper() and len(next_line) > len(stripped):
                    heading_info = {
                        "text": stripped,
                        "level": "H2",  # Assume H2 for all-caps
                        "position": line_pos,
                        "line_num": i + 1,
                    }
        # Check for Title Case short lines
        elif len(stripped) < 80 and not stripped.endswith('.') and not stripped.endswith(','):
            words = stripped.split()
            if len(words) >= 2:
                # Check if most words start with capital
                caps = sum(1 for w in words if w[0].isupper())
                if caps >= len(words) * 0.6:
                    # Check if next non-empty line looks like content
                    for j in range(i + 1, min(i + 3, len(lines))):
                        next_line = lines[j].strip()
                        if next_line and len(next_line) > len(stripped):
                            heading_info = {
                                "text": stripped,
                                "level": "H2",
                                "position": line_pos,
                                "line_num": i + 1,
                            }
                            break

        if heading_info:
            headings.append(heading_info)

        line_pos += len(line) + 1

    return headings


def split_by_headings(text: str, headings: List[Dict]) -> List[Dict]:
    """
    Split text into sections based on detected headings.

    Returns:
        List of {heading, level, content, word_count}
    """
    if not headings:
        # No headings found - treat entire text as one section
        word_count = len(text.split())
        return [{
            "heading": "(No heading)",
            "level": None,
            "content": text[:500] + "..." if len(text) > 500 else text,
            "word_count": word_count,
        }]

    sections = []

    for i, heading in enumerate(headings):
        start_pos = heading["position"]

        # Find end position (start of next heading, or end of text)
        if i + 1 < len(headings):
            end_pos = headings[i + 1]["position"]
        else:
            end_pos = len(text)

        # Extract section content (excluding the heading line itself)
        heading_line_end = text.find('\n', start_pos)
        if heading_line_end == -1:
            heading_line_end = start_pos + len(heading["text"])

        section_content = text[heading_line_end:end_pos].strip()
        word_count = len(section_content.split())

        sections.append({
            "heading": heading["text"],
            "level": heading["level"],
            "content": section_content[:300] + "..." if len(section_content) > 300 else section_content,
            "word_count": word_count,
        })

    return sections


def normalize_heading(heading: str) -> str:
    """
    Normalize heading text for fuzzy matching.

    Removes numbering, punctuation, extra whitespace, and lowercases.
    """
    # Remove leading numbers/bullets (e.g., "1. Introduction" -> "Introduction")
    heading = re.sub(r'^[\d.)\-]+\s*', '', heading)
    # Remove markdown symbols
    heading = re.sub(r'^#+\s*', '', heading)
    # Remove punctuation
    heading = re.sub(r'[^\w\s]', '', heading)
    # Lowercase and strip
    return heading.lower().strip()


def fuzzy_match_heading(section_heading: str, target_headings: List[str]) -> Optional[Tuple[str, float]]:
    """
    Find best matching target heading for a section heading.

    Returns:
        (matched_target, confidence_score) or None if no good match
    """
    normalized_section = normalize_heading(section_heading)

    best_match = None
    best_score = 0.0

    for target in target_headings:
        normalized_target = normalize_heading(target)

        # Exact match after normalization
        if normalized_section == normalized_target:
            return (target, 1.0)

        # Check if one contains the other
        if normalized_section in normalized_target or normalized_target in normalized_section:
            # Score based on length overlap
            overlap = min(len(normalized_section), len(normalized_target))
            max_len = max(len(normalized_section), len(normalized_target))
            score = overlap / max_len if max_len > 0 else 0

            if score > best_score and score > 0.5:
                best_match = target
                best_score = score

        # Check word overlap
        section_words = set(normalized_section.split())
        target_words = set(normalized_target.split())
        if section_words and target_words:
            overlap = len(section_words & target_words)
            total = len(section_words | target_words)
            score = overlap / total if total > 0 else 0

            if score > best_score and score > 0.5:
                best_match = target
                best_score = score

    if best_match and best_score > 0.5:
        return (best_match, best_score)
    return None


class WordCountCheck(CheckModule):
    """
    Word count per section check.

    Splits article by headings, counts words per section,
    and compares against brief targets if available.
    """

    name = "word_counts"
    requires_llm = False
    requires_brief = True
    model_tier = "none"

    def run(self, article_text: str, brief_data: Union['BriefData', str] = None, **kwargs) -> CheckResult:
        """
        Analyze word counts per section.

        Args:
            article_text: Full article text
            brief_data: BriefData object (for heading targets) or None

        Returns:
            CheckResult with per-section word counts
        """
        findings = []
        comments = []  # Phase 6: Collect WriterComment objects

        # 1. Detect headings
        headings = detect_headings(article_text)

        # 2. Split into sections
        sections = split_by_headings(article_text, headings)

        # 3. Extract heading targets from brief if available
        # Note: Current BriefData doesn't have HeadingSpec, so this is
        # forward-compatible for when that's added
        heading_targets = {}
        if brief_data and hasattr(brief_data, 'headings'):
            for spec in brief_data.headings:
                if hasattr(spec, 'text') and hasattr(spec, 'word_count_min'):
                    heading_targets[spec.text] = {
                        "min": getattr(spec, 'word_count_min', None),
                        "max": getattr(spec, 'word_count_max', None),
                    }

        # 4. Analyze each section
        section_results = []
        sections_ok = 0
        sections_under = 0
        sections_over = 0
        sections_no_target = 0

        for section in sections:
            result = {
                "heading": section["heading"],
                "level": section["level"],
                "word_count": section["word_count"],
                "target_min": None,
                "target_max": None,
                "status": "no_target",
                "variance": None,
            }

            # Try to match to a target
            if heading_targets:
                match = fuzzy_match_heading(section["heading"], list(heading_targets.keys()))
                if match:
                    target_heading, confidence = match
                    target = heading_targets[target_heading]
                    result["target_min"] = target["min"]
                    result["target_max"] = target["max"]
                    result["matched_to"] = target_heading
                    result["match_confidence"] = confidence

                    # Calculate status
                    if target["min"] is not None and section["word_count"] < target["min"] * UNDER_THRESHOLD:
                        result["status"] = "under"
                        result["variance"] = round((section["word_count"] / target["min"] - 1) * 100)
                        sections_under += 1

                        findings.append({
                            "type": "section_under_target",
                            "severity": "warning",
                            "heading": section["heading"],
                            "word_count": section["word_count"],
                            "target_min": target["min"],
                            "variance": result["variance"],
                            "message": f"Section '{section['heading']}' has {section['word_count']} words ({result['variance']}% below target min of {target['min']})",
                        })
                        comments.append(WriterComment(
                            id=f"word_counts_{len(comments)}",
                            check_name="word_counts",
                            severity="warning",
                            anchor_text=section["heading"],
                            comment_text=f"SECTION TOO SHORT: '{section['heading']}' has {section['word_count']} words, but brief requires minimum {target['min']} words. ACTION: Add {target['min'] - section['word_count']} more words to this section.",
                            context=f"Current: {section['word_count']} words, Target: {target['min']}-{target['max']} words"
                        ))

                    elif target["max"] is not None and section["word_count"] > target["max"] * OVER_THRESHOLD:
                        result["status"] = "over"
                        result["variance"] = round((section["word_count"] / target["max"] - 1) * 100)
                        sections_over += 1

                        findings.append({
                            "type": "section_over_target",
                            "severity": "warning",
                            "heading": section["heading"],
                            "word_count": section["word_count"],
                            "target_max": target["max"],
                            "variance": result["variance"],
                            "message": f"Section '{section['heading']}' has {section['word_count']} words ({result['variance']}% above target max of {target['max']})",
                        })
                        comments.append(WriterComment(
                            id=f"word_counts_{len(comments)}",
                            check_name="word_counts",
                            severity="warning",
                            anchor_text=section["heading"],
                            comment_text=f"SECTION TOO LONG: '{section['heading']}' has {section['word_count']} words, but brief allows maximum {target['max']} words. ACTION: Trim {section['word_count'] - target['max']} words from this section.",
                            context=f"Current: {section['word_count']} words, Target: {target['min']}-{target['max']} words"
                        ))
                    else:
                        result["status"] = "ok"
                        sections_ok += 1
                else:
                    sections_no_target += 1
            else:
                sections_no_target += 1

            section_results.append(result)

        # 5. Calculate total word count
        total_words = sum(s["word_count"] for s in sections)

        # 6. Check for very short sections (potential incomplete content)
        for section in sections:
            if section["word_count"] < 20 and section["level"] in ["H2", "H3"]:
                findings.append({
                    "type": "very_short_section",
                    "severity": "info",
                    "heading": section["heading"],
                    "word_count": section["word_count"],
                    "message": f"Section '{section['heading']}' is very short ({section['word_count']} words) - may be incomplete",
                })

        # Stats
        stats = {
            "total_sections": len(sections),
            "total_words": total_words,
            "sections_ok": sections_ok,
            "sections_under": sections_under,
            "sections_over": sections_over,
            "sections_no_target": sections_no_target,
            "has_targets": len(heading_targets) > 0,
        }

        # Determine status
        if sections_under > 0 or sections_over > 0:
            status = "warn"
            issues = []
            if sections_under > 0:
                issues.append(f"{sections_under} under target")
            if sections_over > 0:
                issues.append(f"{sections_over} over target")
            summary = f"{len(sections)} sections, {', '.join(issues)}"
        else:
            status = "pass"
            if heading_targets:
                summary = f"All {sections_ok} sections within target range ({total_words} total words)"
            else:
                summary = f"{len(sections)} sections detected ({total_words} total words)"

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "findings": findings,
                "sections": section_results,
                "stats": stats,
            },
            comments=comments  # Phase 6: Writer comments
        )
