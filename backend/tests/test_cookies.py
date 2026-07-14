from __future__ import annotations

import json
import time
from urllib.request import Request

import pytest
from cryptography.fernet import Fernet

from app.core.exceptions import AppError, ErrorCode
from app.security.cookies import CookieCipher, CookieFileParser


def cookie_header(jar: object, url: str) -> str:
    request = Request(url)
    jar.add_cookie_header(request)
    return request.get_header("Cookie", "")


def test_cookie_parser_preserves_semantics_and_discards_other_domains(
    valid_cookie_json: bytes,
) -> None:
    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    assert parsed.accepted_count == 2
    assert parsed.discarded_count == 1
    assert parsed.expires_at is not None
    api_header = cookie_header(parsed.jar, "https://api.bilibili.com/x/web-interface/nav")
    assert "SESSDATA=test-session-value" in api_header
    assert "bili_jct=test-csrf-value" in api_header
    assert cookie_header(parsed.jar, "https://example.com/") == ""
    cookies = {item.name: item for item in parsed.jar}
    assert cookies["SESSDATA"].secure is True
    assert cookies["SESSDATA"].path == "/"
    assert cookies["bili_jct"].expires is None
    assert cookies["bili_jct"].discard is True


def test_cookie_parser_rejects_expired_and_oversized_files() -> None:
    parser = CookieFileParser(max_bytes=256, max_items=10)
    expired = json.dumps(
        [
            {
                "name": "SESSDATA",
                "value": "expired-test-value",
                "domain": ".bilibili.com",
                "path": "/",
                "expires": int(time.time()) - 10,
                "secure": True,
            }
        ]
    ).encode()
    with pytest.raises(AppError) as expired_error:
        parser.parse(expired)
    assert expired_error.value.code == ErrorCode.AUTH_EXPIRED
    with pytest.raises(AppError) as size_error:
        parser.parse(b"[" + b" " * 300 + b"]")
    assert size_error.value.code == ErrorCode.UPLOAD_TOO_LARGE


def test_cookie_parser_treats_expiration_at_current_second_as_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_second = 2_000_000_000
    monkeypatch.setattr(time, "time", lambda: float(current_second))
    payload = json.dumps(
        [
            {
                "name": "SESSDATA",
                "value": "boundary-expiration-value",
                "domain": ".bilibili.com",
                "path": "/",
                "expires": current_second,
                "secure": True,
            }
        ]
    ).encode()

    with pytest.raises(AppError) as caught:
        CookieFileParser(max_bytes=1_024, max_items=10).parse(payload)

    assert caught.value.code == ErrorCode.AUTH_EXPIRED


def test_cookie_parser_rejects_malformed_values() -> None:
    parser = CookieFileParser(max_bytes=1_024, max_items=10)
    for payload in (
        b"{}",
        b"not-json",
        json.dumps([{"name": "bad name", "value": "x", "domain": ".bilibili.com"}]).encode(),
        json.dumps([{"name": "ok", "value": "x\n", "domain": ".bilibili.com"}]).encode(),
    ):
        with pytest.raises(AppError) as caught:
            parser.parse(payload)
        assert caught.value.code == ErrorCode.AUTH_FORMAT_INVALID


@pytest.mark.parametrize(
    "expiration",
    [
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        1e308,
        "NaN",
        "Infinity",
        "-Infinity",
        10**400,
    ],
)
def test_cookie_parser_rejects_nonfinite_or_unrepresentable_expiration(
    expiration: object,
) -> None:
    payload = json.dumps(
        [
            {
                "name": "SESSDATA",
                "value": "bounded-expiration-test",
                "domain": ".bilibili.com",
                "expires": expiration,
            }
        ]
    ).encode()

    with pytest.raises(AppError) as caught:
        CookieFileParser(max_bytes=16_384, max_items=10).parse(payload)

    assert caught.value.code == ErrorCode.AUTH_FORMAT_INVALID


def test_cookie_parser_rejects_excessive_json_depth() -> None:
    parser = CookieFileParser(max_bytes=16_384, max_items=10)
    deeply_nested_json = ("[" * 1_100 + "0" + "]" * 1_100).encode()
    nested_extra = json.dumps(
        [
            {
                "name": "SESSDATA",
                "value": "safe-value",
                "domain": ".bilibili.com",
                "extra": [[[[[[[["too-deep"]]]]]]]],
            }
        ]
    ).encode()

    for payload in (deeply_nested_json, nested_extra):
        with pytest.raises(AppError) as caught:
            parser.parse(payload)
        assert caught.value.code == ErrorCode.AUTH_FORMAT_INVALID


def test_cookie_cipher_round_trip_and_wrong_key(valid_cookie_json: bytes) -> None:
    parsed = CookieFileParser(max_bytes=1_048_576, max_items=100).parse(valid_cookie_json)
    cipher = CookieCipher(Fernet.generate_key())
    encrypted = cipher.encrypt(parsed.jar)
    assert b"test-session-value" not in encrypted
    restored = cipher.decrypt(encrypted)
    assert "SESSDATA=test-session-value" in cookie_header(
        restored, "https://api.bilibili.com/x/web-interface/nav"
    )
    with pytest.raises(AppError) as caught:
        CookieCipher(Fernet.generate_key()).decrypt(encrypted)
    assert caught.value.code == ErrorCode.AUTH_VALIDATION
