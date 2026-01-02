import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Supabase Configuration
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_KEY = os.getenv('SUPABASE_KEY')
    SUPABASE_POOL_SIZE = int(os.getenv('SUPABASE_POOL_SIZE', 10))

    # SQLALCHEMY_DATABASE_URI = 'postgresql://postgres:root@localhost/Sindi' # Removed
    SQLALCHEMY_TRACK_MODIFICATIONS = False # Keep this for now, may be removed later if not needed
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'supersecretkey'
    # AES encryption key (32 bytes for AES-256)
    TOKEN_CRYPT_KEY = os.environ.get('TOKEN_CRYPT_KEY', '32_byte_hex_or_base64_key_here')
    
    
    
    # Celery & Redis
    CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = CELERY_BROKER_URL

    # ---------------------------------------------------------------------
    # JWT configuration
    # By default Flask-JWT-Extended sets the access-token expiry to 15 minutes.
    # For local development we increase it to one day so you don't have to
    # re-login constantly while testing.
    # ---------------------------------------------------------------------
    JWT_SECRET_KEY=os.getenv('JWT_SECRET_KEY')
     # Add this for debugging
    JWT_TOKEN_LOCATION = ['headers']
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=60)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
    JWT_BLACKLIST_ENABLED = True
    JWT_BLACKLIST_TOKEN_CHECKS = ['access', 'refresh']
    JWT_ALGORITHM=os.getenv('JWT_ALGORITHM', 'HS256')
    JWT_IDENTITY_CLAIM = 'sub'