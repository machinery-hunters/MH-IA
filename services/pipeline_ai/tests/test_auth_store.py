"""Almacén SQLite: invariantes que no deben depender de la capa de servicio."""

import os

import pytest

from pipeline_ai.security.auth_store import UserExistsError
from tests.conftest import db_count


def test_a_totp_step_can_be_consumed_only_once(auth_env, make_user):
    user = make_user()

    assert auth_env.store.consume_totp_step(user.id, 100) is True
    assert auth_env.store.consume_totp_step(user.id, 100) is False  # repetición exacta
    assert auth_env.store.consume_totp_step(user.id, 99) is False  # paso anterior
    assert auth_env.store.consume_totp_step(user.id, 101) is True


def test_usernames_are_unique(auth_env, make_user):
    make_user("ana")

    with pytest.raises(UserExistsError):
        make_user("ana")


def test_deleting_a_user_cascades_to_sessions(auth_env, make_user):
    user = make_user()
    auth_env.store.create_session("h" * 64, user.id, mfa=False, now=1.0, absolute_s=100)

    with auth_env.store.connection() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user.id,))
    assert db_count(auth_env.store, "sessions") == 0


def test_sql_injection_in_lookups_is_inert(auth_env, make_user):
    make_user("ana")

    assert auth_env.store.get_user_by_username("' OR '1'='1") is None
    assert auth_env.store.get_user_by_username("ana' --") is None
    assert auth_env.store.lookup_session("' OR '1'='1", now=1.0, idle_s=100) is None


def test_stale_throttle_entries_are_pruned(auth_env):
    store = auth_env.store
    store.register_failure("a", now=0.0, max_failures=5, lock_s=900)
    store.register_failure("b", now=900 * 16 * 2 + 1, max_failures=5, lock_s=900)

    with store.connection() as conn:
        keys = {r["key"] for r in conn.execute("SELECT key FROM throttle").fetchall()}
    assert keys == {"b"}


@pytest.mark.skipif(os.name == "nt", reason="los permisos POSIX no aplican en Windows")
def test_database_file_is_private(auth_env):
    if auth_env.backend != "sqlite":
        pytest.skip("solo aplica al archivo SQLite")
    assert oct(os.stat(auth_env.store.path).st_mode & 0o777) == "0o600"
