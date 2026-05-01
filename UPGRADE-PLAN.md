# Pitboss v2 → v3 Upgrade Plan

## Context for Claude Code

This is a Flask-based iGaming content proofreading and SEO analysis tool. It takes an article (DOCX or Google Doc) and a task brief (DOCX or Excel), runs AI-powered checks, and returns a report with corrections that can be applied back to Google Docs.

**Current codebase** (all in repo root):
- `app.py` — monolith: routes, prompts, all business logic (~790 lines)
- `config.py` — env var loading
- `google_auth.py` — OAuth flow helpers
- `google_docs.py` — Google Docs API: fetch, position tracking, apply corrections
- `templates/` — 4 Jinja2 HTML templates (form, results, proofread_report, apply_confirm)
- `requirements.txt`

**Stack**: Flask, OpenAI GPT-4.1, Google APIs, python-docx, pandas. Runs locally on Windows (started via `start.bat`).

**Owner's workflow**: Receives iGaming editing tasks in Notion. Each task has a brief (Google Sheet or Google Doc) and an article (Google Doc). Uses this tool to check grammar, keywords, headings, and content compliance against the brief. Applies corrections directly to the Google Doc.

---

## Phase 0: Refactor — Break the Monolith

**Goal**: Turn `app.py` into a thin routing layer with modular checks. Every subsequent phase adds a new check module without touching existing ones.

### 0.1 Create project structure

```
pitboss-v2/
├── app.py                    # Slim: routes, orchestration only
├── config.py                 # Unchanged
├── google_auth.py            # Unchanged
├── google_docs.py            # Unchanged
├── brief_parser/
│   ├── __init__.py
│   ├── base.py               # BriefData dataclass (keywords, headings, meta specs, word counts, style notes)
│   ├── sheet_parser.py       # Structured Excel/Google Sheet parsing
│   └── doc_parser.py         # DOCX/Google Doc brief parsing
├── checks/
│   ├── __init__.py
│   ├── base.py               # Abstract CheckModule class + CheckResult + WriterComment dataclasses
│   ├── proofread.py          # Grammar/spelling (LLM, model_tier=full)
│   ├── keywords.py           # Keyword extraction + counting (hybrid: Python parser, LLM fallback on mini)
│   ├── seo_structure.py      # Headings, meta, structure (LLM, model_tier=full)
│   ├── compliance.py         # Responsible gambling, disclaimers (Python, model_tier=none)
│   ├── consistency.py        # Internal fact consistency — numbers, bonus values (LLM, model_tier=mini)
│   ├── formatting.py         # Currency formats, date formats, capitalization (Python, model_tier=none)
│   ├── readability.py        # Sentence length, paragraph density, word count per section (Python, model_tier=none)
│   ├── style.py              # Tone, banned words, person/voice (hybrid, model_tier=mini)
│   ├── word_counts.py        # Per-heading word count vs brief spec (Python, model_tier=none)
│   └── fact_check.py         # Sheldon integration — operator site fact verification (LLM, model_tier=full)
├── crawl/
│   ├── __init__.py
│   └── site_crawler.py       # Playwright-based operator site pre-crawler
├── config/
│   ├── brands/               # Per-brand style guide YAML files
│   │   └── default.yaml
│   └── compliance_rules.json # Market-specific compliance patterns (UKGC, MGA, etc.)
├── templates/
│   └── (updated templates)
├── static/                   # CSS extracted from inline styles
│   └── style.css
├── requirements.txt
└── .env.example
```

### 0.2 Define the check module interface

```python
# checks/base.py
from dataclasses import dataclass
from typing import Any

@dataclass
class CheckResult:
    """Standard output from any check module."""
    name: str                    # e.g. "proofread", "keywords", "compliance"
    status: str                  # "pass", "warn", "fail"
    summary: str                 # One-line human summary
    details: Any                 # Module-specific structured data (list/dict)
    corrections: list = None     # Optional: list of corrections that can be applied to Google Docs
    comments: list = None        # Optional: list of WriterComment objects (Phase 6)
    score: float = None          # Optional: 0-100 score for this check

class CheckModule:
    """Base class for all check modules."""
    name: str = "unnamed"
    requires_llm: bool = False   # If True, orchestrator passes the OpenAI client
    requires_brief: bool = True  # Most checks need the parsed brief
    model_tier: str = "none"     # "full" = GPT-4.1, "mini" = GPT-4.1-mini, "none" = Python only

    def run(self, article_text: str, brief_data, **kwargs) -> CheckResult:
        raise NotImplementedError
```

### 0.3 Model Routing Config

LLM checks are split into two tiers to control API cost without sacrificing quality where it matters.

```python
# config.py additions
MODEL_FULL = "gpt-4.1"           # For checks requiring deep language understanding
MODEL_MINI = "gpt-4.1-mini"     # For checks requiring structured extraction/classification

# Per-check model assignments (rationale below):
#
# GPT-4.1 (full) — needs nuanced language judgment:
#   - proofread:      Grammar corrections must be precise; false positives waste editor time
#   - seo_structure:  Matching headings to brief specs requires reading comprehension
#   - fact_check:     Comparing crawled page content against article claims — precision critical
#
# GPT-4.1-mini — structured extraction and classification tasks:
#   - keywords:       Extracting keywords from brief (fallback only; primary path is Python parser)
#   - consistency:    Comparing numbers/values within the same article — pattern matching, not nuance
#   - style:          Checking voice/tone against explicit rules — classification, not generation
#
# No model (Python only):
#   - compliance, formatting, readability, word_counts — deterministic regex/math
```

The orchestrator in `app.py` reads each module's `model_tier` attribute and passes the corresponding OpenAI client/model string. This is transparent to the modules — they just call `self.client.chat.completions.create(model=self.model, ...)` where both are injected at init time.

### 0.4 Refactor `app.py`

Strip all prompt strings, keyword logic, and check-specific code out of `app.py`. The `/run` route becomes:

1. Parse inputs (article + brief)
2. Run brief through `brief_parser` → get `BriefData` object
3. Instantiate check modules (list configurable per run)
4. Execute checks in parallel (`concurrent.futures.ThreadPoolExecutor`)
5. Collect `CheckResult` objects
6. Pass to template

Keep all existing functionality working identically before adding anything new. This is a pure refactor — no new features, no changed behavior.

### 0.5 Extract CSS

Move all inline `<style>` blocks from templates into `static/style.css`. Link from a shared base template.

---

## Phase 1: Fix Brief Parsing (Critical Bug)

**Problem**: Excel briefs are converted to a flat string via `pd.to_string()` and sent to GPT-4.1, which hallucinates keywords from non-keyword sections (instructions, SEO notes, meta descriptions, etc.).

### 1.1 Build structured sheet parser (`brief_parser/sheet_parser.py`)

```python
# Pseudocode for the approach

def parse_excel_brief(file_bytes: bytes) -> BriefData:
    """
    1. Read each sheet tab separately
    2. For each sheet, read the header row
    3. Identify keyword-relevant columns by fuzzy matching headers against known patterns:
       - Keyword column: "keyword", "search term", "target keyword", "phrase", "term"
       - Volume column: "volume", "search volume", "sv", "monthly searches"
       - Usage column: "usage", "density", "count", "how much", "times", "frequency", "min", "max"
       - Type column: "type", "group", "category", "priority", "classification"
    4. If a sheet has a keyword column, extract rows into structured keyword objects
    5. For keyword type/group assignment:
       - If a "type" column exists, use its values (map to main/support/lsi)
       - If no type column, check for grouped sections within the sheet
         (e.g., a row that says "Primary Keywords" followed by keyword rows)
       - Fall back to: first keyword = main, rest = support
    6. For usage counts:
       - Parse "3" → min=3, max=3
       - Parse "2-5" → min=2, max=5
       - Parse "0-10" → min=0, max=10
       - Parse empty/missing → min=null, max=null
    7. Non-keyword sheets/sections: extract as structured brief metadata
       (headings, meta specs, word counts, content guidelines)
    """
```

### 1.2 Build doc brief parser (`brief_parser/doc_parser.py`)

For DOCX/Google Doc briefs, use section detection:
- Scan for keyword section headers ("Primary Keywords", "Main Keywords", "SEO Keywords", etc.)
- Parse the content between that header and the next section header
- Handle both bullet-list format and table format
- Same fuzzy header matching as sheet parser

### 1.3 LLM fallback

If structured parsing finds zero keywords (unrecognized format), fall back to sending ONLY the likely keyword sections to the LLM — not the entire brief. Pre-filter by looking for tables and sections with keyword-like headers. Include a confidence flag in the output so the UI can warn the user: "Keywords were extracted by AI — please verify."

### 1.4 `BriefData` dataclass

```python
@dataclass
class KeywordSpec:
    keyword: str
    group: str              # "main", "support", "lsi"
    required_min: int | None
    required_max: int | None
    source: str             # "parsed" or "llm_extracted" (for confidence display)

@dataclass
class HeadingSpec:
    level: str              # "H1", "H2", "H3"
    text: str
    word_count_min: int | None
    word_count_max: int | None
    content_guidelines: list[str]

@dataclass
class BriefData:
    keywords: list[KeywordSpec]
    headings: list[HeadingSpec]
    meta_title_spec: str | None
    meta_description_spec: str | None
    english_variant: str | None    # Some briefs specify this
    target_word_count: int | None
    faq_specs: list[dict] | None
    style_notes: list[str]
    raw_text: str                  # Full brief text for LLM checks that need it
    parse_method: str              # "structured" or "llm_fallback"
```

---

## Phase 2: New Check Modules (Pure Python — No LLM Cost)

These checks are deterministic. They run fast, cost nothing, and catch real issues.

### 2.1 Compliance Check (`checks/compliance.py`)

Scan article text for iGaming compliance issues using regex patterns:

- **Responsible gambling**: Check for presence of at least one responsible gambling mention ("gamble responsibly", "gambling can be addictive", "18+", "21+", "BeGambleAware", "GamCare", etc.)
- **T&C references**: When bonus claims are detected (regex for "bonus", "free spins", "welcome offer", "deposit match", etc.), check that "terms and conditions", "T&Cs", "wagering requirements" appear nearby (within same section/paragraph)
- **Absolute claims**: Flag "guaranteed win", "risk-free" (note: "risk-free" is specifically banned by ASA/UKGC), "no-lose", "100% safe", "sure bet"
- **Missing age gates**: If article discusses gambling products, check for 18+/21+ reference
- **Missing disclaimer patterns**: Configurable list per market/jurisdiction

Output: List of compliance findings with severity (error/warning/info) and location in text.

### 2.2 Formatting Consistency (`checks/formatting.py`)

Pure regex checks:

- **Currency format**: Detect all currency mentions, flag inconsistency (e.g., article uses both "£500" and "500 GBP" and "GBP500")
- **Number format**: Detect inconsistent thousands separators ("1,000" vs "1000")
- **Percentage format**: "50%" vs "50 %" vs "50 percent" — flag inconsistency
- **Wagering format**: "35x" vs "35 times" vs "35X" — flag inconsistency within same article
- **Date format**: Detect mixed formats (DD/MM/YYYY vs MM/DD/YYYY vs "January 5" vs "5 January")
- **Heading capitalization**: Detect whether headings use Title Case or Sentence case, flag inconsistency

### 2.3 Readability Check (`checks/readability.py`)

- **Sentence length distribution**: Flag sentences over 40 words. Report average sentence length.
- **Paragraph density**: Flag paragraphs over 150 words with no subheading break.
- **Word count per section**: Split article by headings, count words per section. Compare against brief's word count specs (from `BriefData.headings[].word_count_min/max`).
- **Total word count**: Report total and compare against brief's target if specified.
- **Keyword stuffing indicator**: If any keyword appears more than 3% of total word count, flag it.

### 2.4 Word Count Per Heading (`checks/word_counts.py`)

This is currently not checked at all. Briefs often say things like "H2: Introduction (≈150–180 words)".

- Parse article into sections by heading
- Match each section to the corresponding `HeadingSpec` from `BriefData`
- Report actual vs expected word count
- Flag sections that are <80% or >130% of the target range

---

## Phase 3: Enhanced LLM Checks

### 3.1 Upgrade Proofreading (`checks/proofread.py`)

Current proofreading only catches grammar/spelling/punctuation. Expand the system prompt to also catch:

- **Awkward phrasing**: Sentences that are grammatically correct but read poorly (common in AI-generated iGaming content)
- **Redundancy**: "free bonus at no cost", "added bonus", "completely free"
- **Weak transitions**: Flag section transitions that are just "Now let's look at..." or "Moving on to..."
- **Passive voice overuse**: Flag if >30% of sentences are passive
- **Cliché detection**: "In today's fast-paced world", "look no further", "without further ado"

Keep the same JSON output format — just broaden what gets flagged. Add a `severity` field to each correction: "error" (grammar/spelling), "warning" (clarity/style), "suggestion" (improvement).

### 3.2 Internal Consistency Check (`checks/consistency.py`)

New LLM check. Send the article (not the brief) and ask:

- Are bonus values consistent throughout? (e.g., "200% up to £500" in intro, "200% up to £200" in table)
- Are wagering requirements stated consistently?
- Are operator/casino names spelled consistently?
- Are dates/timeframes consistent?
- Do any claims in one section contradict claims in another?

This catches a real class of errors that human editors spend significant time on.

### 3.3 Style Guide Check (`checks/style.py`)

Hybrid: configurable banned-word list (Python) + LLM for nuanced checks.

**Python layer:**
- Banned words/phrases list (configurable, stored in a JSON/YAML file):
  - `"risk-free"` (UKGC banned)
  - `"guaranteed win"`, `"no-lose"`, `"sure thing"`
  - Operator-specific banned terms (loaded from config)
- Person/voice consistency: detect pronouns, flag if article switches between "you" (2nd person) and "players" (3rd person) inconsistently

**LLM layer** (only if style notes exist in the brief):
- Check tone against brief's style notes
- Flag overly promotional language vs informational tone

---

## Phase 4: Parallel Execution and Report Redesign

### 4.1 Parallel check execution

In `app.py`'s `/run` route:

```python
import concurrent.futures
from config import MODEL_FULL, MODEL_MINI

# Initialize checks with appropriate model tier
openai_client = OpenAI()

checks = [
    # Full-tier LLM checks (GPT-4.1 — precision critical)
    ProofreadCheck(client=openai_client, model=MODEL_FULL),
    SEOStructureCheck(client=openai_client, model=MODEL_FULL),
    
    # Mini-tier LLM checks (GPT-4.1-mini — classification/extraction)
    KeywordCheck(client=openai_client, model=MODEL_MINI),  # LLM only used as fallback
    ConsistencyCheck(client=openai_client, model=MODEL_MINI),
    StyleCheck(client=openai_client, model=MODEL_MINI),
    
    # Python-only checks (free, instant)
    ComplianceCheck(),
    FormattingCheck(),
    ReadabilityCheck(),
    WordCountCheck(),
]

# Optionally add fact-check if operator URL was provided
if source_url:
    # Pre-crawl happens before parallel execution (or pulled from cache)
    crawl_result = get_or_crawl(source_url, geo_target)
    checks.append(FactCheckModule(
        client=openai_client, model=MODEL_FULL,
        crawl_result=crawl_result, geo_target=geo_target, categories=selected_categories
    ))

# Run Python-only checks synchronously (fast), LLM checks in parallel
python_checks = [c for c in checks if c.model_tier == "none"]
llm_checks = [c for c in checks if c.model_tier != "none"]

results = {}

# Python checks are instant
for check in python_checks:
    results[check.name] = check.run(article_text, brief_data, brand_config=brand_config)

# LLM checks in parallel (3-4 concurrent API calls)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures = {
        executor.submit(check.run, article_text, brief_data, brand_config=brand_config): check.name
        for check in llm_checks
    }
    for future in concurrent.futures.as_completed(futures):
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception as e:
            results[name] = CheckResult(name=name, status="error", summary=str(e), details=None)
```

### 4.2 Redesign the report template

Current report is a long vertical scroll of tables. Redesign as a tabbed/sectioned report:

1. **Summary bar at top**: Overall score, pass/warn/fail counts per check category
2. **Corrections tab**: Grammar corrections with checkboxes (existing functionality, keep it)
3. **SEO tab**: Keywords, headings, word counts, meta — all in one view
4. **Compliance tab**: Regulatory findings with severity badges
5. **Quality tab**: Readability stats, formatting consistency, style issues
6. **Content vs Brief tab**: Per-heading content quality assessment (existing)

Keep the sticky "Apply to Google Doc" button for the corrections tab.

---

## Phase 5: Brand Style Guide System

### 5.1 Persistent brand config

Create `config/brands/` directory. Each brand gets a YAML file (e.g., `config/brands/default.yaml`, `config/brands/client_x.yaml`):

```yaml
# config/brands/default.yaml
brand_name: "Default iGaming Style"

voice:
  person: "second"           # "second" (you/your) or "third" (players/they)
  tone: "informational"      # "informational", "promotional", "balanced"
  formality: "casual_professional"  # affects sentence structure expectations

structure:
  max_paragraph_words: 120
  max_sentence_words: 35
  require_intro_section: true
  require_conclusion_section: true
  min_paragraphs_per_h2_section: 2
  heading_capitalization: "sentence_case"  # or "title_case"

  # Flow rules: each H2 section should have...
  flow_rules:
    - "Opening sentence that introduces the section topic"
    - "No section should start with a question unless it is an FAQ"
    - "Transition sentence or phrase connecting to the next section"
    - "No back-to-back single-sentence paragraphs"

transitions:
  banned_transitions:
    - "Now let's look at"
    - "Let's dive into"
    - "Without further ado"
    - "In this section, we will"
    - "Moving on to"
    - "As mentioned earlier"
    - "It's worth noting that"
    - "It goes without saying"
  
  weak_transition_patterns:
    # Regex patterns that indicate lazy transitions
    - "^(So|Now|Next|Also|Additionally),? "
    - "^(Let's|Let us) (look|dive|explore|discuss|talk)"

formatting:
  currency_format: "symbol_first"    # "£500" not "500 GBP"
  currency_symbol: "£"               # default for this brand
  percentage_format: "no_space"      # "50%" not "50 %"
  wagering_format: "x_suffix"        # "35x" not "35 times"
  list_style: "bullets_not_numbers"  # unless explicitly ordered
  
banned_phrases:
  # Hard banned — always flag as error
  errors:
    - "risk-free"
    - "guaranteed win"
    - "no-lose"
    - "sure bet"
    - "100% safe"
    - "free money"
  # Soft banned — flag as warning
  warnings:
    - "best casino"           # comparative claims need substantiation
    - "top rated"
    - "number one"
    - "most popular"
    - "the biggest"

cta_rules:
  require_cta_after_sections: ["introduction", "conclusion", "comparison"]
  cta_style: "soft"           # "soft" (informational) vs "hard" (promotional)
  
compliance:
  market: "UKGC"              # Determines which compliance rules apply
  require_age_gate: true
  require_rg_mention: true
  require_tc_near_bonus: true

seo:
  max_keyword_density_percent: 2.5
  require_keyword_in_h1: true
  require_keyword_in_first_paragraph: true
  require_keyword_in_meta_title: true
  require_keyword_in_meta_description: true
```

### 5.2 Brand selector on the form

Add a dropdown to `form.html` that lists available brands (auto-discovered from `config/brands/*.yaml`). The selected brand config is loaded and passed to all check modules alongside the `BriefData`.

### 5.3 Integration with check modules

Every check module receives a `brand_config` parameter:

- **Readability check**: Uses `structure.max_paragraph_words`, `structure.max_sentence_words`
- **Formatting check**: Uses `formatting.*` rules instead of just detecting inconsistency — it now knows the correct format
- **Style check**: Uses `voice.*`, `transitions.*`, `banned_phrases.*`
- **Compliance check**: Uses `compliance.*` to know which market rules apply
- **SEO structure check**: Uses `seo.*` for keyword placement rules
- **Proofread check**: Appends relevant brand rules to the LLM system prompt (e.g., "This brand uses second person. Flag any use of third person referring to the reader.")

### 5.4 Flow and structure checking (new sub-check)

This is the editorial pass you currently do manually. With the brand config defining explicit flow rules, a hybrid check can:

**Python layer:**
- Detect single-sentence paragraphs (flag if back-to-back)
- Detect sections with only 1 paragraph under an H2
- Check that sections don't start/end with banned transition phrases
- Verify intro and conclusion sections exist
- Check that heading order follows a logical progression (not random topic jumps)

**LLM layer:**
- For each section transition (last paragraph of section N → first paragraph of section N+1), ask: "Does this transition flow naturally? Is the connection between these topics clear?"
- For each section opening: "Does this opening sentence introduce the section topic, or does it start abruptly?"
- Flag sections where the content doesn't match what the heading promises

This produces findings like:
- "Section 'Wagering Requirements' starts abruptly — no transition from previous section 'Bonus Types'"
- "Three consecutive single-sentence paragraphs under H2: Payment Methods"
- "Section 'Conclusion' is missing — brand guide requires a conclusion section"

---

## Phase 6: Writer Comments System

This is the second output mode. Currently the tool produces corrections (auto-applied text replacements). This phase adds comments (editorial notes anchored to specific text, visible to writers in Google Docs).

### 6.1 Two-track output model

Every check finding is classified into one of two tracks:

**Track A — Auto-corrections** (existing behavior):
- Grammar, spelling, punctuation fixes
- Formatting normalization (currency format, capitalization)
- These produce `replaceText` operations via Google Docs batchUpdate

**Track B — Writer comments**:
- Factual errors ("Brief says 200% bonus, article says 300%")
- Missing content ("Brief requires wagering requirements explanation, this section doesn't cover it")
- Structural issues ("This section needs to be expanded — brief specifies 150-180 words, current count is 40")
- Compliance gaps ("Missing T&C reference near bonus claim")
- Content quality issues ("This paragraph restates the previous one — consolidate or remove")
- Consistency issues ("Bonus described as '200% up to £500' here but '200% up to £200' in the table above")

### 6.2 Comment data structure

```python
@dataclass
class WriterComment:
    """A comment to be placed in the Google Doc for the writer."""
    id: str
    check_name: str              # Which check module generated this
    severity: str                # "error", "warning", "suggestion"
    anchor_text: str             # The text in the doc this comment refers to
    anchor_start: int            # Document index (for Google Docs API)
    anchor_end: int              # Document index
    comment_text: str            # The actual comment content
    context: str                 # Brief excerpt or rule that justifies the comment
    auto_generated: bool = True  # Flag so user knows this was AI-generated
    
    # For the review UI
    approved: bool = False       # User must approve before applying
    edited_text: str = None      # User can edit the comment before applying
```

### 6.3 Comment generation in check modules

Each check module that produces writer-action-required findings generates `WriterComment` objects. The LLM checks include a prompt addition:

```
For issues that cannot be auto-corrected (factual errors, missing content, 
structural problems), generate a clear, professional editorial comment that:
1. States what the issue is
2. References the specific brief requirement or style rule
3. Tells the writer exactly what action to take
4. Uses a professional but direct tone (you are a senior editor)

Format: "ISSUE: [what's wrong]. BRIEF REQUIRES: [relevant spec]. ACTION: [what the writer should do]."
```

Example generated comments:
- `"ISSUE: Bonus amount inconsistency — '200% up to £500' here vs '200% up to £200' in the comparison table. ACTION: Verify the correct amount from the operator's site and make both references consistent."`
- `"ISSUE: This section is 42 words. BRIEF REQUIRES: ≈150–180 words covering wagering requirements, max cashout, and eligible games. ACTION: Expand to cover all three required subtopics."`
- `"ISSUE: Missing responsible gambling reference. BRAND RULE: All articles must include at least one BeGambleAware or GamCare reference. ACTION: Add an appropriate responsible gambling disclaimer."`

### 6.4 Review UI for comments

In the report template, add a new tab/section: "Writer Comments". Each comment shows:

- Severity badge (red/yellow/blue)
- The anchor text (highlighted excerpt from the article)
- The generated comment text — **in an editable textarea** so you can tweak the wording
- A checkbox to approve/reject
- Context: which brief requirement or brand rule triggered it

At the bottom: "Apply Approved Comments to Google Doc" button.

### 6.5 Google Docs comment API integration

Add to `google_docs.py`:

```python
def apply_comments(doc_id: str, comments: list[dict]) -> dict:
    """
    Apply editorial comments to a Google Doc using the Drive API v3 comments endpoint.
    
    Google Docs API batchUpdate does NOT support comments directly.
    Instead, use Drive API v3: files.comments.create with anchor content.
    
    Args:
        doc_id: Google Doc file ID
        comments: List of dicts with:
            - content: The comment text
            - anchor_start: Start index in the document
            - anchor_end: End index in the document
            
    Returns:
        dict with applied_count and any failures
    """
    from googleapiclient.discovery import build
    credentials = get_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    applied = 0
    failed = []
    
    for comment in comments:
        try:
            # Google Drive API v3 comment anchor format
            # The anchor references a text range in the document
            anchor = json.dumps({
                "r": doc_id,
                "a": [{
                    "txt": {
                        "o": comment['anchor_start'],
                        "l": comment['anchor_end'] - comment['anchor_start']
                    }
                }]
            })
            
            drive_service.comments().create(
                fileId=doc_id,
                fields='id,content,anchor',
                body={
                    'content': comment['content'],
                    'anchor': anchor
                }
            ).execute()
            applied += 1
        except Exception as e:
            failed.append({
                'comment': comment['content'][:50],
                'error': str(e)
            })
    
    return {'applied_count': applied, 'failed': failed}
```

**Important**: The Drive API comment anchor format is not well-documented. The anchor JSON structure above is reverse-engineered from Google's implementation. If it doesn't work reliably, the fallback approach is:

**Fallback — Suggest mode insertions**: Instead of comments, use Google Docs `batchUpdate` with `insertText` in suggestion mode. This inserts highlighted text that appears as a suggestion the writer can accept/reject. Less clean than comments but uses the well-documented Docs API.

**Fallback 2 — Clipboard-ready comments**: If the API approach proves unreliable, generate the comments in a copy-paste-ready format in the report. Each comment shows the heading/section it belongs under, the anchor text, and the comment. You click a "Copy" button next to each comment, switch to the Google Doc, select the relevant text, and Ctrl+Alt+M to add a comment, then paste. Slower than automatic, but still much faster than writing each comment from scratch.

### 6.6 OAuth scope update

Add `https://www.googleapis.com/auth/drive.file` scope (already present in `config.py`) — this scope covers comments on files the app has accessed. No new scope needed.

---

## Phase 7: Fact-Checker Integration (Sheldon)

Integrates the existing Sheldon fact-checking tool (currently a standalone Streamlit app) as a check module within pitboss. Sheldon verifies every claim, name, game, provider, bonus detail, and payment method in an article against the actual operator website.

### 7.1 Pre-crawl engine (`crawl/site_crawler.py`)

**Critical**: The current Sheldon sends a prompt telling GPT-4.1 to "crawl the entire site" — but the API cannot browse the web. It answers from training data, which is stale for operator sites that update T&Cs, bonuses, and payment methods constantly.

Replace this with an actual pre-crawl using Playwright (already in `requirements.txt`):

```python
# crawl/site_crawler.py
"""
Pre-crawls an operator website and extracts structured content
for fact-checking. Feeds real page content to the LLM instead
of relying on training data.
"""

from dataclasses import dataclass
from playwright.async_api import async_playwright
import asyncio
import re

@dataclass 
class CrawledPage:
    url: str
    title: str
    content: str          # Cleaned text content
    page_type: str        # "bonus_terms", "payments", "promotions", "vip", "rg", "legal", "faq", "games", "general"
    content_tokens: int   # Approximate token count for budget management

@dataclass
class CrawlResult:
    domain: str
    pages: list           # list of CrawledPage
    total_tokens: int
    crawl_log: list       # URLs visited, for the coverage log output
    errors: list          # URLs that failed

# Priority crawl targets — ordered by authority (legal pages first)
PRIORITY_PATHS = [
    ("/bonus-terms", "bonus_terms"),
    ("/terms", "legal"),
    ("/terms-and-conditions", "legal"),
    ("/privacy", "legal"),
    ("/payments", "payments"),
    ("/banking", "payments"),
    ("/promotions", "promotions"),
    ("/bonuses", "promotions"),
    ("/vip", "vip"),
    ("/loyalty", "vip"),
    ("/responsible-gaming", "rg"),
    ("/responsible-gambling", "rg"),
    ("/support", "faq"),
    ("/faq", "faq"),
    ("/help", "faq"),
    ("/providers", "games"),
    ("/games", "games"),
]

async def crawl_operator_site(
    root_url: str,
    max_pages: int = 20,
    max_total_tokens: int = 50000,
    geo_locale: str = None,
) -> CrawlResult:
    """
    Crawl an operator site starting from root_url.
    
    Strategy:
    1. Hit the root URL, extract nav/footer links
    2. Visit priority paths that exist on this domain
    3. If geo_locale is set (e.g., "en-CA"), prefer locale-prefixed paths
    4. For each page: expand accordions/tabs (click "show more"), extract clean text
    5. Stop when max_pages or max_total_tokens is reached
    6. Return structured content grouped by page_type
    
    The token budget prevents sending too much context to the LLM.
    Pages are prioritized by authority: legal > bonus_terms > payments > promotions > everything else.
    """
    # Implementation: use Playwright headless browser
    # Key behaviors:
    # - Accept cookies/popups automatically
    # - Click accordion/tab elements to expand hidden content
    # - Handle infinite scroll / "load more" buttons (with a cap)
    # - Strip nav, footer, sidebar chrome — keep only main content
    # - Detect and skip soft-blocked GEOs (redirect to different locale)
    pass
```

### 7.2 Token budget management

Operator sites can be large. The crawler must stay within a token budget to keep costs reasonable.

**Strategy — tiered content selection:**

1. **Always include** (highest authority): bonus terms, T&Cs, legal pages. These are typically 3-5 pages, ~10-15K tokens total.
2. **Include if budget allows**: payments, promotions, VIP, responsible gambling. Another 5-8 pages, ~10-20K tokens.
3. **Include on demand**: games/providers catalog, FAQ, support pages. Only crawled if the article mentions specific games/providers/support channels.

**Demand-based crawling**: Before crawling, run a fast Python scan of the article to extract named entities (game titles, provider names, payment methods, bonus codes). Only crawl games/providers pages if the article references specific games. This avoids wasting tokens on a full games catalog when the article is about payment methods.

Target: **30-50K input tokens per fact-check** (legal core + relevant supplementary pages). At GPT-4.1 pricing ($2/1M input), that's ~$0.06-0.10 per crawl in input tokens.

### 7.3 Fact-check module (`checks/fact_check.py`)

```python
class FactCheckModule(CheckModule):
    name = "fact_check"
    requires_llm = True
    requires_brief = False       # Fact-check only needs article + crawled site
    model_tier = "full"          # GPT-4.1 — precision critical for fact verification

    def run(self, article_text: str, brief_data=None, **kwargs) -> CheckResult:
        """
        Required kwargs:
            source_url: str         — operator root URL
            geo_target: str         — e.g. "Canada (CAD)"
            categories: list[int]   — which Sheldon categories to check (1-13)
            crawl_result: CrawlResult  — pre-crawled site content (passed by orchestrator)
        """
        # 1. Build context from crawled pages, organized by page_type
        # 2. Extract all named entities from article (games, providers, bonuses, etc.)
        # 3. Send to GPT-4.1 with the Sheldon system prompt, but replace
        #    "crawl the entire site" instructions with:
        #    "The following is pre-crawled content from {domain}, organized by section.
        #     Use ONLY this content as your source of truth. Do not use training data."
        # 4. Parse structured JSON output into CheckResult
        # 5. Non-compliant items become WriterComment objects (Phase 6 integration)
        pass
```

**Key change from standalone Sheldon**: The system prompt is modified to tell the model it's receiving pre-crawled content, not crawling itself. This eliminates hallucinated "evidence URLs" — every URL in the output is a real URL from the crawl log.

### 7.4 Sheldon prompt adaptation

The existing 216-line Sheldon system prompt is preserved almost entirely. Changes:

1. Remove all "Full Crawl Required" / "crawl the entire site" / "visit all pages" instructions — the model receives pre-crawled content, it doesn't need to crawl.
2. Add a preamble: "You are receiving pre-crawled, verified content from the operator's website. This content is current as of the crawl timestamp. Base ALL verification exclusively on this provided content. If a claim cannot be verified from the provided content, mark it Not Compliant with reason 'Not found in crawled content'."
3. Keep all output format requirements (Name Verification Matrix, Category Claims, Coverage Log) — these are well-structured and useful.
4. The Coverage Log is now populated from the actual `CrawlResult.crawl_log`, not hallucinated by the model.

### 7.5 Form integration

Add optional fields to `form.html`:

```
Fact-Check (optional)
├── Operator URL: [text input, e.g. https://20bet.com/]
├── GEO Target:   [text input, e.g. "Canada (CAD)"]
└── Categories:   [multi-select checkboxes, 1-13, default: 1,3,4,6,9,12]
```

When Operator URL is blank, the fact-check is skipped entirely — no crawl, no LLM call, no cost.

### 7.6 Crawl caching

Operator sites don't change hourly. Cache crawl results in `~/.pitboss/crawl_cache/` keyed by domain + date. If you're checking 3 articles against the same operator site on the same day, the site is only crawled once. Cache TTL: 24 hours (configurable).

This means: first article against 20bet.com today = crawl + fact-check ($0.15-0.25). Second and third articles against 20bet.com today = fact-check only ($0.06-0.10 each, no crawl cost).

### 7.7 Fact-check output → Writer comments

Non-compliant findings from the fact-check are converted to `WriterComment` objects:

- **Severity mapping**: Sheldon's "Critical" → error, "Major" → error, "Minor" → warning, "Cosmetic" → suggestion
- **Comment format**: "FACT CHECK: [claim from article]. SOURCE SAYS: [evidence quote from crawled page]. URL: [actual page URL]. ACTION: [correct the figure / remove the claim / verify with operator]."
- These appear in the Phase 6 writer comments review UI alongside editorial comments, clearly tagged as fact-check findings.

---

## Phase 8: Google Sheets Brief Support

### 8.1 Add Google Sheets API integration

Currently briefs must be uploaded as files. Add the option to paste a Google Sheet URL, similar to how Google Doc URLs work for articles.

- Add `https://www.googleapis.com/auth/spreadsheets.readonly` to OAuth scopes in `config.py`
- New module: `google_sheets.py` (fetch sheet data via Sheets API, return as structured dataframe)
- Update `form.html` to add a "Brief Source" radio (Upload File / Google Sheet URL)
- Feed the structured sheet data directly to `brief_parser/sheet_parser.py` — skip the `pd.to_string()` lossy conversion entirely

This is a big quality win: you get actual column headers, cell types, sheet names, and merged cell info instead of a flattened string.

---

## Phase 9: Quality of Life

### 9.1 Brief format memory

Save a mapping of brief structures per client/batch. When the same column layout is seen again, skip the detection step. Store in a local JSON file (`~/.pitboss/brief_schemas.json`).

### 9.2 Check toggles on the form

Let the user enable/disable individual checks before running. Default all on, but allow skipping e.g. compliance checks for non-UKGC markets.

### 9.3 Progress indicator

The current form submits and blocks until all checks complete. Add a simple progress page that polls a `/status/<job_id>` endpoint showing which checks have finished.

### 9.4 Report export

Add a "Download Report as DOCX" button that generates a formatted report document the user can attach to the Notion task.

---

## Implementation Order

Work through these in order. Each phase should be a working commit — never break the app between phases.

1. **Phase 0** — Refactor. Module structure + model routing config. All existing functionality preserved.
2. **Phase 1** — Fix brief parsing. Highest-impact bug fix.
3. **Phase 2** — Pure Python checks (compliance, formatting, readability, word counts). Free.
4. **Phase 3** — Enhanced LLM checks (better proofreading, consistency, style). Mini-tier where possible.
5. **Phase 4** — Parallel execution + report redesign.
6. **Phase 5** — Brand style guide system. Persistent editorial rules.
7. **Phase 6** — Writer comments system. Two-track output: auto-corrections + editorial comments.
8. **Phase 7** — Sheldon fact-checker integration with Playwright pre-crawl + crawl caching.
9. **Phase 8** — Google Sheets integration for briefs.
10. **Phase 9** — QoL polish (check toggles, progress indicator, report export).

---

## Cost Model

Baseline: ~7-10 articles/day at ~2,000-2,500 words each. Current spend: $5-6/week.

### Per-article API cost breakdown (after all upgrades)

| Check | Model | Tier | Input tokens (approx) | Output tokens (approx) | Cost/article |
|---|---|---|---|---|---|
| Proofread | GPT-4.1 | full | ~4K | ~3K | ~$0.032 |
| SEO structure | GPT-4.1 | full | ~8K | ~2K | ~$0.032 |
| Keyword extract (fallback only) | GPT-4.1-mini | mini | ~4K | ~1K | ~$0.003 |
| Consistency | GPT-4.1-mini | mini | ~3K | ~1K | ~$0.002 |
| Style/flow | GPT-4.1-mini | mini | ~4K | ~1K | ~$0.003 |
| Compliance | Python | none | — | — | $0 |
| Formatting | Python | none | — | — | $0 |
| Readability | Python | none | — | — | $0 |
| Word counts | Python | none | — | — | $0 |
| **Subtotal (no fact-check)** | | | | | **~$0.07** |
| Fact-check (when used) | GPT-4.1 | full | ~40K | ~4K | ~$0.11 |
| **Subtotal (with fact-check)** | | | | | **~$0.18** |

### Weekly cost estimates

| Scenario | Per article | Articles/day | Weekly (5 days) |
|---|---|---|---|
| All checks, no fact-checker | $0.07 | 8 | **~$2.80** |
| All checks + fact-check 25% of articles | $0.07 base + $0.11 × 25% | 8 | **~$3.90** |
| All checks + fact-check 50% of articles | $0.07 base + $0.11 × 50% | 8 | **~$5.00** |

**This is actually cheaper than your current $5-6/week** because:
- The keyword extraction LLM call is eliminated for most briefs (structured parser handles it in Python)
- Consistency and style checks use GPT-4.1-mini (5x cheaper per token than full)
- The 4 new Python checks add zero API cost
- Crawl caching means multiple articles against the same operator site share one crawl

The current tool uses GPT-4.1 for all 3 calls including keyword extraction (which is expensive overkill for what's essentially structured data parsing). After the upgrade, you get 9 checks for roughly the same cost as the current 3.

---

## Technical Notes

- **Local deployment**: Keep running locally via Flask dev server. No need to deploy anywhere — this is a personal productivity tool. If you ever want to access it from your phone or another machine on the same network, just bind to `0.0.0.0` instead of `127.0.0.1`.
- **Model routing**: GPT-4.1 for proofread, SEO structure, and fact-check (precision-critical). GPT-4.1-mini for keyword fallback, consistency, and style (classification tasks). Python for compliance, formatting, readability, word counts. This split cuts LLM cost roughly in half vs using GPT-4.1 for everything while maintaining quality where it matters. If at any point a mini-tier check isn't performing well enough, promote it to full — the config change is one line.
- **Playwright for fact-checking**: Playwright is already in `requirements.txt`. The pre-crawl approach gives real, current page content instead of stale training data. Run Playwright in headless mode. On Windows (local dev), Playwright installs Chromium automatically via `playwright install chromium`. The crawl runs before the fact-check LLM call, adding ~10-20 seconds per operator site (cached for 24 hours).
- **Testing**: Create a `tests/` folder with sample briefs (one Excel, one DOCX) and sample articles. Write a `test_brief_parser.py` that asserts the parser extracts the correct keywords from each format. This is critical for Phase 1 — you need to verify the parser works across your actual brief formats before trusting it. For the fact-checker, add a `tests/crawl/` folder with saved HTML snapshots of operator pages to test the content extraction without hitting live sites.
- **Config file for compliance rules**: Create `config/compliance_rules.json` with market-specific rules (UKGC, MGA, Curacao, etc.) that the compliance checker loads. This makes it easy to add new markets without changing code.
- **Sheldon migration**: The standalone Sheldon Streamlit app (`Ogi404/Sheldon`) can be retired after Phase 7. Its system prompt moves into `checks/fact_check.py`, the Streamlit UI is replaced by the pitboss form fields, and the output format is preserved but rendered inside the pitboss report template. No functionality is lost.
