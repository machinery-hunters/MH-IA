"""Servicio de autenticación: login, bloqueo, segundo factor y vida de las sesiones."""

import sqlite3

import pytest
from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from pipeline_ai.config import settings
from pipeline_ai.security import auth, passwords, totp
from tests.conftest import PASSWORD


def _dump_db(store) -> str:
    """Todo el contenido de la base como texto, para comprobar qué se guarda en claro."""
    with sqlite3.connect(store.path) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return "\n".join(
            repr(row) for t in tables for row in conn.execute(f"SELECT * FROM {t}")  # noqa: S608
        )


# -- Login básico y uniformidad de fallos -------------------------------------------------


def test_successful_login_creates_a_session_that_resolves(make_user):
    user = make_user()

    result = auth.authenticate("ana", PASSWORD)

    assert result.status == "ok" and result.token
    info = auth.resolve_session(result.token)
    assert info is not None and info.user_id == user.id and info.role == "lider"
    assert info.mfa is False


def test_the_session_token_is_never_stored_in_clear(auth_env, make_user):
    make_user()

    token = auth.authenticate("ana", PASSWORD).token

    assert token and token not in _dump_db(auth_env.store)
    assert PASSWORD not in _dump_db(auth_env.store)


def test_each_login_gets_a_fresh_random_token(make_user):
    make_user()

    first = auth.authenticate("ana", PASSWORD).token
    second = auth.authenticate("ana", PASSWORD).token

    assert first != second and len(first) >= 40


def test_wrong_password_unknown_user_and_inactive_user_look_identical(make_user):
    make_user("ana")
    make_user("beto", active=False)

    results = [
        auth.authenticate("ana", "contraseña-equivocada"),
        auth.authenticate("nadie", PASSWORD),
        auth.authenticate("beto", PASSWORD),
        auth.authenticate("' OR 1=1 --", PASSWORD),
    ]

    assert all(r.status == "invalid" and r.token is None and r.user is None for r in results)


def test_unknown_users_still_pay_for_a_password_verification(make_user, monkeypatch):
    make_user("ana")
    calls = []
    monkeypatch.setattr(passwords, "verify_dummy", lambda password: calls.append(password))

    auth.authenticate("nadie", "x" * 20)

    assert calls == ["x" * 20]


def test_username_is_normalized(make_user):
    make_user("ana")

    assert auth.authenticate("  ANA ", PASSWORD).status == "ok"
    assert auth.authenticate("ＡＮＡ", PASSWORD).status == "ok"  # ancho completo -> NFKC
    assert auth.normalize_username("a b") is None
    assert auth.normalize_username("ab") is None
    assert auth.normalize_username("x" * 65) is None


# -- Fuerza bruta -----------------------------------------------------------------------


def test_account_locks_after_repeated_failures_even_for_the_right_password(auth_env, make_user):
    make_user()
    for _ in range(5):
        assert auth.authenticate("ana", "mala-contraseña-1").status == "invalid"

    blocked = auth.authenticate("ana", PASSWORD)

    assert blocked.status == "locked" and blocked.token is None
    assert 0 < blocked.retry_after <= 901


def test_lock_expires_and_a_successful_login_clears_the_counter(auth_env, make_user):
    make_user()
    for _ in range(5):
        auth.authenticate("ana", "mala-contraseña-1")
    auth_env.clock.advance(901)

    assert auth.authenticate("ana", PASSWORD).status == "ok"
    # Contador limpio: otros 4 fallos no bloquean.
    for _ in range(4):
        assert auth.authenticate("ana", "mala-contraseña-1").status == "invalid"
    assert auth.authenticate("ana", PASSWORD).status == "ok"


def test_repeated_failures_after_unlock_lock_for_longer(auth_env, make_user):
    make_user()
    for _ in range(5):
        auth.authenticate("ana", "mala-contraseña-1")
    auth_env.clock.advance(901)

    assert auth.authenticate("ana", "mala-contraseña-1").status == "invalid"
    second = auth.authenticate("ana", PASSWORD)

    assert second.status == "locked" and second.retry_after > 1500


def test_old_failures_are_forgotten_after_a_long_quiet_period(auth_env, make_user):
    make_user()
    for _ in range(4):
        auth.authenticate("ana", "mala-contraseña-1")
    auth_env.clock.advance(900 * 16 + 1)

    for _ in range(4):  # si los 4 viejos siguieran contando, el 1.º de estos ya bloquearía
        assert auth.authenticate("ana", "mala-contraseña-1").status == "invalid"

    assert auth.authenticate("ana", PASSWORD).status == "ok"


def test_unknown_usernames_get_locked_too_so_the_lock_does_not_reveal_accounts(make_user):
    make_user("ana")
    for _ in range(5):
        auth.authenticate("fantasma", "mala-contraseña-1")

    assert auth.authenticate("fantasma", PASSWORD).status == "locked"


def test_one_users_lock_does_not_affect_another(make_user):
    make_user("ana")
    make_user("beto")
    for _ in range(5):
        auth.authenticate("ana", "mala-contraseña-1")

    assert auth.authenticate("beto", PASSWORD).status == "ok"


def test_failed_attempts_do_not_create_sessions(auth_env, make_user):
    make_user()
    auth.authenticate("ana", "mala-contraseña-1")

    with sqlite3.connect(auth_env.store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# -- Segundo factor ---------------------------------------------------------------------


def test_control_total_without_enrolled_totp_cannot_log_in(make_user):
    make_user("root", role="control_total")

    assert auth.authenticate("root", PASSWORD).status == "mfa_not_enrolled"


def test_control_total_requires_a_code_and_the_session_is_marked_mfa(auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)

    assert auth.authenticate("root", PASSWORD).status == "mfa_required"
    result = auth.authenticate("root", PASSWORD, totp.totp(secret, auth_env.clock.t))

    assert result.status == "ok"
    info = auth.resolve_session(result.token)
    assert info is not None and info.mfa is True


def test_a_wrong_totp_code_fails_and_counts_towards_the_lock(auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)
    real = totp.totp(secret, auth_env.clock.t)
    wrong = "000000" if real != "000000" else "111111"

    for _ in range(5):
        assert auth.authenticate("root", PASSWORD, wrong).status == "invalid"

    assert auth.authenticate("root", PASSWORD, real).status == "locked"


def test_a_totp_code_cannot_be_used_twice(auth_env, make_user):
    secret = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=secret)
    code = totp.totp(secret, auth_env.clock.t)

    assert auth.authenticate("root", PASSWORD, code).status == "ok"
    assert auth.authenticate("root", PASSWORD, code).status == "invalid"
    auth_env.clock.advance(30)
    assert auth.authenticate("root", PASSWORD, totp.totp(secret, auth_env.clock.t)).status == "ok"


def test_a_non_privileged_user_with_totp_enabled_must_use_it(auth_env, make_user):
    secret = totp.generate_secret()
    make_user("ana", role="lider", totp_secret=secret)

    assert auth.authenticate("ana", PASSWORD).status == "mfa_required"


def test_a_privileged_session_without_mfa_never_resolves(auth_env, make_user):
    user = make_user("root", role="control_total")
    # Sesión fabricada sin segundo factor (por un bug futuro, por manipulación de la base...).
    token = "t" * 43
    auth_env.store.create_session(
        auth.hash_token(token), user.id, mfa=False, now=auth_env.clock.t, absolute_s=3600
    )

    assert auth.resolve_session(token) is None


def test_a_broken_totp_key_makes_login_unavailable_instead_of_skipping_mfa(
    auth_env, make_user, monkeypatch
):
    make_user("root", role="control_total", totp_secret=totp.generate_secret())
    monkeypatch.setattr(settings, "api_totp_key", Fernet.generate_key().decode())

    assert auth.authenticate("root", PASSWORD, "123456").status == "unavailable"


# -- Vida de la sesión ------------------------------------------------------------------


def test_session_expires_after_idle_time(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token

    auth_env.clock.advance(1801)

    assert auth.resolve_session(token) is None


def test_activity_keeps_the_session_alive_but_never_past_the_absolute_limit(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token

    for _ in range(43):  # 43 000 s con actividad cada 1 000 s (< idle de 1 800)
        auth_env.clock.advance(1000)
        assert auth.resolve_session(token) is not None

    auth_env.clock.advance(1000)  # 44 000 s > 43 200 s absolutos
    assert auth.resolve_session(token) is None


def test_expired_sessions_are_deleted_from_the_database(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token
    auth_env.clock.advance(1801)
    auth.resolve_session(token)

    with sqlite3.connect(auth_env.store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_role_changes_and_deactivation_apply_on_the_next_request(auth_env, make_user):
    user = make_user()
    token = auth.authenticate("ana", PASSWORD).token

    auth_env.store.set_role(user.id, "consulta")
    assert auth.resolve_session(token).role == "consulta"

    auth_env.store.set_active(user.id, False)
    assert auth.resolve_session(token) is None


def test_logout_invalidates_only_that_session(make_user):
    make_user()
    first = auth.authenticate("ana", PASSWORD).token
    second = auth.authenticate("ana", PASSWORD).token

    auth.logout(first)

    assert auth.resolve_session(first) is None
    assert auth.resolve_session(second) is not None


@pytest.mark.parametrize("token", [None, "", "x" * 500, "no-existe"])
def test_garbage_tokens_resolve_to_nothing(make_user, token):
    make_user()

    assert auth.resolve_session(token) is None
    auth.logout(token)  # idempotente, no lanza


def test_a_token_signed_with_another_secret_is_not_valid(make_user, monkeypatch):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token
    monkeypatch.setattr(settings, "api_session_secret", "otro-secreto-distinto-" + "z" * 20)

    assert auth.resolve_session(token) is None


def test_sessions_cannot_be_managed_without_a_session_secret(make_user, monkeypatch):
    make_user()
    monkeypatch.setattr(settings, "api_session_secret", "")

    with pytest.raises(auth.AuthConfigError):
        auth.authenticate("ana", PASSWORD)


def test_password_hash_is_upgraded_transparently_on_login(auth_env, make_user, monkeypatch):
    user = make_user()
    old_hash = auth_env.store.get_user(user.id).password_hash
    monkeypatch.setattr(
        passwords, "_HASHER", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )

    assert auth.authenticate("ana", PASSWORD).status == "ok"

    new_hash = auth_env.store.get_user(user.id).password_hash
    assert new_hash != old_hash and not passwords.needs_rehash(new_hash)
    assert auth.authenticate("ana", PASSWORD).status == "ok"
