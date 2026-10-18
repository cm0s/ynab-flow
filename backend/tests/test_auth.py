import time

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from jose.utils import base64url_encode

from src import auth


@pytest.fixture(autouse=True)
def configured_auth(monkeypatch):
    monkeypatch.setattr(auth, 'SUPABASE_URL', 'https://test.supabase.co')
    monkeypatch.setattr(auth, 'SUPABASE_JWT_SECRET', 'test-signing-secret')
    monkeypatch.setattr(auth, 'ADMIN_USER_ID', 'admin')
    monkeypatch.setattr(auth, 'AUTH_REQUIRED', True)
    monkeypatch.setattr(auth, '_jwks_expires', 0)


def token(**overrides):
    claims = dict(sub='admin', role='authenticated', aud='authenticated',
                  iss='https://test.supabase.co/auth/v1', exp=int(time.time()) + 60)
    claims.update(overrides)
    return jwt.encode(claims, 'test-signing-secret', algorithm='HS256')


def verify(value):
    return auth.verify_token(HTTPAuthorizationCredentials(scheme='Bearer', credentials=value))


def test_admin_token():
    assert verify(token()) == 'admin'


@pytest.mark.parametrize('claims,code', [
    ({'exp': 1}, 401), ({'aud': 'other'}, 401), ({'iss': 'https://other'}, 401),
    ({'sub': ''}, 403), ({'sub': 'other-user'}, 403), ({'role': 'anon'}, 403),
])
def test_rejected_claims(claims, code):
    with pytest.raises(HTTPException) as error:
        verify(token(**claims))
    assert error.value.status_code == code


@pytest.mark.parametrize('value', ['garbage', token()[:-5] + 'wrong'])
def test_invalid_token(value):
    with pytest.raises(HTTPException) as error:
        verify(value)
    assert error.value.status_code == 401


def test_missing_token():
    with pytest.raises(HTTPException) as error:
        auth.verify_token(None)
    assert error.value.status_code == 401


def test_hosted_config_fails_closed(monkeypatch):
    monkeypatch.setattr(auth, 'SUPABASE_URL', '')
    monkeypatch.setattr(auth, 'SUPABASE_JWT_SECRET', '')
    with pytest.raises(RuntimeError):
        auth.validate_auth_config()


def test_local_dev(monkeypatch):
    monkeypatch.setattr(auth, 'AUTH_REQUIRED', False)
    monkeypatch.setattr(auth, 'SUPABASE_URL', '')
    monkeypatch.setattr(auth, 'SUPABASE_JWT_SECRET', '')
    assert auth.verify_token(None) == 'dev-user'


def test_asymmetric_signing_and_jwks_cache(respx_mock):
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    encode = lambda value: base64url_encode(value.to_bytes(32, 'big')).decode()
    jwk = dict(kty='EC', crv='P-256', alg='ES256', use='sig', kid='test-key',
               x=encode(numbers.x), y=encode(numbers.y))
    route = respx_mock.get('https://test.supabase.co/auth/v1/.well-known/jwks.json').mock(
        return_value=httpx.Response(200, json={'keys': [jwk]}))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    claims = jwt.get_unverified_claims(token())
    signed = jwt.encode(claims, pem, algorithm='ES256', headers={'kid': 'test-key'})
    assert verify(signed) == 'admin'
    assert verify(signed) == 'admin'
    assert route.call_count == 1


def test_jwks_unavailable(respx_mock):
    respx_mock.get('https://test.supabase.co/auth/v1/.well-known/jwks.json').mock(
        return_value=httpx.Response(503))
    with pytest.raises(HTTPException) as error:
        auth._get_jwks()
    assert error.value.status_code == 503


def test_http_auth_and_cors():
    from fastapi.testclient import TestClient
    from src.main import app
    from src.database import get_db

    class EmptyQuery:
        def outerjoin(self, *args): return self
        def group_by(self, *args): return self
        def order_by(self, *args): return self
        def all(self): return []

    class EmptyDatabase:
        def query(self, *args): return EmptyQuery()

    app.dependency_overrides[get_db] = lambda: EmptyDatabase()
    try:
        client = TestClient(app)
        assert client.get('/health').status_code == 200
        assert client.get('/plans').status_code == 401
        assert client.get('/plans', headers={'Authorization': f'Bearer {token(sub="other")}'}).status_code == 403
        assert client.get('/plans', headers={'Authorization': f'Bearer {token()}'}).json() == []
        response = client.options('/plans', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'authorization',
        })
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
        response = client.options('/plans', headers={
            'Origin': 'https://untrusted.example', 'Access-Control-Request-Method': 'GET',
        })
        assert 'access-control-allow-origin' not in response.headers
    finally:
        app.dependency_overrides.clear()


def test_all_business_routes_require_auth():
    from fastapi.routing import APIRoute
    from src.main import app
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path != '/health':
            assert any(dep.call is auth.verify_token for dep in route.dependant.dependencies), route.path
