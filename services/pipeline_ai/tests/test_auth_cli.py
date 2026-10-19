"""CLI de administración de usuarios (python -m pipeline_ai.security.manage)."""

import re

import pytest
from cryptography.fernet import Fernet

from pipeline_ai.security import auth, clock, totp
from pipeline_ai.security.manage import main
from tests.conftest import PASSWORD


class IO:
    """Entradas guionadas (contraseñas y códigos) y salida capturada."""

    def __init__(self, passwords=(), inputs=()):
        self.passwords = list(passwords)
        self.inputs = list(inputs)
        self.lines: list[str] = []

    def getpass(self, _prompt):
        return self.passwords.pop(0)

    def input(self, _prompt):
        value = self.inputs.pop(0)
        return value(self) if callable(value) else value

    def run(self, *argv):
        return main(list(argv), getpass_fn=self.getpass, input_fn=self.input, out=self.lines.append)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def secret(self) -> str:
        return re.search(r"Clave manual : (\w+)", self.text).group(1)


def _code_from_screen(io):
    """Como el usuario real: lee la clave mostrada y calcula el código en su app."""
    return totp.totp(io.secret(), clock.now())


def test_gen_key_prints_a_valid_fernet_key():
    io = IO()

    assert io.run("gen-key") == 0
    Fernet(io.lines[0].encode())


def test_create_user_then_login(auth_env):
    io = IO(passwords=[PASSWORD, PASSWORD])

    assert io.run("create-user", "--username", "Ana", "--role", "lider") == 0

    assert auth.authenticate("ana", PASSWORD).status == "ok"
    assert PASSWORD not in io.text


def test_create_user_rejects_mismatched_weak_and_duplicate_passwords(auth_env, capsys):
    assert (
        IO(passwords=[PASSWORD, "otra-distinta-1234"]).run(
            "create-user", "--username", "ana", "--role", "lider"
        )
        == 1
    )
    assert (
        IO(passwords=["corta", "corta"]).run("create-user", "--username", "ana", "--role", "lider")
        == 1
    )
    assert auth_env.store.get_user_by_username("ana") is None

    assert (
        IO(passwords=[PASSWORD, PASSWORD]).run(
            "create-user", "--username", "ana", "--role", "lider"
        )
        == 0
    )
    assert (
        IO(passwords=[PASSWORD, PASSWORD]).run(
            "create-user", "--username", "ana", "--role", "consulta"
        )
        == 1
    )
    assert "Ya existe" in capsys.readouterr().err


def test_create_user_rejects_invalid_usernames_and_roles(auth_env):
    assert (
        IO(passwords=[PASSWORD, PASSWORD]).run(
            "create-user", "--username", "a b", "--role", "lider"
        )
        == 1
    )
    with pytest.raises(SystemExit):  # argparse rechaza el rol desconocido
        IO().run("create-user", "--username", "ana", "--role", "superadmin")


def test_control_total_is_enrolled_on_creation_and_can_then_log_in(auth_env):
    io = IO(passwords=[PASSWORD, PASSWORD], inputs=[_code_from_screen])

    assert io.run("create-user", "--username", "root", "--role", "control_total") == 0

    user = auth_env.store.get_user_by_username("root")
    assert user.totp_enabled and user.totp_secret_enc
    assert io.secret() not in user.totp_secret_enc  # cifrado en reposo
    auth_env.clock.advance(30)  # el código de la confirmación ya está usado
    code = totp.totp(io.secret(), clock.now())
    assert auth.authenticate("root", PASSWORD, code).status == "ok"


def test_failed_enrollment_leaves_control_total_unable_to_log_in(auth_env):
    io = IO(passwords=[PASSWORD, PASSWORD], inputs=["000000"])

    assert io.run("create-user", "--username", "root", "--role", "control_total") == 1

    assert auth.authenticate("root", PASSWORD).status == "mfa_not_enrolled"
    assert "enroll-totp" in io.text


def test_re_enrollment_failure_keeps_the_previous_factor(auth_env, make_user):
    old = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=old)

    assert IO(inputs=["000000"]).run("enroll-totp", "--username", "root") == 1

    assert auth.authenticate("root", PASSWORD, totp.totp(old, clock.now())).status == "ok"


def test_re_enrollment_replaces_the_factor_and_closes_sessions(auth_env, make_user):
    old = totp.generate_secret()
    make_user("root", role="control_total", totp_secret=old)
    token = auth.authenticate("root", PASSWORD, totp.totp(old, clock.now())).token
    io = IO(inputs=[_code_from_screen])

    assert io.run("enroll-totp", "--username", "root") == 0

    assert auth.resolve_session(token) is None
    auth_env.clock.advance(30)
    assert auth.authenticate("root", PASSWORD, totp.totp(old, clock.now())).status == "invalid"


def test_reset_password_closes_sessions_and_lifts_the_lock(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token
    for _ in range(5):
        auth.authenticate("ana", "mala-contraseña-1")
    new_password = "una-contraseña-nueva-9"

    assert (
        IO(passwords=[new_password, new_password]).run("reset-password", "--username", "ana") == 0
    )

    assert auth.resolve_session(token) is None
    assert auth.authenticate("ana", PASSWORD).status == "invalid"
    assert auth.authenticate("ana", new_password).status == "ok"


def test_disable_and_enable_user(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token

    assert IO().run("disable-user", "--username", "ana") == 0
    assert auth.resolve_session(token) is None
    assert auth.authenticate("ana", PASSWORD).status == "invalid"

    assert IO().run("enable-user", "--username", "ana") == 0
    assert auth.authenticate("ana", PASSWORD).status == "ok"


def test_set_role_closes_sessions(auth_env, make_user):
    make_user()
    token = auth.authenticate("ana", PASSWORD).token

    assert IO().run("set-role", "--username", "ana", "--role", "configuracion") == 0

    assert auth.resolve_session(token) is None
    assert auth.resolve_session(auth.authenticate("ana", PASSWORD).token).role == "configuracion"


def test_unlock_lifts_a_lock(auth_env, make_user):
    make_user()
    for _ in range(5):
        auth.authenticate("ana", "mala-contraseña-1")
    assert auth.authenticate("ana", PASSWORD).status == "locked"

    assert IO().run("unlock", "--username", "ana") == 0

    assert auth.authenticate("ana", PASSWORD).status == "ok"


def test_list_users_never_prints_hashes_or_secrets(auth_env, make_user):
    make_user("ana")
    make_user("root", role="control_total", totp_secret=totp.generate_secret())
    io = IO()

    assert io.run("list-users") == 0

    assert "ana" in io.text and "root" in io.text and "control_total" in io.text
    assert "argon2" not in io.text and "gAAAA" not in io.text


def test_revoke_sessions(auth_env, make_user):
    make_user("ana")
    make_user("beto")
    ana = auth.authenticate("ana", PASSWORD).token
    beto = auth.authenticate("beto", PASSWORD).token

    assert IO().run("revoke-sessions", "--username", "ana") == 0
    assert auth.resolve_session(ana) is None and auth.resolve_session(beto) is not None

    assert IO().run("revoke-sessions", "--all") == 0
    assert auth.resolve_session(beto) is None
    assert IO().run("revoke-sessions") == 1  # sin --username ni --all


def test_unknown_user_is_a_clean_error(auth_env, capsys):
    assert IO().run("reset-password", "--username", "nadie") == 1
    assert "No existe" in capsys.readouterr().err


def test_enrolling_without_a_totp_key_fails_with_a_clear_message(
    auth_env, make_user, monkeypatch, capsys
):
    from pipeline_ai.config import settings

    make_user("ana")
    monkeypatch.setattr(settings, "api_totp_key", "")

    assert IO(inputs=["000000"]).run("enroll-totp", "--username", "ana") == 1
    assert "API_TOTP_KEY" in capsys.readouterr().err
