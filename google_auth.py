"""
Google OAuth 2.0 authentication helpers for iGaming Checker.
Handles the OAuth flow for accessing Google Docs.
"""

from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from flask import session
import config


def create_oauth_flow():
    """Create an OAuth 2.0 flow instance."""
    if not config.GOOGLE_CLIENT_ID or not config.GOOGLE_CLIENT_SECRET:
        raise ValueError(
            "Google OAuth credentials not configured. "
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
        )

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config.GOOGLE_CLIENT_ID,
                "client_secret": config.GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=config.GOOGLE_SCOPES,
        redirect_uri=config.GOOGLE_REDIRECT_URI
    )
    return flow


def get_authorization_url():
    """Generate the Google OAuth authorization URL."""
    flow = create_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['oauth_state'] = state
    return auth_url


def handle_oauth_callback(authorization_response):
    """
    Handle the OAuth callback and store credentials in session.

    Args:
        authorization_response: The full callback URL with auth code

    Returns:
        The user's email address
    """
    flow = create_oauth_flow()
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials

    # Store credentials in session
    session['google_credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': list(credentials.scopes) if credentials.scopes else []
    }

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
