import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from backend.core.config import LOCAL_AUTH_SECRET

_DATABASE_PATH = Path(__file__).resolve().parents[2] / 'data' / 'local_auth.sqlite3'
_EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _connection() -> sqlite3.Connection:
    _DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS local_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        '''
    )
    return connection


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    password_hash = hashlib.scrypt(
        password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1
    )
    return f'{base64.b64encode(salt).decode()}${base64.b64encode(password_hash).decode()}'


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_encoded, _ = stored_hash.split('$', 1)
        expected_hash = _hash_password(password, base64.b64decode(salt_encoded))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected_hash, stored_hash)


def _validate_credentials(email: str, password: str) -> tuple[str, str]:
    normalized_email = email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(normalized_email):
        raise ValueError('Enter a valid email address.')
    if len(password) < 6:
        raise ValueError('Password must contain at least 6 characters.')
    return normalized_email, password


def create_user(email: str, password: str) -> Dict[str, str]:
    email, password = _validate_credentials(email, password)
    try:
        with _connection() as connection:
            cursor = connection.execute(
                'INSERT INTO local_users (email, password_hash, created_at) VALUES (?, ?, ?)',
                (email, _hash_password(password), datetime.now(timezone.utc).isoformat()),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError('An account with this email already exists. Please sign in.') from exc
    return _session_for_user(str(cursor.lastrowid), email)


def authenticate_user(email: str, password: str) -> Dict[str, str]:
    email, password = _validate_credentials(email, password)
    with _connection() as connection:
        user = connection.execute(
            'SELECT id, email, password_hash FROM local_users WHERE email = ?', (email,)
        ).fetchone()
    if user is None or not _verify_password(password, user['password_hash']):
        raise ValueError('Incorrect email or password.')
    return _session_for_user(str(user['id']), user['email'])


def _session_for_user(user_id: str, email: str) -> Dict[str, str]:
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    payload = json.dumps({
        'sub': f'local:{user_id}', 'email': email, 'provider': 'local',
        'exp': int(expires_at.timestamp()),
    }, separators=(',', ':')).encode('utf-8')
    encoded_payload = base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')
    signature = hmac.new(LOCAL_AUTH_SECRET.encode('utf-8'), encoded_payload.encode('ascii'), hashlib.sha256).digest()
    token = f'local.{encoded_payload}.{base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")}'
    return {'access_token': token, 'user_id': user_id, 'email': email}


def verify_local_token(token: str) -> Optional[str]:
    try:
        prefix, encoded_payload, encoded_signature = token.split('.', 2)
        if prefix != 'local':
            return None
        expected_signature = hmac.new(LOCAL_AUTH_SECRET.encode('utf-8'), encoded_payload.encode('ascii'), hashlib.sha256).digest()
        padding = '=' * (-len(encoded_signature) % 4)
        supplied_signature = base64.urlsafe_b64decode(encoded_signature + padding)
        if not hmac.compare_digest(expected_signature, supplied_signature):
            return None
        payload_padding = '=' * (-len(encoded_payload) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + payload_padding))
        if int(payload.get('exp', 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    subject = payload.get('sub', '')
    if payload.get('provider') != 'local' or not subject.startswith('local:'):
        return None
    return subject
