"""
Readability check module.

Analyzes text complexity and structure metrics:
- Sentence length (flags >40 words)
- Paragraph density (flags >150 words without subheading)
- Total word count
- Keyword density (flags >3%)

Now includes exact location tracking for all issues.
"""

import re
from typing import List, Dict, Optional, Union, Tuple
from .base import CheckModule, CheckResult

# Import BriefData for type hints
try:
    from brief_parser import BriefData
except ImportError:
    BriefData = None


# Thresholds
MAX_SENTENCE_WORDS = 40
MAX_PARAGRAPH_WORDS = 150
KEYWORD_DENSITY_THRESHOLD = 3.0  # percentage


# -----------------------------------------------------------------------------
# Heading/Section Tracking
# -----------------------------------------------------------------------------

def parse_headings_with_positions(text: str) -> List[Dict]:
    """
    Parse all headings from text with their character positions.
    Returns list of {text, level, start_pos, end_pos} dicts.
    """
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


def split_sentences(text: str) -> List[str]:
    """
    Split text into sentences.

    Uses regex to split on sentence-ending punctuation followed by
    space and capital letter (or end of text).
    """
    # First, normalize whitespace
    normalized = re.sub(r'\s+', ' ', text.strip())

    # Split on sentence boundaries
    pattern = r'(?<=[.!?])\s+(?=[A-Z"\'])'
    sentences = re.split(pattern, normalized)

    # Filter out empty sentences and strip whitespace
    sentences = [s.strip() for s in sentences if s.strip()]

    return sentences


def split_sentences_with_positions(text: str) -> List[Tuple[str, int]]:
    """
    Split text into sentences with their character positions.

    Returns list of (sentence_text, start_position) tuples.
    """
    sentences = []

    # Find sentences by matching sentence-ending punctuation
    pattern = r'[^.!?]*[.!?]+'
    for match in re.finditer(pattern, text):
        sentence = match.group().strip()
        if sentence and len(sentence) > 10:  # Skip very short fragments
            sentences.append((sentence, match.start()))

    return sentences


def split_paragraphs(text: str, headings: List[Dict] = None) -> List[Dict]:
    """
    Split text into paragraphs with metadata and heading context.

    Returns list of dicts with paragraph text, word count, position,
    and nearest heading info.
    """
    paragraphs = []

    # Find paragraph boundaries using regex to track positions
    para_pattern = r'(?:^|\n\s*\n)([^\n](?:[^\n]*\n?)*?)(?=\n\s*\n|$)'

    current_pos = 0
    para_num = 0

    for match in re.finditer(r'(?:^|\n\n)(.+?)(?=\n\n|$)', text, re.DOTALL):
        part = match.group(1).strip()
        if not part or len(part) < 20:  # Skip very short fragments
            continue

        para_num += 1
        words = part.split()
        word_count = len(words)
        start_pos = match.start(1)

        # Find nearest heading using position
        heading_context = None
        if headings:
            heading_context = find_nearest_heading(headings, start_pos)

        paragraphs.append({
            "number": para_num,
            "text": part,
            "word_count": word_count,
            "start_pos": start_pos,
            "heading_context": heading_context,
            "preview": part[:100] + "..." if len(part) > 100 else part,
        })

    # Fallback: simple split if regex didn't find enough
    if len(paragraphs) < 2:
        paragraphs = []
        parts = re.split(r'\n\s*\n', text)
        current_pos = 0
        para_num = 0

        for part in parts:
            part = part.strip()
            if not part:
                current_pos += 2  # Account for the newlines
                continue

            para_num += 1
            words = part.split()
            word_count = len(words)

            heading_context = None
            if headings:
                heading_context = find_nearest_heading(headings, current_pos)

            paragraphs.append({
                "number": para_num,
                "text": part,
                "word_count": word_count,
                "start_pos": current_pos,
                "heading_context": heading_context,
                "preview": part[:100] + "..." if len(part) > 100 else part,
            })

            current_pos += len(part) + 2

    return paragraphs


def calculate_keyword_density(text: str, keywords: List[str], threshold: float = None) -> Dict[str, Dict]:
    """
    Calculate density of each keyword in the text.

    Args:
        text: Article text
        keywords: List of keyword strings
        threshold: Keyword density threshold percentage (default uses module constant)

    Returns:
        Dict of {keyword: {count, density, flagged}}
    """
    if threshold is None:
        threshold = KEYWORD_DENSITY_THRESHOLD
    total_words = len(text.split())
    if total_words == 0:
        return {}

    densities = {}
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue

        keyword = keyword.strip().lower()
        # Count occurrences (case-insensitive)
        pattern = re.escape(keyword)
        count = len(re.findall(pattern, text, re.IGNORECASE))

        # Calculate density as percentage
        # For multi-word keywords, count each occurrence as contributing
        # the number of words in the keyword
        keyword_words = len(keyword.split())
        word_contribution = count * keyword_words
        density = (word_contribution / total_words) * 100 if total_words > 0 else 0

        densities[keyword] = {
            "count": count,
            "density": round(density, 2),
            "flagged": density > threshold,
        }

    return densities


class ReadabilityCheck(CheckModule):
    """
    Readability analysis check.

    Analyzes text complexity metrics and flags:
    - Sentences over 40 words
    - Paragraphs over 150 words
    - Keywords with >3% density (potential stuffing)
    """

    name = "readability"
    requires_llm = False
    requires_brief = True  # For keyword density check
    model_tier = "none"

    def run(self, article_text: str, brief_data: Union['BriefData', str] = None, **kwargs) -> CheckResult:
        """
        Analyze readability of article text with exact location tracking.

        Args:
            article_text: Full article text
            brief_data: BriefData object (for keyword density) or None
            brand_config: Brand style guide configuration (optional)

        Returns:
            CheckResult with readability findings including precise locations
        """
        # Extract brand-specific thresholds (Phase 5)
        brand_config = kwargs.get('brand_config', {})
        structure_rules = brand_config.get('structure', {})
        max_sentence_words = structure_rules.get('max_sentence_words', MAX_SENTENCE_WORDS)
        max_paragraph_words = structure_rules.get('max_paragraph_words', MAX_PARAGRAPH_WORDS)

        seo_rules = brand_config.get('seo', {})
        keyword_density_threshold = seo_rules.get('max_keyword_density_percent', KEYWORD_DENSITY_THRESHOLD)

        findings = []

        # Parse headings for location tracking
        headings = parse_headings_with_positions(article_text)

        # 1. Analyze sentences WITH LOCATION CONTEXT
        sentences_with_pos = split_sentences_with_positions(article_text)
        total_sentences = len(sentences_with_pos)

        long_sentences = []
        for i, (sentence, start_pos) in enumerate(sentences_with_pos):
            word_count = len(sentence.split())
            if word_count > max_sentence_words:
                # Find the heading this sentence is under
                heading_context = find_nearest_heading(headings, start_pos)
                location = f"sentence {i + 1}"
                if heading_context:
                    location += f" under {heading_context}"

                # Truncate for display (first 80 chars + ...)
                display_text = sentence[:80] + "..." if len(sentence) > 80 else sentence

                long_sentences.append({
                    "sentence_num": i + 1,
                    "word_count": word_count,
                    "text": display_text,
                    "full_text": sentence[:200] + "..." if len(sentence) > 200 else sentence,
                    "location": location,
                    "heading": heading_context,
                })
                findings.append({
                    "type": "long_sentence",
                    "severity": "warning",
                    "word_count": word_count,
                    "text": display_text,
                    "location": location,
                    "message": f"Sentence has {word_count} words (max recommended: {max_sentence_words})",
                })

        # Calculate average from all sentences
        sentence_lengths = [len(s.split()) for s, _ in sentences_with_pos]
        avg_sentence_length = sum(sentence_lengths) / total_sentences if total_sentences > 0 else 0

        # 2. Analyze paragraphs WITH HEADING CONTEXT
        paragraphs = split_paragraphs(article_text, headings)
        total_paragraphs = len(paragraphs)

        dense_paragraphs = []
        for para in paragraphs:
            if para["word_count"] > max_paragraph_words:
                # Build location string with heading context
                location = f"paragraph {para['number']}"
                if para["heading_context"]:
                    location += f" under {para['heading_context']}"

                dense_paragraphs.append({
                    "number": para["number"],
                    "word_count": para["word_count"],
                    "preview": para["preview"],
                    "location": location,
                    "heading": para["heading_context"],
                })
                findings.append({
                    "type": "dense_paragraph",
                    "severity": "warning",
                    "word_count": para["word_count"],
                    "location": location,
                    "preview": para["preview"],
                    "message": f"Paragraph has {para['word_count']} words without subheading break (max recommended: {max_paragraph_words})",
                })

        # 3. Calculate word count stats
        total_words = len(article_text.split())

        # 4. Keyword density analysis (if keywords available)
        keyword_density = {}
        if brief_data:
            # Extract keyword list from BriefData
            keywords = []
            if hasattr(brief_data, 'keywords'):
                keywords = [kw.keyword for kw in brief_data.keywords if hasattr(kw, 'keyword')]
            elif hasattr(brief_data, 'get_keywords_by_group'):
                kw_groups = brief_data.get_keywords_by_group()
                for group in kw_groups.values():
                    for kw_item in group:
                        if isinstance(kw_item, dict) and 'keyword' in kw_item:
                            keywords.append(kw_item['keyword'])

            if keywords:
                keyword_density = calculate_keyword_density(article_text, keywords, keyword_density_threshold)

                # Flag keywords with high density
                for keyword, data in keyword_density.items():
                    if data["flagged"]:
                        findings.append({
                            "type": "keyword_stuffing",
                            "severity": "warning",
                            "keyword": keyword,
                            "count": data["count"],
                            "density": data["density"],
                            "message": f"Keyword '{keyword}' has {data['density']}% density (threshold: {keyword_density_threshold}%)",
                        })

        # Compile stats
        stats = {
            "total_words": total_words,
            "total_sentences": total_sentences,
            "avg_sentence_length": round(avg_sentence_length, 1),
            "total_paragraphs": total_paragraphs,
            "long_sentences_count": len(long_sentences),
            "dense_paragraphs_count": len(dense_paragraphs),
            "keyword_density_flags": sum(1 for k, v in keyword_density.items() if v["flagged"]),
        }

        # Determine overall status
        warnings = [f for f in findings if f["severity"] == "warning"]

        if len(warnings) >= 5:
            status = "fail"
            summary = f"Multiple readability issues: {len(long_sentences)} long sentences, {len(dense_paragraphs)} dense paragraphs"
        elif warnings:
            status = "warn"
            issues = []
            if long_sentences:
                issues.append(f"{len(long_sentences)} long sentence(s)")
            if dense_paragraphs:
                issues.append(f"{len(dense_paragraphs)} dense paragraph(s)")
            if any(f["type"] == "keyword_stuffing" for f in findings):
                issues.append("keyword density warning")
            summary = ", ".join(issues)
        else:
            status = "pass"
            summary = f"Good readability (avg {round(avg_sentence_length, 0)} words/sentence, {total_words} total words)"

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "findings": findings,
                "stats": stats,
                "keyword_density": keyword_density,
                "long_sentences": long_sentences[:10],  # Limit for display
                "dense_paragraphs": dense_paragraphs[:10],
            }
        )
