"""
Brief parser module.

Provides structured parsing of task briefs from Excel, DOCX, and Google Sheets.
Extracts keywords with usage counts and groups, avoiding the need
for LLM-based extraction in most cases.
"""

import os
from .base import BriefData, KeywordSpec
from .sheet_parser import parse_excel_brief, parse_sheets_dataframes
from .doc_parser import parse_docx_brief


__all__ = ['BriefData', 'KeywordSpec', 'parse_brief', 'parse_google_sheet']


def parse_brief(file_storage, task_name: str = None) -> BriefData:
    """
    Parse a brief file and return structured BriefData.

    Automatically detects file type and uses appropriate parser.
    Falls back to raw text extraction if structured parsing fails.

    Args:
        file_storage: Flask FileStorage object with the uploaded file
        task_name: Optional task name to filter to (for multi-task briefs)

    Returns:
        BriefData with keywords (if found) and raw_text
    """
    filename = file_storage.filename or ""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    # Read file bytes
    file_bytes = file_storage.read()
    # Reset stream position for potential re-read
    file_storage.seek(0)

    keywords = []
    raw_text = ""
    warnings = []
    parse_method = "none"

    if ext in [".xls", ".xlsx"]:
        keywords, raw_text, warnings = parse_excel_brief(file_bytes, task_name=task_name)
        parse_method = "excel" if keywords else "none"

    elif ext == ".docx":
        keywords, raw_text, warnings = parse_docx_brief(file_bytes)
        parse_method = "docx" if keywords else "none"

    else:
        # Plain text fallback
        try:
            raw_text = file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            raw_text = ""
        warnings.append(f"Unknown file type '{ext}'. Using raw text only.")

    return BriefData(
        keywords=keywords,
        raw_text=raw_text,
        parse_method=parse_method,
        parse_warnings=warnings,
        task_name=task_name
    )


def parse_google_sheet(sheet_id: str, task_name: str = None) -> BriefData:
    """
    Parse a Google Sheet and return structured BriefData.

    Fetches all sheets from the spreadsheet via the Google Sheets API
    and parses them using the same logic as Excel briefs.

    Args:
        sheet_id: Google Spreadsheet ID
        task_name: Optional task name to filter to (for multi-task briefs)

    Returns:
        BriefData with keywords (if found) and raw_text,
        or BriefData with auth_error flag if authentication failed
    """
    from google_sheets import fetch_spreadsheet_as_dataframes

    # Fetch data from Google Sheets API
    sheets_data = fetch_spreadsheet_as_dataframes(sheet_id)

    # Check for auth error
    if isinstance(sheets_data, dict) and sheets_data.get('auth_error'):
        return BriefData(
            keywords=[],
            raw_text="",
            parse_method="auth_error",
            parse_warnings=["Google authentication expired. Please re-authorize."]
        )

    # Parse the DataFrames
    keywords, raw_text, warnings = parse_sheets_dataframes(sheets_data, task_name=task_name)

    parse_method = "google_sheets" if keywords else "none"

    return BriefData(
        keywords=keywords,
        raw_text=raw_text,
        parse_method=parse_method,
        parse_warnings=warnings,
        task_name=task_name
    )
