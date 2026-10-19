import base64

import pytest
from cryptography.fernet import Fernet

from pipeline_ai.config import settings
from pipeline_ai.security import secretbox, totp

# Vectores de prueba de RFC 6238, Apéndice B (SHA-1, 8 dígitos, secreto ASCII de 20 bytes).
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_vectors(timestamp, expected):
    assert totp.totp(RFC_SECRET, timestamp, digits=8) == expected


def test_generated_secrets_are_160_bit_base32_and_unique():
    first, second = totp.generate_secret(), totp.generate_secret()

    assert first != second
    assert len(first) == 32 and set(first) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert len(base64.b32decode(first)) == 20


def test_verify_accepts_current_and_adjacent_steps_only():
    secret, now = totp.generate_secret(), 1_700_000_000
    for offset, accepted in ((-90, False), (-30, True), (0, True), (30, True), (90, False)):
        code = totp.totp(secret, now + offset)
        assert (totp.verify(secret, code, timestamp=now) is not None) is accepted


def test_verify_returns_the_matching_step():
    secret, now = totp.generate_secret(), 1_700_000_000

    assert totp.verify(secret, totp.totp(secret, now), timestamp=now) == totp.step_for(now)


def test_replay_of_an_already_used_step_is_rejected():
    secret, now = totp.generate_secret(), 1_700_000_000
    code = totp.totp(secret, now)
    step = totp.verify(secret, code, timestamp=now)

    assert totp.verify(secret, code, timestamp=now, last_step=step) is None
    # Un paso anterior al último aceptado tampoco sirve.
    older = totp.totp(secret, now - 30)
    assert totp.verify(secret, older, timestamp=now, last_step=step) is None
    # El siguiente paso sí.
    newer = totp.totp(secret, now + 30)
    assert totp.verify(secret, newer, timestamp=now, last_step=step) == step + 1


@pytest.mark.parametrize(
    "bad", ["", "12345", "1234567", "abcdef", "12 456", "１２３４５６", "+12345"]
)
def test_malformed_codes_are_rejected_before_any_comparison(bad):
    secret, now = totp.generate_secret(), 1_700_000_000
    assert totp.verify(secret, bad, timestamp=now) is None


def test_wrong_code_is_rejected():
    secret, now = totp.generate_secret(), 1_700_000_000
    real = totp.totp(secret, now)
    wrong = str((int(real) + 1) % 10**6).zfill(6)
    neighbours = {totp.totp(secret, now + d) for d in (-30, 0, 30)}
    if wrong in neighbours:
        wrong = str((int(wrong) + 7) % 10**6).zfill(6)

    assert totp.verify(secret, wrong, timestamp=now) is None


def test_provisioning_uri_carries_issuer_and_secret():
    uri = totp.provisioning_uri("ABCDEFGH", "ana@mh", issuer="MH IA")

    assert uri.startswith("otpauth://totp/MH%20IA%3Aana%40mh?")
    assert "secret=ABCDEFGH" in uri and "issuer=MH%20IA" in uri
    assert "digits=6" in uri and "period=30" in uri


# -- Cifrado en reposo del secreto -------------------------------------------------------


def test_secretbox_roundtrip_and_ciphertext_hides_the_secret(monkeypatch):
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())

    token = secretbox.encrypt("MISECRETOTOTP")

    assert "MISECRETOTOTP" not in token
    assert secretbox.decrypt(token) == "MISECRETOTOTP"


def test_secretbox_fails_explicitly_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "api_totp_key", "")

    with pytest.raises(secretbox.SecretBoxError, match="API_TOTP_KEY"):
        secretbox.encrypt("x")


def test_secretbox_rejects_an_invalid_key_without_echoing_it(monkeypatch):
    monkeypatch.setattr(settings, "api_totp_key", "clave-rota")

    with pytest.raises(secretbox.SecretBoxError) as excinfo:
        secretbox.encrypt("x")
    assert "clave-rota" not in str(excinfo.value)


def test_secretbox_cannot_decrypt_with_another_key(monkeypatch):
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())
    token = secretbox.encrypt("MISECRETOTOTP")
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())

    with pytest.raises(secretbox.SecretBoxError):
        secretbox.decrypt(token)
