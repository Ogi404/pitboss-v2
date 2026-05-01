"""
Compliance check module for iGaming content.

Scans articles for regulatory compliance issues using regex patterns.
Uses a universal base approach - flags potential issues and lets the user
interpret based on their target market.
"""

import re
from typing import List, Dict, Tuple
from .base import CheckModule, CheckResult, WriterComment


# Responsible gambling patterns (case-insensitive)
RG_PATTERNS = [
    # Generic phrases
    r"gamble\s+responsibly",
    r"responsible\s+gambling",
    r"gambling\s+addiction",
    r"gambling\s+problem",
    r"problem\s+gambling",
    r"play\s+responsibly",
    # UK organizations
    r"begambleaware",
    r"gamcare",
    r"gamstop",
    r"gambling\s+commission",
    # Canada/International
    r"responsible\s+gaming",
    r"rg\s+council",
    r"gambling\s+therapy",
    r"national\s+council\s+on\s+problem\s+gambling",
    # Generic help references
    r"seek\s+help",
    r"gambling\s+help",
    r"if\s+you\s+or\s+someone",
]

# Age gate patterns
AGE_PATTERNS = [
    r"\b18\+",
    r"\b21\+",
    r"must\s+be\s+18",
    r"must\s+be\s+21",
    r"over\s+18",
    r"over\s+21",
    r"18\s+years",
    r"21\s+years",
    r"adults\s+only",
    r"legal\s+age",
]

# T&C reference patterns
TC_PATTERNS = [
    r"terms\s+and\s+conditions",
    r"t&cs?\s+apply",
    r"t&cs",
    r"terms\s+apply",
    r"wagering\s+requirements?",
    r"playthrough\s+requirements?",
    r"rollover\s+requirements?",
    r"bonus\s+terms",
    r"full\s+terms",
    r"conditions\s+apply",
]

# Bonus/offer patterns (to check for T&C nearby)
BONUS_PATTERNS = [
    r"bonus",
    r"free\s+spins?",
    r"welcome\s+offer",
    r"sign[\s-]?up\s+offer",
    r"deposit\s+match",
    r"no[\s-]?deposit",
    r"cashback",
    r"reload\s+bonus",
    r"loyalty\s+bonus",
    r"vip\s+bonus",
]

# Misleading claims - universal hard errors
MISLEADING_CLAIMS = [
    (r"guaranteed\s+win", "guaranteed win"),
    (r"no[\s-]?lose", "no-lose"),
    (r"100%\s+safe", "100% safe"),
    (r"sure\s+bet", "sure bet"),
    (r"can'?t\s+lose", "can't lose"),
    (r"always\s+win", "always win"),
    (r"certain\s+win", "certain win"),
    (r"winning\s+guaranteed", "winning guaranteed"),
]

# Regulatory banned phrases (market-specific warnings)
REGULATORY_BANNED = [
    {
        "pattern": r"risk[\s-]?free",
        "phrase": "risk-free",
        "markets": ["UKGC"],
        "note": "Banned by UKGC/ASA - may be acceptable in other markets",
    },
    {
        "pattern": r"free\s+money",
        "phrase": "free money",
        "markets": ["UKGC", "MGA"],
        "note": "Often considered misleading in regulated markets",
    },
]


def find_pattern_matches(text: str, pattern: str, context_chars: int = 40) -> List[Dict]:
    """Find all matches of a pattern with surrounding context."""
    matches = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        start = max(0, match.start() - context_chars)
        end = min(len(text), match.end() + context_chars)
        context = text[start:end]
        if start > 0:
            context = "..." + context
        if end < len(text):
            context = context + "..."
        matches.append({
            "matched_text": match.group(),
            "position": match.start(),
            "context": context,
        })
    return matches


def split_into_paragraphs(text: str) -> List[Tuple[int, str]]:
    """Split text into paragraphs with their starting positions."""
    paragraphs = []
    current_pos = 0
    # Split on double newlines or multiple line breaks
    parts = re.split(r'\n\s*\n', text)
    for part in parts:
        part = part.strip()
        if part:
            # Find actual position in original text
            pos = text.find(part, current_pos)
            if pos != -1:
                paragraphs.append((pos, part))
                current_pos = pos + len(part)
    return paragraphs


def check_tc_near_bonus(text: str) -> Dict:
    """
    Check if bonus mentions have T&C references nearby.
    Returns stats about coverage.
    """
    paragraphs = split_into_paragraphs(text)
    bonus_mentions = 0
    bonus_with_tc = 0
    uncovered_bonuses = []

    for para_idx, (para_pos, para_text) in enumerate(paragraphs):
        # Check if paragraph mentions bonuses
        has_bonus = False
        for pattern in BONUS_PATTERNS:
            if re.search(pattern, para_text, re.IGNORECASE):
                has_bonus = True
                break

        if has_bonus:
            bonus_mentions += 1
            # Check if T&C reference exists in same paragraph
            has_tc = False
            for tc_pattern in TC_PATTERNS:
                if re.search(tc_pattern, para_text, re.IGNORECASE):
                    has_tc = True
                    break

            if has_tc:
                bonus_with_tc += 1
            else:
                # Extract a snippet of the bonus mention
                for pattern in BONUS_PATTERNS:
                    match = re.search(pattern, para_text, re.IGNORECASE)
                    if match:
                        context = para_text[max(0, match.start()-20):min(len(para_text), match.end()+30)]
                        uncovered_bonuses.append({
                            "paragraph": para_idx + 1,
                            "context": context.strip(),
                        })
                        break

    return {
        "bonus_mentions": bonus_mentions,
        "bonus_with_tc": bonus_with_tc,
        "coverage_ratio": bonus_with_tc / bonus_mentions if bonus_mentions > 0 else 1.0,
        "uncovered_bonuses": uncovered_bonuses[:5],  # Limit to first 5
    }


class ComplianceCheck(CheckModule):
    """
    Compliance check for iGaming content.

    Scans for:
    - Responsible gambling mentions (warning if missing)
    - Age gate references (info level)
    - T&C coverage near bonus claims (warning if missing)
    - Misleading claims (error - universal)
    - Regulatory banned phrases (warning with market context)
    """

    name = "compliance"
    requires_llm = False
    requires_brief = False
    model_tier = "none"

    def run(self, article_text: str, brief_text: str = None, **kwargs) -> CheckResult:
        """
        Run compliance checks on article text.

        Args:
            article_text: Full article text to check
            brand_config: Brand style guide configuration (optional)

        Returns:
            CheckResult with compliance findings
        """
        # Extract brand-specific compliance settings (Phase 5)
        brand_config = kwargs.get('brand_config', {})
        compliance_rules = brand_config.get('compliance', {})
        market = compliance_rules.get('market', 'UKGC')  # Default to UKGC (strictest)
        require_age_gate = compliance_rules.get('require_age_gate', True)
        require_rg_mention = compliance_rules.get('require_rg_mention', True)
        require_tc_near_bonus = compliance_rules.get('require_tc_near_bonus', True)

        findings = []
        comments = []  # Phase 6: Collect WriterComment objects
        present = {
            "rg_mentions": [],
            "age_gates": [],
            "tc_references": [],
            "market": market,
        }

        # 1. Check for responsible gambling mentions
        rg_found = []
        for pattern in RG_PATTERNS:
            matches = find_pattern_matches(article_text, pattern)
            for m in matches:
                if m["matched_text"].lower() not in [r.lower() for r in rg_found]:
                    rg_found.append(m["matched_text"])
        present["rg_mentions"] = rg_found

        if not rg_found and require_rg_mention:
            findings.append({
                "type": "missing_rg",
                "severity": "warning",
                "message": f"No responsible gambling mention found (required for {market})",
                "suggestion": "Consider adding an RG statement appropriate for your target market",
                "markets_requiring": ["UKGC", "MGA", "Most jurisdictions"],
            })
            comments.append(WriterComment(
                id=f"compliance_{len(comments)}",
                check_name="compliance",
                severity="warning",
                anchor_text="",  # Document-level comment
                comment_text=f"MISSING: Responsible gambling reference. RULE: {market} market requires BeGambleAware, GamCare, or similar RG mention. ACTION: Add appropriate responsible gambling statement.",
                context=f"Brand compliance rule: require_rg_mention=true, market={market}"
            ))

        # 2. Check for age gate references
        age_found = []
        for pattern in AGE_PATTERNS:
            matches = find_pattern_matches(article_text, pattern)
            for m in matches:
                if m["matched_text"] not in age_found:
                    age_found.append(m["matched_text"])
        present["age_gates"] = age_found

        if not age_found and require_age_gate:
            findings.append({
                "type": "missing_age_gate",
                "severity": "info",
                "message": f"No age restriction reference found (required for {market})",
                "suggestion": "Consider adding age restriction for gambling content",
                "markets_requiring": ["Most jurisdictions"],
            })
            comments.append(WriterComment(
                id=f"compliance_{len(comments)}",
                check_name="compliance",
                severity="suggestion",
                anchor_text="",  # Document-level comment
                comment_text=f"MISSING: Age restriction reference (18+ or 21+). RULE: {market} market typically requires age gate. ACTION: Add age restriction statement.",
                context=f"Brand compliance rule: require_age_gate=true, market={market}"
            ))

        # 3. Check for T&C references
        tc_found = []
        for pattern in TC_PATTERNS:
            matches = find_pattern_matches(article_text, pattern)
            for m in matches:
                if m["matched_text"].lower() not in [t.lower() for t in tc_found]:
                    tc_found.append(m["matched_text"])
        present["tc_references"] = tc_found

        # 4. Check T&C coverage near bonus mentions
        tc_coverage = check_tc_near_bonus(article_text)
        if require_tc_near_bonus and tc_coverage["bonus_mentions"] > 0 and tc_coverage["coverage_ratio"] < 1.0:
            uncovered = tc_coverage["bonus_mentions"] - tc_coverage["bonus_with_tc"]
            findings.append({
                "type": "tc_coverage_gap",
                "severity": "warning",
                "message": f"{uncovered} of {tc_coverage['bonus_mentions']} bonus mentions lack nearby T&C reference",
                "suggestion": "Add T&C references near bonus/offer claims",
                "markets_requiring": ["UKGC", "MGA", "Most jurisdictions"],
                "examples": tc_coverage["uncovered_bonuses"],
            })
            # Create a comment for each uncovered bonus (up to 3)
            for bonus_info in tc_coverage["uncovered_bonuses"][:3]:
                comments.append(WriterComment(
                    id=f"compliance_{len(comments)}",
                    check_name="compliance",
                    severity="warning",
                    anchor_text=bonus_info["context"],
                    comment_text=f"MISSING: T&C reference near bonus claim. RULE: {market} requires T&C/wagering requirements near bonus mentions. ACTION: Add 'T&Cs apply' or wagering requirements near this claim.",
                    context=f"Paragraph {bonus_info['paragraph']}: {bonus_info['context'][:50]}..."
                ))

        # 5. Check for misleading claims (universal errors)
        misleading_found = 0
        for pattern, phrase in MISLEADING_CLAIMS:
            matches = find_pattern_matches(article_text, pattern)
            for m in matches:
                misleading_found += 1
                findings.append({
                    "type": "misleading_claim",
                    "severity": "error",
                    "phrase": phrase,
                    "matched_text": m["matched_text"],
                    "context": m["context"],
                    "message": f"Misleading claim '{phrase}' - banned in virtually all regulated markets",
                })
                comments.append(WriterComment(
                    id=f"compliance_{len(comments)}",
                    check_name="compliance",
                    severity="error",
                    anchor_text=m["matched_text"],
                    comment_text=f"BANNED PHRASE: '{phrase}' is misleading and prohibited in regulated markets. ACTION: Remove or rephrase this claim.",
                    context=m["context"]
                ))

        # 6. Check for regulatory banned phrases (market-specific warnings)
        banned_found = 0
        for rule in REGULATORY_BANNED:
            # Only flag if phrase is banned in the target market
            if market not in rule["markets"]:
                continue
            matches = find_pattern_matches(article_text, rule["pattern"])
            for m in matches:
                banned_found += 1
                findings.append({
                    "type": "banned_phrase",
                    "severity": "warning",
                    "phrase": rule["phrase"],
                    "matched_text": m["matched_text"],
                    "context": m["context"],
                    "message": f"'{rule['phrase']}' is banned in {market}",
                    "markets_requiring": rule["markets"],
                    "note": rule["note"],
                })
                comments.append(WriterComment(
                    id=f"compliance_{len(comments)}",
                    check_name="compliance",
                    severity="warning",
                    anchor_text=m["matched_text"],
                    comment_text=f"BANNED IN {market}: '{rule['phrase']}'. NOTE: {rule['note']}. ACTION: Remove or rephrase for {market} compliance.",
                    context=m["context"]
                ))

        # Calculate stats
        stats = {
            "rg_mention_count": len(rg_found),
            "age_gate_count": len(age_found),
            "tc_reference_count": len(tc_found),
            "bonus_mentions": tc_coverage["bonus_mentions"],
            "bonus_with_tc_nearby": tc_coverage["bonus_with_tc"],
            "misleading_claims_found": misleading_found,
            "banned_phrases_found": banned_found,
        }

        # Determine overall status
        errors = [f for f in findings if f["severity"] == "error"]
        warnings = [f for f in findings if f["severity"] == "warning"]

        if errors:
            status = "fail"
            summary = f"{len(errors)} compliance error(s): {', '.join(set(f['phrase'] for f in errors if 'phrase' in f))}"
        elif warnings:
            status = "warn"
            warning_types = []
            if any(f["type"] == "missing_rg" for f in warnings):
                warning_types.append("missing RG")
            if any(f["type"] == "tc_coverage_gap" for f in warnings):
                warning_types.append("T&C gaps")
            if any(f["type"] == "banned_phrase" for f in warnings):
                phrases = [f["phrase"] for f in warnings if f["type"] == "banned_phrase"]
                warning_types.append(f"'{phrases[0]}' found")
            summary = f"{len(warnings)} warning(s): {', '.join(warning_types)}"
        else:
            status = "pass"
            summary = "No compliance issues detected"

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                "findings": findings,
                "present": present,
                "stats": stats,
            },
            comments=comments  # Phase 6: Writer comments
        )
