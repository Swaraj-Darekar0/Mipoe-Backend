from backend.core.config import get_settings


settings = get_settings()


class Config:
    DATABASE_URL = settings.database_url
    SECRET_KEY = settings.secret_key
    TOKEN_CRYPT_KEY = settings.token_crypt_key
    CELERY_BROKER_URL = settings.celery_broker_url
    CELERY_RESULT_BACKEND = settings.celery_result_backend
    JWT_SECRET_KEY = settings.jwt_secret_key
    JWT_ALGORITHM = settings.jwt_algorithm
    SUPABASE_URL = settings.supabase_url
    SUPABASE_KEY = settings.supabase_key
