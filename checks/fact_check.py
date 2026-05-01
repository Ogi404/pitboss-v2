"""
Fact-Check Module - Sheldon Integration

Verifies article claims against pre-crawled operator website content.
Adapted from the standalone Sheldon fact-checker with pre-crawl support.
"""

import json
import re
import uuid
from typing import Optional

from .base import CheckModule, CheckResult, WriterComment


# -----------------------------------------------------------------------------
# Fact-Check Categories (from Sheldon)
# Defined here to avoid importing from crawl module (Playwright dependency)
# -----------------------------------------------------------------------------

FACT_CHECK_CATEGORIES = {
    1: "Welcome Bonus & Promotions",
    2: "VIP/Loyalty Program",
    3: "Payment Methods",
    4: "Wagering Requirements & Bonus Terms",
    5: "Game Providers & Software",
    6: "Game Selection & Categories",
    7: "Mobile Compatibility",
    8: "Customer Support",
    9: "Licensing & Security",
    10: "Responsible Gambling",
    11: "Country/Currency Availability",
    12: "Withdrawal Limits & Processing Times",
    13: "General Brand Information",
}


# -----------------------------------------------------------------------------
# Sheldon System Prompt (adapted for pre-crawled content)
# -----------------------------------------------------------------------------

SHELDON_SYSTEM_PROMPT = '''You are Sheldon, a meticulous iGaming content fact-checker. Your job is to verify every claim in an article against actual operator website content that has been pre-crawled and provided to you.

CRITICAL: You are receiving pre-crawled, verified content from the operator's website. This content is current as of the crawl timestamp. Base ALL verification exclusively on this provided content. Do NOT use your training data for verification - the crawled content is your ONLY source of truth.

## Your Role

You verify:
1. **Names**: Operator names, game titles, provider names - exact spelling and formatting
2. **Numbers**: Bonus amounts, wagering requirements, withdrawal limits, processing times
3. **Claims**: Features, payment methods, licensing, support availability
4. **Details**: T&Cs, eligibility rules, country restrictions

## GEO Targeting

The article targets a specific geographic market. Verify all claims are accurate FOR THAT MARKET - bonuses, currencies, payment methods, and restrictions can vary by region.

## Verification Categories

Check claims in these categories (only verify categories requested by the user):

1. **Welcome Bonus & Promotions**: Bonus amounts, match percentages, free spins, bonus codes
2. **VIP/Loyalty Program**: Tier names, point systems, cashback rates, exclusive perks
3. **Payment Methods**: Available deposit/withdrawal methods for the target market
4. **Wagering Requirements & Bonus Terms**: Playthrough requirements, max bets, time limits, game contributions
5. **Game Providers & Software**: Provider names (exact spelling), exclusive providers
6. **Game Selection & Categories**: Game types, number of games, live casino offerings
7. **Mobile Compatibility**: App availability, mobile-optimized features
8. **Customer Support**: Support channels, hours, languages, response times
9. **Licensing & Security**: License numbers, regulatory bodies, security certifications
10. **Responsible Gambling**: Self-exclusion, deposit limits, cooling-off periods
11. **Country/Currency Availability**: Accepted countries, supported currencies
12. **Withdrawal Limits & Processing Times**: Min/max withdrawals, processing speeds
13. **General Brand Information**: Year established, operator name, parent company

## Output Format

Return your analysis as a JSON object with this structure:

```json
{
  "name_verification": [
    {
      "name_in_article": "The exact name as written in article",
      "correct_name": "The correct name from operator site (or same if correct)",
      "type": "operator|game|provider|brand",
      "status": "correct|incorrect|not_found",
      "note": "Brief explanation if incorrect or not found"
    }
  ],
  "category_claims": [
    {
      "category_id": 1,
      "category_name": "Welcome Bonus & Promotions",
      "claims": [
        {
          "claim": "The specific claim from the article",
          "article_says": "Exact quote from article",
          "source_says": "What the crawled content says (quote if possible)",
          "source_url": "URL where the information was found",
          "status": "compliant|non_compliant|not_verifiable",
          "severity": "critical|major|minor|cosmetic",
          "note": "Explanation of discrepancy or verification"
        }
      ]
    }
  ],
  "overall_compliance": {
    "total_claims": 0,
    "compliant": 0,
    "non_compliant": 0,
    "not_verifiable": 0,
    "critical_issues": 0,
    "recommendation": "Brief overall assessment"
  }
}
```

## Severity Guidelines

- **Critical**: Factually wrong numbers (bonus amounts, wagering requirements), wrong licensing info, wrong payment methods
- **Major**: Incorrect game/provider names, wrong support hours, misleading promotional claims
- **Minor**: Slight wording differences, outdated minor details, incomplete information
- **Cosmetic**: Formatting differences, capitalization, minor stylistic choices

## Important Rules

1. If a claim CANNOT be verified from the provided crawled content, mark it "not_verifiable" - do NOT guess
2. Quote the source content when possible to show evidence
3. Always include the source URL where information was found
4. Be precise - "200% up to £500" is different from "200% up to £200"
5. Consider the target GEO - a claim may be true for one market but false for another
6. For name verification, check EXACT spelling including capitalization
'''


# -----------------------------------------------------------------------------
# FactCheckModule
# -----------------------------------------------------------------------------

class FactCheckModule(CheckModule):
    """
    Fact-check module that verifies article claims against crawled operator content.

    Uses GPT-4.1 (full tier) for precise verification against pre-crawled content.
    """
    name = "fact_check"
    requires_llm = True
    requires_brief = False
    model_tier = "full"

    def run(
        self,
        article_text: str,
        brief_data=None,
        **kwargs
    ) -> CheckResult:
        """
        Verify article claims against pre-crawled operator content.

        Required kwargs:
            crawl_result: CrawlResult object from site_crawler
            geo_target: str - Target market (e.g., "Canada (CAD)")
            categories: list[int] - Category IDs to verify (1-13)

        Returns:
            CheckResult with fact-check findings
        """
        crawl_result = kwargs.get('crawl_result')
        geo_target = kwargs.get('geo_target', '')
        categories = kwargs.get('categories', [1, 3, 4, 6, 9, 12])  # Default categories

        if not crawl_result or not crawl_result.pages:
            return CheckResult(
                name=self.name,
                status="error",
                summary="No crawled content available for fact-checking",
                details={"error": "crawl_result is empty or missing"}
            )

        # Build context from crawled pages
        crawl_context = self._build_crawl_context(crawl_result)

        # Build category list
        category_names = [FACT_CHECK_CATEGORIES.get(c, f"Category {c}") for c in categories]

        # Build the prompt
        user_prompt = self._build_user_prompt(
            article_text=article_text,
            crawl_context=crawl_context,
            geo_target=geo_target,
            categories=categories,
            category_names=category_names,
            crawl_result=crawl_result,
        )

        # Call LLM
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SHELDON_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            result_data = json.loads(result_text)

        except json.JSONDecodeError as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary=f"Failed to parse fact-check response: {e}",
                details={"error": str(e), "raw_response": result_text if 'result_text' in dir() else None}
            )
        except Exception as e:
            return CheckResult(
                name=self.name,
                status="error",
                summary=f"Fact-check failed: {e}",
                details={"error": str(e)}
            )

        # Process results
        return self._process_results(result_data, crawl_result)

    def _build_crawl_context(self, crawl_result) -> str:
        """Build formatted context from crawled pages."""
        sections = []

        # Group pages by type
        pages_by_type = {}
        for page in crawl_result.pages:
            page_type = page.page_type
            if page_type not in pages_by_type:
                pages_by_type[page_type] = []
            pages_by_type[page_type].append(page)

        # Priority order for sections
        type_order = ['legal', 'bonus_terms', 'payments', 'promotions', 'vip', 'rg', 'games', 'faq', 'general']

        for page_type in type_order:
            if page_type not in pages_by_type:
                continue

            section_title = page_type.replace('_', ' ').title()
            sections.append(f"\n=== {section_title} ===\n")

            for page in pages_by_type[page_type]:
                sections.append(f"\n--- {page.title} ({page.url}) ---\n")
                # Truncate very long content
                content = page.content[:8000] if len(page.content) > 8000 else page.content
                sections.append(content)

        return "\n".join(sections)

    def _build_user_prompt(
        self,
        article_text: str,
        crawl_context: str,
        geo_target: str,
        categories: list,
        category_names: list,
        crawl_result,
    ) -> str:
        """Build the user prompt for fact-checking."""
        prompt = f"""## Fact-Check Request

**Target Market**: {geo_target}
**Operator Domain**: {crawl_result.domain}
**Crawl Timestamp**: {crawl_result.crawl_timestamp}
**Categories to Verify**: {', '.join(f'{c}. {n}' for c, n in zip(categories, category_names))}

## Pre-Crawled Operator Content

The following content was crawled from the operator's website. This is your ONLY source of truth for verification:

{crawl_context}

## Article to Verify

Verify the following article against the crawled content above:

{article_text}

## Instructions

1. Extract ALL verifiable claims from the article related to the requested categories
2. Check each claim against the pre-crawled content
3. For names (operator, games, providers), verify exact spelling
4. Return your analysis in the specified JSON format
5. If something cannot be verified from the provided content, mark it "not_verifiable"

Return your JSON analysis:
"""
        return prompt

    def _process_results(self, result_data: dict, crawl_result) -> CheckResult:
        """Process LLM results into CheckResult with WriterComments."""
        name_verification = result_data.get('name_verification', [])
        category_claims = result_data.get('category_claims', [])
        overall = result_data.get('overall_compliance', {})

        # Calculate status
        critical_issues = overall.get('critical_issues', 0)
        non_compliant = overall.get('non_compliant', 0)

        if critical_issues > 0:
            status = "fail"
        elif non_compliant > 0:
            status = "warn"
        else:
            status = "pass"

        # Build summary
        total = overall.get('total_claims', 0)
        compliant = overall.get('compliant', 0)
        summary = f"Verified {total} claims: {compliant} compliant, {non_compliant} issues"
        if critical_issues > 0:
            summary += f" ({critical_issues} critical)"

        # Generate WriterComments for non-compliant findings
        comments = self._generate_comments(name_verification, category_claims)

        # Calculate score
        if total > 0:
            score = int((compliant / total) * 100)
        else:
            score = 100

        return CheckResult(
            name=self.name,
            status=status,
            summary=summary,
            details={
                'name_verification': name_verification,
                'category_claims': category_claims,
                'overall_compliance': overall,
                'coverage_log': crawl_result.crawl_log,
                'domain': crawl_result.domain,
                'crawl_timestamp': crawl_result.crawl_timestamp,
            },
            comments=comments,
            score=score,
        )

    def _generate_comments(
        self,
        name_verification: list,
        category_claims: list
    ) -> list:
        """Generate WriterComment objects for non-compliant findings."""
        comments = []

        # Comments for incorrect names
        for item in name_verification:
            if item.get('status') == 'incorrect':
                comment_id = f"fc_name_{uuid.uuid4().hex[:8]}"
                anchor = item.get('name_in_article', '')
                correct = item.get('correct_name', '')
                note = item.get('note', '')

                comments.append(WriterComment(
                    id=comment_id,
                    check_name=self.name,
                    severity="warning",
                    anchor_text=anchor,
                    comment_text=f"FACT CHECK: Name spelling issue. Article says '{anchor}' but operator site uses '{correct}'. {note}",
                    context=f"Correct spelling: {correct}",
                ))

        # Comments for non-compliant claims
        for category in category_claims:
            for claim in category.get('claims', []):
                if claim.get('status') == 'non_compliant':
                    comment_id = f"fc_claim_{uuid.uuid4().hex[:8]}"

                    severity_map = {
                        'critical': 'error',
                        'major': 'error',
                        'minor': 'warning',
                        'cosmetic': 'suggestion',
                    }
                    severity = severity_map.get(claim.get('severity', 'minor'), 'warning')

                    article_says = claim.get('article_says', '')
                    source_says = claim.get('source_says', '')
                    source_url = claim.get('source_url', '')
                    note = claim.get('note', '')

                    # Use article quote as anchor if available
                    anchor = article_says if len(article_says) < 100 else article_says[:97] + '...'

                    comment_text = (
                        f"FACT CHECK: {claim.get('claim', 'Claim discrepancy')}. "
                        f"ARTICLE SAYS: \"{article_says}\". "
                        f"SOURCE SAYS: \"{source_says}\". "
                        f"URL: {source_url}. "
                        f"ACTION: Verify and correct this information."
                    )

                    comments.append(WriterComment(
                        id=comment_id,
                        check_name=self.name,
                        severity=severity,
                        anchor_text=anchor,
                        comment_text=comment_text,
                        context=f"Source: {source_url}",
                    ))

        return comments
