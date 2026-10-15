import base64
import re

import pytest

from srx_api.api_tokens import generate_api_token, token_fingerprint, verify_api_token


def test_generated_token_encodes_32_random_bytes():
    token = generate_api_token()
    assert token.startswith("ak_v1_")
    encoded = token.removeprefix("ak_v1_")
    assert len(base64.urlsafe_b64decode(encoded + "=")) == 32
    assert re.fullmatch(r"ak_v1_[A-Za-z0-9_-]{43}", token)


def test_fingerprint_is_stable_and_verifies():
    token = generate_api_token()
    pepper = b"p" * 32
    fingerprint = token_fingerprint(token, pepper)
    assert len(fingerprint) == 64
    assert token_fingerprint(token, pepper) == fingerprint
    assert verify_api_token(token, fingerprint, pepper)
    assert not verify_api_token(generate_api_token(), fingerprint, pepper)


def test_fingerprint_rejects_weak_pepper_and_malformed_token():
    with pytest.raises(ValueError):
        token_fingerprint(generate_api_token(), "short")
    with pytest.raises(ValueError):
        token_fingerprint("07f1e80ce609c0ad98254ca132b40ea67f32b7f53526bd4810c76de73fb1cc4f", b"p" * 32)


def test_verification_fails_closed_for_bad_inputs():
    assert not verify_api_token("bad", "not-a-fingerprint", b"p" * 32)
    assert not verify_api_token("bad", "0" * 64, b"p" * 32)
