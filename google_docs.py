"""
Google Docs API wrapper for iGaming Checker.
Handles document fetching, text extraction with positions, and applying corrections.
"""

import re
from flask import session
from googleapiclient.discovery import build
from google_auth import get_credentials


def get_docs_service():
    """Get an authenticated Google Docs API service, or None if re-auth needed."""
    try:
        credentials = get_credentials()
        if not credentials:
            return None
        return build('docs', 'v1', credentials=credentials)
    except Exception as e:
        if 'invalid_grant' in str(e) or 'Token has been' in str(e):
            session.pop('google_credentials', None)
            return None
        raise


def extract_doc_id(url):
    """
    Extract document ID from a Google Docs URL.

    Supports formats:
    - https://docs.google.com/document/d/DOCUMENT_ID/edit
    - https://docs.google.com/document/d/DOCUMENT_ID
    - Just the document ID itself

    Returns:
        Document ID string or None if not found
    """
    if not url:
        return None

    # Pattern for extracting doc ID from URL
    patterns = [
        r'/document/d/([a-zA-Z0-9-_]+)',
        r'^([a-zA-Z0-9-_]{20,})$',  # Just the ID
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def fetch_document(doc_id):
    """
    Fetch a Google Doc by ID.

    Returns:
        The full document object from the API, or {'auth_error': True} if re-auth needed
    """
    try:
        service = get_docs_service()
        if service is None:
            return {'auth_error': True}
        document = service.documents().get(documentId=doc_id).execute()
        return document
    except Exception as e:
        error_str = str(e)
        if 'invalid_grant' in error_str or 'Token has been' in error_str or 'expired' in error_str.lower():
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise


def extract_text_with_positions(document):
    """
    Extract plain text from a Google Doc along with position mapping.

    Google Docs uses 1-based character indices. This function extracts
    all text content while tracking where each piece of text appears
    in the document, enabling accurate find-and-replace operations.

    Args:
        document: The document object from fetch_document()

    Returns:
        tuple: (full_text, position_map)
            - full_text: All text concatenated
            - position_map: List of dicts with text, start_index, end_index
    """
    content = document.get('body', {}).get('content', [])
    position_map = []
    text_parts = []

    for element in content:
        if 'paragraph' in element:
            para = element['paragraph']
            para_start = element.get('startIndex', 1)

            # Extract text from all text runs in the paragraph
            for elem in para.get('elements', []):
                if 'textRun' in elem:
                    text_run = elem['textRun']
                    content_text = text_run.get('content', '')
                    run_start = elem.get('startIndex', para_start)
                    run_end = elem.get('endIndex', run_start + len(content_text))

                    if content_text:
                        position_map.append({
                            'text': content_text,
                            'start_index': run_start,
                            'end_index': run_end
                        })
                        text_parts.append(content_text)

        elif 'table' in element:
            # Handle tables - extract text from each cell
            table = element['table']
            for row in table.get('tableRows', []):
                for cell in row.get('tableCells', []):
                    cell_content = cell.get('content', [])
                    for cell_elem in cell_content:
                        if 'paragraph' in cell_elem:
                            para = cell_elem['paragraph']
                            for elem in para.get('elements', []):
                                if 'textRun' in elem:
                                    text_run = elem['textRun']
                                    content_text = text_run.get('content', '')
                                    run_start = elem.get('startIndex', 1)
                                    run_end = elem.get('endIndex', run_start + len(content_text))

                                    if content_text:
                                        position_map.append({
                                            'text': content_text,
                                            'start_index': run_start,
                                            'end_index': run_end
                                        })
                                        text_parts.append(content_text)

    full_text = ''.join(text_parts)
    return full_text, position_map


def find_text_occurrences(search_text, position_map, context_chars=50):
    """
    Find all occurrences of a text string in the document.

    Args:
        search_text: The text to find
        position_map: The position map from extract_text_with_positions()
        context_chars: Number of characters of context to include

    Returns:
        List of dicts with:
            - start_index: Document index where text starts
            - end_index: Document index where text ends
            - context_before: Text before the match
            - context_after: Text after the match
            - occurrence_num: Which occurrence this is (1-based)
    """
    # Rebuild the full text with position tracking
    full_text = ''.join([p['text'] for p in position_map])

    # Build a mapping from plain text index to document index
    plain_to_doc = {}
    plain_idx = 0
    for segment in position_map:
        seg_len = len(segment['text'])
        for i in range(seg_len):
            plain_to_doc[plain_idx + i] = segment['start_index'] + i
        plain_idx += seg_len

    occurrences = []
    start = 0
    occurrence_num = 0

    while True:
        idx = full_text.find(search_text, start)
        if idx == -1:
            break

        occurrence_num += 1

        # Get document indices
        doc_start = plain_to_doc.get(idx)
        doc_end = plain_to_doc.get(idx + len(search_text) - 1)

        if doc_start is not None and doc_end is not None:
            # Get context
            context_start = max(0, idx - context_chars)
            context_end = min(len(full_text), idx + len(search_text) + context_chars)

            occurrences.append({
                'start_index': doc_start,
                'end_index': doc_end + 1,  # end_index is exclusive
                'context_before': full_text[context_start:idx],
                'context_after': full_text[idx + len(search_text):context_end],
                'occurrence_num': occurrence_num,
                'plain_text_index': idx
            })

        start = idx + 1

    return occurrences


def find_single_occurrence(search_text, position_map):
    """
    Find a single occurrence of text in the document.

    Returns:
        tuple: (start_index, end_index) or (None, None) if not found
    """
    occurrences = find_text_occurrences(search_text, position_map, context_chars=0)
    if occurrences:
        return occurrences[0]['start_index'], occurrences[0]['end_index']
    return None, None


def apply_corrections(doc_id, corrections):
    """
    Apply corrections to a Google Doc using batchUpdate.

    Corrections are applied in reverse order (from end of document to beginning)
    to preserve index validity. This also helps preserve comment anchors for
    unchanged text.

    Args:
        doc_id: The Google Doc ID
        corrections: List of dicts with:
            - start_index: Where the original text starts
            - end_index: Where the original text ends
            - corrected: The replacement text

    Returns:
        dict: Result from the API with applied_count and any failures,
              or {'auth_error': True} if re-auth needed
    """
    try:
        service = get_docs_service()
        if service is None:
            return {'auth_error': True}
    except Exception as e:
        error_str = str(e)
        if 'invalid_grant' in error_str or 'Token has been' in error_str or 'expired' in error_str.lower():
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise

    # Sort by position descending (process from end to beginning)
    sorted_corrections = sorted(
        corrections,
        key=lambda c: c['start_index'],
        reverse=True
    )

    requests = []
    for corr in sorted_corrections:
        # Delete the original text
        requests.append({
            'deleteContentRange': {
                'range': {
                    'startIndex': corr['start_index'],
                    'endIndex': corr['end_index']
                }
            }
        })

        # Insert the corrected text at the same position
        requests.append({
            'insertText': {
                'location': {'index': corr['start_index']},
                'text': corr['corrected']
            }
        })

    result = {
        'applied_count': 0,
        'failed': []
    }

    if requests:
        try:
            service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests}
            ).execute()
            result['applied_count'] = len(sorted_corrections)
        except Exception as e:
            error_str = str(e)
            if 'invalid_grant' in error_str or 'Token has been' in error_str or 'expired' in error_str.lower():
                session.pop('google_credentials', None)
                return {'auth_error': True}
            result['failed'].append({
                'error': error_str
            })

    return result


def get_document_url(doc_id):
    """Get the URL to open a document in Google Docs."""
    return f"https://docs.google.com/document/d/{doc_id}/edit"


def get_drive_service():
    """Get an authenticated Google Drive API service, or None if re-auth needed."""
    try:
        credentials = get_credentials()
        if not credentials:
            return None
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        if 'invalid_grant' in str(e) or 'Token has been' in str(e):
            session.pop('google_credentials', None)
            return None
        raise


def apply_comments(doc_id, comments):
    """
    Apply editorial comments to a Google Doc.

    Uses Google Drive API v3 comments endpoint since Google Docs API
    doesn't support comments directly.

    Note: The Google Drive comments API uses a different anchor format.
    If position-based anchoring doesn't work reliably, comments will
    be added at the document level.

    Args:
        doc_id: The Google Doc file ID
        comments: List of dicts with:
            - content: The comment text
            - anchor_start: Start index (optional)
            - anchor_end: End index (optional)

    Returns:
        dict: Result with applied_count and failed list,
              or {'auth_error': True} if re-auth needed
    """
    import json

    try:
        service = get_drive_service()
        if service is None:
            return {'auth_error': True}
    except Exception as e:
        error_str = str(e)
        if 'invalid_grant' in error_str or 'Token has been' in error_str or 'expired' in error_str.lower():
            session.pop('google_credentials', None)
            return {'auth_error': True}
        raise

    result = {
        'applied_count': 0,
        'failed': []
    }

    for comment in comments:
        try:
            body = {
                'content': comment['content']
            }

            # Try to add position anchor if we have position info
            # Note: Drive API comment anchors use a specific format
            # that may not work reliably with document indices
            if comment.get('anchor_start') is not None:
                try:
                    # Google Drive comment anchor format (reverse-engineered)
                    # This anchors the comment to a text range
                    anchor_data = {
                        'r': doc_id,
                        'a': [{
                            'txt': {
                                'o': comment['anchor_start'],
                                'l': comment.get('anchor_end', comment['anchor_start'] + 1) - comment['anchor_start']
                            }
                        }]
                    }
                    body['anchor'] = json.dumps(anchor_data)
                except Exception:
                    # If anchor fails, comment will be added without position
                    pass

            service.comments().create(
                fileId=doc_id,
                fields='id,content',
                body=body
            ).execute()

            result['applied_count'] += 1

        except Exception as e:
            error_str = str(e)
            if 'invalid_grant' in error_str or 'Token has been' in error_str or 'expired' in error_str.lower():
                session.pop('google_credentials', None)
                return {'auth_error': True}
            result['failed'].append({
                'comment': comment['content'][:100] + '...' if len(comment['content']) > 100 else comment['content'],
                'error': error_str
            })

    return result
