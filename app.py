import os
import re
import json
from io import BytesIO

from flask import Flask, render_template, request
from openai import OpenAI
from docx import Document
import pandas as pd

app = Flask(__name__)

# OpenAI client (uses OPENAI_API_KEY env var)
client = OpenAI()

# Character caps to keep SEO calls within token limits
MAX_BRIEF_CHARS_FOR_SEO = 40000
MAX_ARTICLE_CHARS_FOR_SEO = 20000


# ------------------------------------------------------------------
# File extraction helpers
# ------------------------------------------------------------------

def extract_text_from_docx(file_storage):
    """
    Takes an uploaded DOCX file and returns all the text as one big string.
    """
    file_bytes = file_storage.read()
    doc = Document(BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def extract_text_from_brief(file_storage):
    """
    Accepts DOCX or Excel (XLS/XLSX) for the task brief and returns plain text.
    """
    filename = file_storage.filename or ""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    if ext == ".docx":
        return extract_text_from_docx(file_storage)

    if ext in [".xls", ".xlsx"]:
        file_bytes = file_storage.read()
        excel_file = BytesIO(file_bytes)
        xls = pd.ExcelFile(excel_file)

        texts = []
        for sheet_name in xls.sheet_names:
            df = xls.parse(sheet_name, dtype=str)
            df = df.fillna("")
            texts.append(f"=== Sheet: {sheet_name} ===")
            texts.append(df.to_string(index=False))

        return "\n\n".join(texts)

    # Fallback: try to decode as plain text
    return file_storage.read().decode("utf-8", errors="ignore")


# ------------------------------------------------------------------
# System prompts
# ------------------------------------------------------------------

PROOFREAD_SYSTEM_PROMPT = """
You are a professional iGaming content proofreader.

Your job:
- Fix grammar, spelling, punctuation, and minor clarity issues.
- Respect the requested English variant (US, UK, Canadian, Australian, New Zealand, Indian).
- Do NOT change factual content, headings structure, or SEO keywords unless they are clearly misspelled.
- Do NOT rewrite the article; only make necessary corrections.

Output JSON ONLY in this exact format:

{
  "sentences": [
    {
      "original": "string",
      "corrected": "string",
      "status": "ok or corrected",
      "explanation": "string (why you changed it, or 'No change needed.')"
    }
  ],
  "clean_article": "the full corrected article as one block of text"
}
"""


KEYWORDS_SYSTEM_PROMPT = """
You are a careful parser of iGaming task briefs.

Goal: extract ONLY the explicit keyword lists from the brief and return them in JSON.
Do NOT invent or drop keywords. If a keyword appears in a list or table, it must appear in the JSON.

Look for sections labelled things like:
- "Primary keywords", "Primary:", "Main keywords"
- "Secondary keywords", "Support keywords"
- "LSI keywords", "LSI terms", "Single-word LSI keys"
- Tables with columns like "Keyword | Volume | How much to use in the text"

Rules:
- Each row or bullet becomes ONE keyword entry.
- Use the keyword text exactly as written (keep spaces and order, ignore casing).
- If a table has columns like "Keyword  Volume  How much to use in the text":
    - Take the keyword from the first column.
    - Parse the last numeric column into:
        - required_min and required_max:
            - If it is a single integer (e.g. "3") -> min = max = 3
            - If it is a range like "0-10" -> min = 0, max = 10
    - Ignore the "Volume" column (this is search volume, not usage).
- If no explicit usage count is given for a keyword:
    - Set required_min = null, required_max = null.
- For LSI / "single-word LSI keys" lists without counts:
    - Use required_min = 1, required_max = null (we just want them to appear at least once).

Output JSON with this exact shape:

{
  "main": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ],
  "support": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ],
  "lsi": [
    { "keyword": "string", "required_min": int or null, "required_max": int or null },
    ...
  ]
}

- Put "primary"/"main" keywords into "main".
- Put "secondary"/"support" keywords into "support".
- Put LSI/single-word LSI into "lsi".
- If a category does not exist in the brief, return an empty list for it.
"""


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

# ------------------------------------------------------------------
# Keyword counting helpers
# ------------------------------------------------------------------

def count_keyword_occurrences(text: str, phrase: str) -> int:
    if not phrase or not phrase.strip():
        return 0
    pattern = re.escape(phrase.strip())
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def build_keyword_report(article_text: str, kw_data: dict):
    """
    kw_data = {"main": [...], "support": [...], "lsi": [...]}
    Returns a flat list of dicts used by the template.
    """
    seo_keywords = []

    for group_name in ["main", "support", "lsi"]:
        items = kw_data.get(group_name, []) or []
        for item in items:
            kw = (item.get("keyword") or "").strip()
            if not kw:
                continue
            required_min = item.get("required_min")
            required_max = item.get("required_max")
            found = count_keyword_occurrences(article_text, kw)

            if required_min is None and required_max is None:
                # No explicit count – just presence
                status = "missing" if found == 0 else "present"
            else:
                if found == 0 and (required_min or 0) > 0:
                    status = "missing"
                elif required_min is not None and found < required_min:
                    status = "underused"
                elif required_max is not None and found > required_max:
                    status = "overused"
                else:
                    status = "compliant"

            seo_keywords.append(
                {
                    "keyword": kw,
                    "group": group_name,      # "main" / "support" / "lsi"
                    "found": found,
                    "required_min": required_min,
                    "required_max": required_max,
                    "status": status,
                }
            )

    return seo_keywords


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template("form.html")


@app.route("/run", methods=["POST"])
def run():
    # 1) Get form fields and files
    english_variant = request.form.get("english_variant", "UK")
    article_file = request.files.get("article_file")
    brief_file = request.files.get("brief_file")

    if not article_file:
        return "No article file uploaded.", 400
    if not brief_file:
        return "No task brief file uploaded.", 400

    # 2) Extract text
    try:
        article_text_full = extract_text_from_docx(article_file)
    except Exception as e:
        return f"Error reading article DOCX: {e}", 500

    try:
        brief_text_full = extract_text_from_brief(brief_file)
    except Exception as e:
        return f"Error reading task brief file: {e}", 500

    # Truncated versions for SEO calls
    article_text_for_seo = article_text_full[:MAX_ARTICLE_CHARS_FOR_SEO]
    brief_text_for_seo = brief_text_full[:MAX_BRIEF_CHARS_FOR_SEO]

    # 3) Proofreading on full article
    user_prompt = f"""
English variant to use: {english_variant}

Here is the article text to proofread:

\"\"\"{article_text_full}\"\"\"
"""

    completion = client.chat.completions.create(
        model="gpt-4.1",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROOFREAD_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    raw_text = completion.choices[0].message.content

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return f"Model did not return valid JSON for proofreading:\n\n{raw_text}", 500

    sentences = data.get("sentences", [])
    clean_article = data.get("clean_article", "")

    # 4) KEYWORD EXTRACTION from brief (model only reads the brief)
    keywords_json = {"main": [], "support": [], "lsi": []}
    try:
        kw_completion = client.chat.completions.create(
            model="gpt-4.1",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": KEYWORDS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Task brief:\n\n\"\"\"\n{brief_text_for_seo}\n\"\"\"",
                },
            ],
        )
        keywords_json = json.loads(kw_completion.choices[0].message.content)
    except Exception as e:
        print("Keyword extraction failed:", e)
        keywords_json = {"main": [], "support": [], "lsi": []}

    # Python-based deterministic counts
    seo_keywords = build_keyword_report(article_text_full, keywords_json)

    # 5) SEO / structure / content quality (no keywords here)
    seo_user_prompt = f"""
TASK BRIEF (possibly truncated):

\"\"\"{brief_text_for_seo}\"\"\"

ARTICLE (possibly truncated):

\"\"\"{article_text_for_seo}\"\"\"
"""

    try:
        seo_completion = client.chat.completions.create(
            model="gpt-4.1",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SEO_SYSTEM_PROMPT},
                {"role": "user", "content": seo_user_prompt},
            ],
        )
        seo_raw = seo_completion.choices[0].message.content
        seo_data = json.loads(seo_raw)
    except Exception as e:
        print("SEO check failed:", e)
        seo_data = {
            "headings": [],
            "meta": {
                "has_meta_title": False,
                "has_meta_description": False,
                "issues": [f"SEO check failed: {e}"],
            },
            "structure_issues": ["SEO check failed due to an API error."],
            "content_quality": [],
        }

    seo_headings = seo_data.get("headings", [])
    seo_meta = seo_data.get("meta", {})
    seo_structure_issues = seo_data.get("structure_issues", [])
    seo_content_quality = seo_data.get("content_quality", [])

    # 6) Render combined report
    return render_template(
        "proofread_report.html",
        sentences=sentences,
        clean_article=clean_article,
        english_variant=english_variant,
        seo_headings=seo_headings,
        seo_keywords=seo_keywords,          # our deterministic keyword report
        seo_meta=seo_meta,
        seo_structure_issues=seo_structure_issues,
        seo_content_quality=seo_content_quality,
    )


if __name__ == "__main__":
    app.run(debug=True, port=8001)
