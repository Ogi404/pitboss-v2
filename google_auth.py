"""
Google OAuth 2.0 authentication helpers for iGaming Checker.
Handles the OAuth flow for accessing Google Docs.

Uses manual OAuth implementation without PKCE to avoid session
persistence issues with Flask file-based sessions across redirects.
"""

import secrets
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from flask import session
import config


def generate_random_state():
    """Generate a random state string for CSRF protection."""
    return secrets.token_urlsafe(32)


def get_authorization_url():
    """
    Generate the Google OAuth authorization URL manually (no PKCE).

    Bypasses google_auth_oauthlib.flow.Flow to avoid PKCE code_verifier
    session persistence issues.
    """
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "Google OAuth credentials not configured. "
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
        )

    # Generate and store state for CSRF protection
    state = generate_random_state()
    session['oauth_state'] = state

    # Build authorization URL manually - no PKCE
    params = {
        'client_id': config.GOOGLE_CLIENT_ID,
        'redirect_uri': config.GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(config.GOOGLE_SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
        'include_granted_scopes': 'true',
    }

    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)
    return auth_url


def handle_oauth_callback(authorization_response):
    """
    Handle the OAuth callback and store credentials in session.

    Uses manual token exchange without PKCE.

    Args:
        authorization_response: The full callback URL with auth code

    Returns:
        The user's email address
    """
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "Google OAuth credentials not configured. "
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
        )

    # Parse the authorization code from the callback URL
    parsed = urlparse(authorization_response)
    query_params = parse_qs(parsed.query)

    if 'error' in query_params:
        raise ValueError(f"OAuth error: {query_params['error'][0]}")

    code = query_params.get('code', [None])[0]
    if not code:
        raise ValueError("No authorization code in callback")

    # Exchange code for tokens manually (no PKCE)
    token_response = requests.post(
        'https://oauth2.googleapis.com/token',
        data={
            'code': code,
            'client_id': config.GOOGLE_CLIENT_ID,
            'client_secret': config.GOOGLE_CLIENT_SECRET,
            'redirect_uri': config.GOOGLE_REDIRECT_URI,
            'grant_type': 'authorization_code',
        },
        timeout=30
    )

    if token_response.status_code != 200:
        error_data = token_response.json()
        raise ValueError(f"Token exchange failed: {error_data.get('error_description', error_data.get('error', 'Unknown error'))}")

    tokens = token_response.json()

    # Store credentials in session
    session['google_credentials'] = {
        'token': tokens.get('access_token'),
        'refresh_token': tokens.get('refresh_token'),
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': config.GOOGLE_CLIENT_ID,
        'client_secret': config.GOOGLE_CLIENT_SECRET,
        'scopes': tokens.get('scope', '').split() if tokens.get('scope') else config.GOOGLE_SCOPES
    }

    # Build credentials object for API calls
    credentials = Credentials(
        token=tokens.get('access_token'),
        refresh_token=tokens.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        scopes=config.GOOGLE_SCOPES
    )

    # Get user email
    try:
        oauth2_service = build('oauth2', 'v2', credentials=credentials)
        user_info = oauth2_service.userinfo().get().execute()
        email = user_info.get('email', 'Unknown')
        session['google_email'] = email
        return email
    except Exception:
        session['google_email'] = 'Connected'
        return 'Connected'


def get_credentials():
    """
    Retrieve stored credentials from session.

    Returns:
        Credentials object or None if not authenticated
    """
    if 'google_credentials' not in session:
        return None

    creds_data = session['google_credentials']
    credentials = Credentials(
        token=creds_data['token'],
        refresh_token=creds_data.get('refresh_token'),
        token_uri=creds_data['token_uri'],
        client_id=creds_data['client_id'],
        client_secret=creds_data['client_secret'],
        scopes=creds_data.get('scopes', [])
    )

    return credentials


def is_authorized():
    """Check if user has valid Google credentials in session."""
    return 'google_credentials' in session


def get_user_email():
    """Get the authenticated user's email address."""
    return session.get('google_email', None)


def clear_credentials():
    """Clear stored credentials from session."""
    session.pop('google_credentials', None)
    session.pop('google_email', None)
    session.pop('oauth_state', None)
