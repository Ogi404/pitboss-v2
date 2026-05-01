import os

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
GOOGLE_REDIRECT_URI = os.environ.get(
    'GOOGLE_REDIRECT_URI',
    'http://localhost:5000/auth/google/callback'
)

# Scopes for Google Docs and Sheets access
GOOGLE_SCOPES = [
    'https://www.googleapis.com/auth/documents',  # Read/write documents
    'https://www.googleapis.com/auth/drive.file',  # Access files opened via app
    'https://www.googleapis.com/auth/spreadsheets.readonly',  # Read Google Sheets (Phase 8)
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',  # Get user email
]

# Flask session configuration
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
SESSION_TYPE = 'filesystem'
SESSION_FILE_DIR = '.flask_session'

# Model routing for LLM checks
# GPT-4.1 (full) - for checks requiring nuanced language judgment:
#   proofread, seo_structure, fact_check
# GPT-4.1-mini - for structured extraction/classification:
#   keywords (fallback), consistency, style
MODEL_FULL = "gpt-4.1"
MODEL_MINI = "gpt-4.1-mini"

# Character caps to keep LLM calls within token limits
MAX_BRIEF_CHARS = 40000
MAX_ARTICLE_CHARS = 20000
