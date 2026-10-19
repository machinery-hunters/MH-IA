import pytest
from argon2 import PasswordHasher

from pipeline_ai.security import passwords


def test_hash_is_argon2id_and_salted():
    first = passwords.hash_password("una contraseña larga")
    second = passwords.hash_password("una contraseña larga")

    assert first.startswith("$argon2id$")
    assert first != second  # sal aleatoria
    assert "una contraseña larga" not in first


def test_verify_accepts_the_right_password_and_rejects_others():
    stored = passwords.hash_password("una contraseña larga")

    assert passwords.verify_password(stored, "una contraseña larga") is True
    assert passwords.verify_password(stored, "otra contraseña larga") is False
    assert passwords.verify_password(stored, "") is False


def test_verify_never_raises_on_garbage_hashes():
    assert passwords.verify_password("no-es-un-hash", "x" * 20) is False
    assert passwords.verify_password("", "x" * 20) is False


def test_verify_rejects_oversized_passwords_without_hashing_them():
    stored = passwords.hash_password("una contraseña larga")

    assert passwords.verify_password(stored, "a" * (passwords.MAX_LENGTH + 1)) is False


def test_needs_rehash_detects_weaker_parameters():
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("una contraseña larga")

    assert passwords.needs_rehash(weak) is True
    assert passwords.needs_rehash(passwords.hash_password("una contraseña larga")) is False
    assert passwords.needs_rehash("basura") is True


def test_dummy_verification_does_not_raise():
    passwords.verify_dummy("lo que sea")


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("corta", "al menos"),
        ("a" * 300, "no puede pasar"),
        ("aaaaaaaaaaaaaaaa", "repetitiva"),
        ("password1234", "común"),
        ("Machinery Hunters", "común"),
    ],
)
def test_policy_rejects_weak_passwords(password, message):
    with pytest.raises(passwords.PasswordPolicyError, match=message):
        passwords.validate_policy(password)


def test_policy_rejects_a_password_containing_the_username():
    with pytest.raises(passwords.PasswordPolicyError, match="nombre de usuario"):
        passwords.validate_policy("xx-fernando-yy-zz", username="Fernando")


def test_policy_accepts_a_long_passphrase():
    passwords.validate_policy("caballo grapa bateria correcta", username="ana")
