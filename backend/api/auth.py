import logging

try:
    import jwt
except ImportError:
    jwt = None

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.config import AUTH_REQUIRED, SUPABASE_JWT_SECRET, SUPABASE_URL

logger = logging.getLogger('ats_resume_scorer')
_bearer_scheme = HTTPBearer(auto_error=False)
_ASYMMETRIC_ALGS = ['ES256', 'RS256']
_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    if not SUPABASE_URL or jwt is None:
        return None
    _jwks_client = jwt.PyJWKClient(
        f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
        lifespan=3600,
    )
    return _jwks_client


def _verify_supabase_token(token: str) -> dict:
    if jwt is None:
        raise ValueError('Supabase verification requires the optional PyJWT package.')
    algorithm = jwt.get_unverified_header(token).get('alg')
    if algorithm in _ASYMMETRIC_ALGS:
        jwks_client = _get_jwks_client()
        if jwks_client is None:
            raise ValueError('SUPABASE_URL is not configured.')
        return jwt.decode(token, jwks_client.get_signing_key_from_jwt(token).key, algorithms=_ASYMMETRIC_ALGS, audience='authenticated')
    if algorithm == 'HS256' and SUPABASE_JWT_SECRET:
        return jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=['HS256'], audience='authenticated')
    raise ValueError('Unsupported token or missing Supabase configuration.')


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme)) -> str:
    if creds and creds.credentials:
        from backend.database.local_auth import verify_local_token
        local_user_id = verify_local_token(creds.credentials)
        if local_user_id:
            return local_user_id

    if not AUTH_REQUIRED:
        return 'guest'
    if not creds or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Please sign in.', headers={'WWW-Authenticate': 'Bearer'})
    try:
        payload = _verify_supabase_token(creds.credentials)
    except Exception as exc:
        logger.warning('Token verification failed: %s', exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired sign-in token.', headers={'WWW-Authenticate': 'Bearer'}) from exc
    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token has no user identity.')
    return user_id
