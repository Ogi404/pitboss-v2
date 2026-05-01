"""
Google Sheets API wrapper for iGaming Checker.
Handles spreadsheet fetching and data extraction for brief parsing.

Phase 8: Google Sheets Brief Support
"""

import re
from flask import session
from googleapiclient.discovery import build
from google_auth import get_credentials
import pandas as pd


def get_sheets_service():
    """Get an authenticated Google Sheets API service, or None if re-auth needed."""
    try:
        credentials = get_credentials()
        if not credentials:
            return None
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        if 'invalid_grant' in str(e) or 'Token has been' in str(e):
            session.pop('google_credentials', None)
            return None
        raise


def extract_sheet_id(url):
    """
    Extract spreadsheet ID from a Google Sheets URL.

    Supports formats:
    - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
    - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=0
    - https://docs.google.com/spreadsheets/d/SPREADSHEET_ID
    - Just the spreadsheet ID itself

    Returns:
        Spreadsheet ID string or None if not found
    """
    if not url:
        return None

    # Pattern for extracting spreadsheet ID from URL
    patterns = [
        r'/spreadsheets/d/([a-zA-Z0-9-_]+)',
        r'^([a-zA-Z0-9-_]{20,})$',  # Just the ID
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def get_sheet_names(sheet_id):
    """
    Get list of sheet/tab names in a spreadsheet.

    Args:
        sheet_id: Google Spreadsheet ID

    Returns:
        List of sheet names, or None if auth error
    """
    service = get_sheets_service()
    if not service:
        return {'auth_error': True}

    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        return [sheet['properties']['title'] for sheet in sheets]
    except Exception as e:
        if 'invalid_grant' in str(e) or '401' in str(e):
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise


def fetch_sheet_data(sheet_id, sheet_name=None):
    """
    Fetch data from a Google Sheet.

    Args:
        sheet_id: Google Spreadsheet ID
        sheet_name: Optional specific sheet/tab name. If None, fetches first sheet.

    Returns:
        List of rows (each row is a list of cell values), or dict with auth_error
    """
    service = get_sheets_service()
    if not service:
        return {'auth_error': True}

    try:
        # If no sheet name specified, get the first sheet
        if not sheet_name:
            spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
            sheets = spreadsheet.get('sheets', [])
            if sheets:
                sheet_name = sheets[0]['properties']['title']
            else:
                return []

        # Fetch all data from the sheet
        range_name = f"'{sheet_name}'"
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=range_name,
            valueRenderOption='FORMATTED_VALUE'
        ).execute()

        return result.get('values', [])

    except Exception as e:
        if 'invalid_grant' in str(e) or '401' in str(e):
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise


def fetch_all_sheets_data(sheet_id):
    """
    Fetch data from all sheets/tabs in a spreadsheet.

    Args:
        sheet_id: Google Spreadsheet ID

    Returns:
        Dict mapping sheet names to their data (list of rows),
        or dict with auth_error
    """
    service = get_sheets_service()
    if not service:
        return {'auth_error': True}

    try:
        # Get all sheet names
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get('sheets', [])

        all_data = {}
        for sheet in sheets:
            sheet_name = sheet['properties']['title']
            range_name = f"'{sheet_name}'"

            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=range_name,
                valueRenderOption='FORMATTED_VALUE'
            ).execute()

            all_data[sheet_name] = result.get('values', [])

        return all_data

    except Exception as e:
        if 'invalid_grant' in str(e) or '401' in str(e):
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise


def sheet_data_to_dataframe(rows, sheet_name="Sheet1"):
    """
    Convert sheet data (list of rows) to a pandas DataFrame.

    Args:
        rows: List of rows from fetch_sheet_data()
        sheet_name: Name of the sheet (for metadata)

    Returns:
        pandas DataFrame with the sheet data
    """
    if not rows:
        return pd.DataFrame()

    # Normalize row lengths (pad shorter rows with empty strings)
    max_cols = max(len(row) for row in rows) if rows else 0
    normalized_rows = [
        row + [''] * (max_cols - len(row))
        for row in rows
    ]

    # Create DataFrame without headers (let sheet_parser detect them)
    df = pd.DataFrame(normalized_rows)
    df = df.fillna('')

    return df


def fetch_spreadsheet_as_dataframes(sheet_id):
    """
    Fetch all sheets from a spreadsheet as pandas DataFrames.

    This is the main function used by brief_parser to get structured data
    from a Google Sheet, similar to how pd.ExcelFile works for Excel files.

    Args:
        sheet_id: Google Spreadsheet ID

    Returns:
        Dict mapping sheet names to DataFrames, or dict with auth_error
    """
    all_data = fetch_all_sheets_data(sheet_id)

    if isinstance(all_data, dict) and all_data.get('auth_error'):
        return all_data

    dataframes = {}
    for sheet_name, rows in all_data.items():
        dataframes[sheet_name] = sheet_data_to_dataframe(rows, sheet_name)

    return dataframes
