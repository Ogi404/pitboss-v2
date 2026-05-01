"""
Excel/Google Sheets brief parser.

Extracts keywords from structured Excel briefs by detecting
keyword columns, usage counts, and keyword groups.
"""

import re
from io import BytesIO
from typing import List, Tuple, Optional
import pandas as pd

from .base import KeywordSpec


# Patterns for detecting keyword-related columns (case-insensitive)
KEYWORD_COLUMN_PATTERNS = [
    r"keyword",
    r"search\s*term",
    r"target\s*keyword",
    r"phrase",
    r"^term$",
    r"^key$",
]

USAGE_COLUMN_PATTERNS = [
    r"usage",
    r"how\s*much",
    r"times",
    r"frequency",
    r"count",
    r"density",
    r"^min$",
    r"^max$",
    r"use\s*in",
    r"quantity",
]

GROUP_COLUMN_PATTERNS = [
    r"^type$",
    r"^group$",
    r"category",
    r"priority",
    r"classification",
]

# Patterns for detecting keyword group from section headers or values
GROUP_PATTERNS = {
    "main": [r"primary", r"^main$", r"target", r"focus"],
    "support": [r"secondary", r"support", r"related"],
    "lsi": [r"lsi", r"semantic", r"single[\s-]*word", r"long[\s-]*tail"],
}

# Patterns for detecting keyword SECTION headers (e.g., "Main keywords" cell)
# These are scanned across ALL cells in the sheet
KEYWORD_SECTION_PATTERNS = {
    "main": [r"main\s+keywords?", r"primary\s+keywords?", r"target\s+keywords?"],
    "support": [r"support\s+keywords?", r"supporting\s+keywords?", r"secondary\s+keywords?", r"related\s+keywords?"],
    "lsi": [r"lsi\s+keywords?", r"semantic\s+keywords?", r"long[\s-]*tail\s+keywords?"],
}


def _normalize_header(header: str) -> str:
    """Normalize column header for matching."""
    if not isinstance(header, str):
        return str(header).lower().strip()
    return header.lower().strip()


def _matches_patterns(text: str, patterns: List[str]) -> bool:
    """Check if text matches any of the given regex patterns."""
    text = _normalize_header(text)
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _detect_group_from_text(text: str) -> Optional[str]:
    """Detect keyword group from text (section header or cell value)."""
    text = _normalize_header(text)
    for group, patterns in GROUP_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return group
    return None


def _parse_usage_value(value) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse usage count value into (min, max) tuple.

    Examples:
        "3" -> (3, 3)
        "2-5" -> (2, 5)
        "0-10" -> (0, 10)
        "Any" -> (None, None)
        "" or None -> (None, None)
    """
    if pd.isna(value) or value == "":
        return None, None

    value_str = str(value).strip()

    # Handle "Any" or similar (no specific requirement)
    if value_str.lower() in ["any", "n/a", "-", "optional"]:
        return None, None

    # Try range format: "2-5" or "2 - 5"
    range_match = re.match(r"(\d+)\s*[-–—]\s*(\d+)", value_str)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    # Try single number
    num_match = re.match(r"^(\d+)$", value_str)
    if num_match:
        num = int(num_match.group(1))
        return num, num

    return None, None


def _find_column_index(headers: List[str], patterns: List[str]) -> Optional[int]:
    """Find the index of a column matching the given patterns."""
    for i, header in enumerate(headers):
        if _matches_patterns(header, patterns):
            return i
    return None


def _detect_section_group(df: pd.DataFrame, row_idx: int) -> Optional[str]:
    """
    Look backwards from row_idx to find a section header that indicates group.

    Section headers are typically rows where only the first column has text
    like "Primary Keywords" or "LSI Terms".
    """
    for i in range(row_idx - 1, -1, -1):
        row = df.iloc[i]
        # Check if this looks like a section header (mostly empty row with text in first col)
        non_empty = row.dropna().astype(str).str.strip().replace("", pd.NA).dropna()
        if len(non_empty) == 1:
            header_text = str(non_empty.iloc[0])
            group = _detect_group_from_text(header_text)
            if group:
                return group
    return None


def _find_keyword_sections(df: pd.DataFrame) -> List[Tuple[int, int, str]]:
    """
    Scan entire DataFrame for keyword section headers.

    Looks for cells containing "Main keywords", "Support keywords", "LSI keywords", etc.
    anywhere in the sheet (not just the first row).

    Returns:
        List of (row_idx, col_idx, group_type) tuples where keyword sections start.
    """
    sections = []

    for row_idx in range(len(df)):
        for col_idx in range(len(df.columns)):
            cell_value = str(df.iloc[row_idx, col_idx]).strip()
            if not cell_value or cell_value.lower() == "nan":
                continue

            cell_lower = cell_value.lower()

            # Check against each group's patterns
            for group, patterns in KEYWORD_SECTION_PATTERNS.items():
                for pattern in patterns:
                    if re.search(pattern, cell_lower, re.IGNORECASE):
                        sections.append((row_idx, col_idx, group))
                        break
                else:
                    continue
                break  # Found a match for this cell, move to next cell

    return sections


def _extract_keywords_from_section(
    df: pd.DataFrame,
    header_row: int,
    keyword_col: int,
    group: str
) -> List[KeywordSpec]:
    """
    Extract keywords from a section starting at the given header position.

    Args:
        df: The DataFrame
        header_row: Row index where the section header ("Main keywords") is
        keyword_col: Column index of the keyword column
        group: The keyword group (main, support, lsi)

    Returns:
        List of KeywordSpec objects extracted from this section.
    """
    keywords = []
    quantity_col = keyword_col + 1 if keyword_col + 1 < len(df.columns) else None

    # Start reading from the row after the header
    for row_idx in range(header_row + 1, len(df)):
        keyword_value = str(df.iloc[row_idx, keyword_col]).strip()

        # Stop at empty cell or if we hit another section header
        if not keyword_value or keyword_value.lower() in ["", "nan", "none"]:
            break

        # Also stop if this looks like another section header
        cell_lower = keyword_value.lower()
        is_section_header = False
        for patterns in KEYWORD_SECTION_PATTERNS.values():
            for pattern in patterns:
                if re.search(pattern, cell_lower, re.IGNORECASE):
                    is_section_header = True
                    break
            if is_section_header:
                break
        if is_section_header:
            break

        # Skip if this looks like a column header repeated
        if _matches_patterns(keyword_value, KEYWORD_COLUMN_PATTERNS):
            continue

        # Get usage count from adjacent column
        req_min, req_max = None, None
        if quantity_col is not None:
            quantity_value = df.iloc[row_idx, quantity_col]
            req_min, req_max = _parse_usage_value(quantity_value)

        keywords.append(KeywordSpec(
            keyword=keyword_value,
            group=group,
            required_min=req_min,
            required_max=req_max,
            source="parsed"
        ))

    return keywords


def parse_excel_brief(file_bytes: bytes) -> Tuple[List[KeywordSpec], str, List[str]]:
    """
    Parse an Excel file and extract keyword specifications.

    Args:
        file_bytes: Raw bytes of the Excel file

    Returns:
        Tuple of (keywords_list, raw_text, warnings)
    """
    keywords = []
    warnings = []
    raw_texts = []

    try:
        excel_file = BytesIO(file_bytes)
        xls = pd.ExcelFile(excel_file)
    except Exception as e:
        warnings.append(f"Failed to read Excel file: {e}")
        return [], "", warnings

    for sheet_name in xls.sheet_names:
        try:
            df = xls.parse(sheet_name, dtype=str, header=None)
            df = df.fillna("")

            # Store raw text for SEO check
            raw_texts.append(f"=== Sheet: {sheet_name} ===")
            raw_texts.append(df.to_string(index=False, header=False))

            if df.empty or len(df) < 2:
                continue

            # NEW APPROACH: Scan ALL cells for keyword section headers
            # (e.g., "Main keywords", "Support keywords", "LSI keywords")
            sections = _find_keyword_sections(df)

            if sections:
                # Extract keywords from each found section
                for header_row, keyword_col, group in sections:
                    section_keywords = _extract_keywords_from_section(
                        df, header_row, keyword_col, group
                    )
                    keywords.extend(section_keywords)
            else:
                # FALLBACK: Try the old approach for sheets with traditional headers
                # Try to find header row (first row with keyword-like column)
                header_row_idx = None
                for i in range(min(5, len(df))):  # Check first 5 rows for headers
                    row = df.iloc[i].tolist()
                    if _find_column_index(row, KEYWORD_COLUMN_PATTERNS) is not None:
                        header_row_idx = i
                        break

                if header_row_idx is None:
                    continue  # No keyword structure found

                headers = [str(h) for h in df.iloc[header_row_idx].tolist()]

                # Find relevant columns
                keyword_col = _find_column_index(headers, KEYWORD_COLUMN_PATTERNS)
                usage_col = _find_column_index(headers, USAGE_COLUMN_PATTERNS)
                group_col = _find_column_index(headers, GROUP_COLUMN_PATTERNS)

                if keyword_col is None:
                    continue

                # Process data rows
                current_group = "support"  # Default group

                for row_idx in range(header_row_idx + 1, len(df)):
                    row = df.iloc[row_idx]
                    keyword = str(row.iloc[keyword_col]).strip() if keyword_col < len(row) else ""

                    if not keyword or keyword.lower() in ["", "nan", "none"]:
                        first_cell = str(row.iloc[0]).strip() if len(row) > 0 else ""
                        detected_group = _detect_group_from_text(first_cell)
                        if detected_group:
                            current_group = detected_group
                        continue

                    if _matches_patterns(keyword, KEYWORD_COLUMN_PATTERNS):
                        continue

                    req_min, req_max = None, None
                    if usage_col is not None and usage_col < len(row):
                        req_min, req_max = _parse_usage_value(row.iloc[usage_col])

                    group = current_group
                    if group_col is not None and group_col < len(row):
                        cell_group = _detect_group_from_text(str(row.iloc[group_col]))
                        if cell_group:
                            group = cell_group

                    section_group = _detect_section_group(df, row_idx)
                    if section_group:
                        group = section_group

                    keywords.append(KeywordSpec(
                        keyword=keyword,
                        group=group,
                        required_min=req_min,
                        required_max=req_max,
                        source="parsed"
                    ))

        except Exception as e:
            warnings.append(f"Error parsing sheet '{sheet_name}': {e}")
            continue

    raw_text = "\n\n".join(raw_texts)

    if not keywords:
        warnings.append("No keywords found in Excel structure. Will use LLM fallback.")

    return keywords, raw_text, warnings


def parse_sheets_dataframes(sheets_dict: dict) -> Tuple[List[KeywordSpec], str, List[str]]:
    """
    Parse keyword specifications from a dict of DataFrames (from Google Sheets).

    This function is used by parse_google_sheet() in brief_parser/__init__.py
    to process data fetched from the Google Sheets API.

    Args:
        sheets_dict: Dict mapping sheet names to pandas DataFrames

    Returns:
        Tuple of (keywords_list, raw_text, warnings)
    """
    keywords = []
    warnings = []
    raw_texts = []

    for sheet_name, df in sheets_dict.items():
        try:
            df = df.fillna("")

            # Store raw text for SEO check
            raw_texts.append(f"=== Sheet: {sheet_name} ===")
            raw_texts.append(df.to_string(index=False, header=False))

            if df.empty or len(df) < 2:
                continue

            # NEW APPROACH: Scan ALL cells for keyword section headers
            # (e.g., "Main keywords", "Support keywords", "LSI keywords")
            sections = _find_keyword_sections(df)

            if sections:
                # Extract keywords from each found section
                for header_row, keyword_col, group in sections:
                    section_keywords = _extract_keywords_from_section(
                        df, header_row, keyword_col, group
                    )
                    keywords.extend(section_keywords)
            else:
                # FALLBACK: Try the old approach for sheets with traditional headers
                header_row_idx = None
                for i in range(min(5, len(df))):
                    row = df.iloc[i].tolist()
                    if _find_column_index(row, KEYWORD_COLUMN_PATTERNS) is not None:
                        header_row_idx = i
                        break

                if header_row_idx is None:
                    continue

                headers = [str(h) for h in df.iloc[header_row_idx].tolist()]

                keyword_col = _find_column_index(headers, KEYWORD_COLUMN_PATTERNS)
                usage_col = _find_column_index(headers, USAGE_COLUMN_PATTERNS)
                group_col = _find_column_index(headers, GROUP_COLUMN_PATTERNS)

                if keyword_col is None:
                    continue

                current_group = "support"

                for row_idx in range(header_row_idx + 1, len(df)):
                    row = df.iloc[row_idx]
                    keyword = str(row.iloc[keyword_col]).strip() if keyword_col < len(row) else ""

                    if not keyword or keyword.lower() in ["", "nan", "none"]:
                        first_cell = str(row.iloc[0]).strip() if len(row) > 0 else ""
                        detected_group = _detect_group_from_text(first_cell)
                        if detected_group:
                            current_group = detected_group
                        continue

                    if _matches_patterns(keyword, KEYWORD_COLUMN_PATTERNS):
                        continue

                    req_min, req_max = None, None
                    if usage_col is not None and usage_col < len(row):
                        req_min, req_max = _parse_usage_value(row.iloc[usage_col])

                    group = current_group
                    if group_col is not None and group_col < len(row):
                        cell_group = _detect_group_from_text(str(row.iloc[group_col]))
                        if cell_group:
                            group = cell_group

                    section_group = _detect_section_group(df, row_idx)
                    if section_group:
                        group = section_group

                    keywords.append(KeywordSpec(
                        keyword=keyword,
                        group=group,
                        required_min=req_min,
                        required_max=req_max,
                        source="parsed"
                    ))

        except Exception as e:
            warnings.append(f"Error parsing sheet '{sheet_name}': {e}")
            continue

    raw_text = "\n\n".join(raw_texts)

    if not keywords:
        warnings.append("No keywords found in Google Sheets structure. Will use LLM fallback.")

    return keywords, raw_text, warnings
