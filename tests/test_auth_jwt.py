"""Smoke tests for Cineverse's JWT authentication.

The point of these tests is to make dependency bumps of PyJWT verifiable.
They exercise the application's *own* auth helpers -- not raw PyJWT -- so that
a behavioural change in the library shows up here as a failing test.

Cineverse has two coexisting token-verification entry points, and both are
covered below:

* ``auth_helpers.verify_token``  -- HTTPBearer based; used by protected.py and
  every endpoint in watchlist.py.
* ``auth.get_current_user``      -- OAuth2PasswordBearer based; used by
  cineverse.py.

No database, no network, no real .env (see conftest.py).
"""

import datetime as _datetime
import inspect
import re

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import auth
import auth_helpers
from conftest import TEST_SECRET_KEY

EMAIL = "smoke-test@example.com"
EXPECTED_ALGORITHM = "HS256"


def _bearer(token):
    """Wrap a raw token the way FastAPI's HTTPBearer dependency would."""
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# ---------------------------------------------------------------------------
# Happy path: encode -> decode -> claims round-trip
# ---------------------------------------------------------------------------

def test_create_access_token_returns_a_str():
    """PyJWT 1.x returned bytes from jwt.encode, 2.x returns str.

    The app hands this value straight back in a JSON response body, so a
    regression here would break every client.
    """
    token = auth_helpers.create_access_token(EMAIL)
    assert isinstance(token, str)
    assert token.count(".") == 2


def test_token_header_uses_the_expected_algorithm():
    token = auth_helpers.create_access_token(EMAIL)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == EXPECTED_ALGORITHM
    assert header["typ"] == "JWT"


def test_claims_round_trip_sub_and_expiry():
    """`sub` is the email and `exp` is roughly two hours out."""
    before = _datetime.datetime.utcnow()
    token = auth_helpers.create_access_token(EMAIL)
    payload = jwt.decode(token, TEST_SECRET_KEY, algorithms=[EXPECTED_ALGORITHM])

    assert payload["sub"] == EMAIL

    exp = _datetime.datetime.utcfromtimestamp(payload["exp"])
    delta = exp - before
    # ~2 hours, with a generous window so the test is not clock-flaky.
    assert _datetime.timedelta(hours=1, minutes=55) < delta < _datetime.timedelta(hours=2, minutes=5)


def test_verify_token_accepts_a_freshly_minted_token():
    token = auth_helpers.create_access_token(EMAIL)
    assert auth_helpers.verify_token(_bearer(token)) == EMAIL


def test_get_current_user_accepts_a_freshly_minted_token():
    """The second verification path must agree with the first."""
    token = auth_helpers.create_access_token(EMAIL)
    assert auth.get_current_user(token) == {"email": EMAIL}


# ---------------------------------------------------------------------------
# Expired tokens must be rejected
# ---------------------------------------------------------------------------

@pytest.fixture
def expired_token(monkeypatch):
    """Mint a token through the app's own helper, but with the clock rewound.

    auth_helpers computes ``datetime.utcnow() + timedelta(hours=2)``, so
    pretending "now" was three hours ago yields a token that expired an hour
    ago -- without hand-rolling the payload.
    """

    real_datetime = auth_helpers.datetime

    class _PastDatetime(real_datetime):
        @classmethod
        def utcnow(cls):
            return real_datetime.utcnow() - _datetime.timedelta(hours=3)

    monkeypatch.setattr(auth_helpers, "datetime", _PastDatetime)
    return auth_helpers.create_access_token(EMAIL)


def test_verify_token_rejects_an_expired_token(expired_token):
    with pytest.raises(HTTPException) as exc_info:
        auth_helpers.verify_token(_bearer(expired_token))
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_an_expired_token(expired_token):
    """auth.get_current_user catches jwt.ExpiredSignatureError specifically."""
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(expired_token)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token has expired"


def test_pyjwt_still_raises_expiredsignatureerror_for_expired_tokens(expired_token):
    """Guard the exception type the app catches by name."""
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(expired_token, TEST_SECRET_KEY, algorithms=[EXPECTED_ALGORITHM])


# ---------------------------------------------------------------------------
# Wrong signing key must be rejected
# ---------------------------------------------------------------------------

WRONG_KEY = "an-attacker-controlled-key-that-is-not-the-server-secret"


def _token_signed_with(key, alg=EXPECTED_ALGORITHM, **extra_claims):
    payload = {
        "sub": EMAIL,
        "exp": _datetime.datetime.utcnow() + _datetime.timedelta(hours=2),
    }
    payload.update(extra_claims)
    return jwt.encode(payload, key, algorithm=alg)


def test_verify_token_rejects_a_token_signed_with_the_wrong_key():
    with pytest.raises(HTTPException) as exc_info:
        auth_helpers.verify_token(_bearer(_token_signed_with(WRONG_KEY)))
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_a_token_signed_with_the_wrong_key():
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(_token_signed_with(WRONG_KEY))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


def test_pyjwt_signature_failure_is_still_an_invalidtokenerror():
    """auth.py catches jwt.InvalidTokenError; auth_helpers catches PyJWTError.

    Both are supposed to be superclasses of InvalidSignatureError. If PyJWT
    ever re-parents its exception hierarchy, forged tokens would escape the
    handlers and surface as a 500 instead of a 401 -- catch that here.
    """
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(_token_signed_with(WRONG_KEY), TEST_SECRET_KEY, algorithms=[EXPECTED_ALGORITHM])

    assert issubclass(jwt.InvalidSignatureError, jwt.InvalidTokenError)
    assert issubclass(jwt.InvalidSignatureError, auth_helpers.PyJWTError)
    assert issubclass(jwt.ExpiredSignatureError, jwt.InvalidTokenError)


def test_garbage_and_empty_tokens_are_rejected():
    for bad in ["", "not-a-jwt", "a.b.c"]:
        with pytest.raises(HTTPException):
            auth_helpers.verify_token(_bearer(bad))
        with pytest.raises(HTTPException):
            auth.get_current_user(bad)


# ---------------------------------------------------------------------------
# Algorithm confusion
# ---------------------------------------------------------------------------

def test_unsigned_alg_none_token_is_rejected():
    """The classic algorithm-confusion attack: alg=none, empty signature."""
    unsigned = jwt.encode(
        {"sub": EMAIL, "exp": _datetime.datetime.utcnow() + _datetime.timedelta(hours=2)},
        key="",
        algorithm="none",
    )
    with pytest.raises(HTTPException):
        auth_helpers.verify_token(_bearer(unsigned))
    with pytest.raises(HTTPException):
        auth.get_current_user(unsigned)


@pytest.mark.parametrize(
    "func",
    [auth_helpers.verify_token, auth.get_current_user],
    ids=["auth_helpers.verify_token", "auth.get_current_user"],
)
def test_every_decode_call_site_passes_an_explicit_algorithms_list(func):
    """Regression guard against algorithm-confusion by omission.

    ``jwt.decode(token, key)`` without ``algorithms=`` lets the *token* choose
    its own verification algorithm. Every decode call site in this repo must
    pin the allow-list explicitly.
    """
    source = inspect.getsource(func)
    decode_calls = re.findall(r"jwt\.decode\((.*?)\)", source, re.DOTALL)
    assert decode_calls, f"expected {func.__qualname__} to call jwt.decode"
    for call_args in decode_calls:
        assert "algorithms=" in call_args, (
            f"{func.__qualname__} calls jwt.decode without an explicit "
            f"algorithms= allow-list: jwt.decode({call_args})"
        )


def test_the_two_verification_paths_pin_the_same_algorithm():
    assert auth.ALGORITHM == auth_helpers.ALGORITHM == EXPECTED_ALGORITHM


def test_no_network_or_jwks_client_is_used():
    """Cineverse signs with a local HMAC secret and never fetches a JWKS.

    The PyJWT advisories that prompted this bump are largely PyJWKClient
    issues; assert we do not use it, so the assumption is checked rather than
    just asserted in a PR description.
    """
    for module in (auth, auth_helpers):
        source = inspect.getsource(module)
        assert "PyJWKClient" not in source
        assert "jwks" not in source.lower()
