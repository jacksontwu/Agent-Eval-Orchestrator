import time

import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)


UNIT_SECRET = "unit-secret-with-at-least-32-bytes"
OTHER_SECRET = "other-secret-with-at-least-32-bytes"


def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_access_token_round_trip():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret=UNIT_SECRET,
        ttl_seconds=60,
    )

    payload = verify_access_token(token, secret=UNIT_SECRET)

    assert payload.subject == "alice"
    assert payload.source == "db"
    assert payload.groups == ["user"]
    assert payload.permissions == ["tasks.create"]


def test_access_token_rejects_wrong_secret():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret=UNIT_SECRET,
        ttl_seconds=60,
    )

    with pytest.raises(InvalidTokenError):
        verify_access_token(token, secret=OTHER_SECRET)


def test_access_token_rejects_expired_token():
    token = create_access_token(
        subject="alice",
        source="db",
        groups=["user"],
        permissions=["tasks.create"],
        secret=UNIT_SECRET,
        ttl_seconds=-1,
    )
    time.sleep(0.01)

    with pytest.raises(InvalidTokenError):
        verify_access_token(token, secret=UNIT_SECRET)
