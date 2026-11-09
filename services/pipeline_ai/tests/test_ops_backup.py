"""Respaldo / verificación / simulacro / restauración con un Qdrant falso en memoria."""

import json
import re
import sqlite3

import httpx
import pytest

from pipeline_ai.ops import backup


class FakeQdrant:
    """Implementa el subconjunto de la API REST de Qdrant que usa el respaldo."""

    def __init__(self, collections: dict[str, int]):
        # nombre -> (points_count, contenido del snapshot)
        self.collections = {n: (c, f"snap-{n}-{c}".encode()) for n, c in collections.items()}
        self.snapshots: dict[tuple[str, str], bytes] = {}
        self.deleted_snapshots: list[str] = []
        self.deleted_collections: list[str] = []
        self.uploads: list[str] = []
        self.corrupt_checksum = False
        self.known = {data: count for count, data in self.collections.values()}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path == "/collections":
            names = [{"name": n} for n in self.collections]
            return httpx.Response(200, json={"result": {"collections": names}})
        m = re.fullmatch(r"/collections/([^/]+)", path)
        if m and method == "GET":
            name = m.group(1)
            if name not in self.collections:
                return httpx.Response(404, json={"status": {"error": "not found"}})
            return httpx.Response(200, json={"result": {"points_count": self.collections[name][0]}})
        if m and method == "DELETE":
            self.collections.pop(m.group(1), None)
            self.deleted_collections.append(m.group(1))
            return httpx.Response(200, json={"result": True})
        m = re.fullmatch(r"/collections/([^/]+)/snapshots", path)
        if m and method == "POST":
            name = m.group(1)
            data = self.collections[name][1]
            snap = f"{name}-123.snapshot"
            self.snapshots[(name, snap)] = data
            checksum = (
                "0" * 64 if self.corrupt_checksum else backup.hashlib.sha256(data).hexdigest()
            )
            return httpx.Response(200, json={"result": {"name": snap, "checksum": checksum}})
        m = re.fullmatch(r"/collections/([^/]+)/snapshots/upload", path)
        if m and method == "POST":
            name = m.group(1)
            body = request.read()
            # El snapshot viaja como multipart: se reconoce por su contenido.
            match = [(data, count) for data, count in self.known.items() if data in body]
            if not match:
                return httpx.Response(400, json={"status": {"error": "bad snapshot"}})
            data, count = match[0]
            self.collections[name] = (count, data)
            self.uploads.append(name)
            return httpx.Response(200, json={"result": True})
        m = re.fullmatch(r"/collections/([^/]+)/snapshots/([^/]+)", path)
        if m and method == "GET":
            return httpx.Response(200, content=self.snapshots[(m.group(1), m.group(2))])
        if m and method == "DELETE":
            self.snapshots.pop((m.group(1), m.group(2)), None)
            self.deleted_snapshots.append(m.group(2))
            return httpx.Response(200, json={"result": True})
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    auth = data / "auth.db"
    conn = sqlite3.connect(auth)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE users (name TEXT)")
    conn.execute("INSERT INTO users VALUES ('original')")
    conn.commit()
    conn.close()
    manifest = data / "sync_manifest.json"
    manifest.write_text('{"files": {}}', encoding="utf-8")
    paths = backup.Paths(auth_db=auth, sync_manifest=manifest)
    qdrant = FakeQdrant({"mhia_drive_docs": 42})
    return tmp_path / "backups", paths, qdrant


def _create(env, now=1_700_000_000.0):
    dest, paths, qdrant = env
    return backup.create_backup(
        dest, paths=paths, qdrant_url="http://q", transport=qdrant.transport, now=now
    )


def test_create_writes_everything_with_checksums(env):
    dest, _, qdrant = env
    out = _create(env)
    assert backup.BACKUP_NAME_RE.fullmatch(out.name)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {
        "auth.db",
        "sync_manifest.json",
        "qdrant/mhia_drive_docs.snapshot",
    }
    assert manifest["qdrant"] == {"mhia_drive_docs": {"points_count": 42}}
    assert backup.verify_backup(out) == []
    # El snapshot temporal se borra del almacenamiento de Qdrant.
    assert qdrant.deleted_snapshots == ["mhia_drive_docs-123.snapshot"]
    # No quedan directorios temporales.
    assert [p.name for p in dest.iterdir()] == [out.name]


def test_auth_db_copy_is_consistent_and_readable(env):
    out = _create(env)
    conn = sqlite3.connect(out / "auth.db")
    assert conn.execute("SELECT name FROM users").fetchall() == [("original",)]
    # El origen está en WAL; la copia queda en un solo archivo.
    assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    conn.close()


def test_verifying_twice_leaves_no_sqlite_side_files(env):
    """Regresión: abrir el auth.db copiado (en WAL) creaba -wal/-shm dentro del respaldo y
    la segunda verificación lo daba por alterado."""
    out = _create(env)
    assert backup.verify_backup(out) == []
    assert backup.verify_backup(out) == []
    assert sorted(p.name for p in out.iterdir()) == [
        "auth.db",
        "manifest.json",
        "qdrant",
        "sync_manifest.json",
    ]


def test_missing_optional_files_are_recorded_as_skipped(env, tmp_path):
    dest, _, qdrant = env
    paths = backup.Paths(auth_db=tmp_path / "nope.db", sync_manifest=tmp_path / "nope.json")
    out = backup.create_backup(dest, paths=paths, qdrant_url="http://q", transport=qdrant.transport)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["skipped"] == ["auth.db", "sync_manifest.json"]
    assert not (tmp_path / "nope.db").exists()  # el respaldo nunca crea la base


def test_failed_backup_leaves_no_partial_directory(env):
    dest, paths, qdrant = env
    qdrant.corrupt_checksum = True
    with pytest.raises(backup.BackupError, match="corrupto"):
        backup.create_backup(dest, paths=paths, qdrant_url="http://q", transport=qdrant.transport)
    assert list(dest.iterdir()) == []


def test_verify_detects_tampering_and_extra_files(env):
    out = _create(env)
    (out / "sync_manifest.json").write_text("{}", encoding="utf-8")
    (out / "intruso.txt").write_text("x", encoding="utf-8")
    problems = backup.verify_backup(out)
    assert "sync_manifest.json: sha256 no coincide" in problems
    assert "archivo no esperado: intruso.txt" in problems


def test_verify_reports_incomplete_backup(tmp_path):
    incomplete = tmp_path / "mhia-20260101T000000Z"
    incomplete.mkdir()
    assert "falta manifest.json" in backup.verify_backup(incomplete)[0]


def test_drill_restores_into_temp_collection_and_cleans_up(env):
    _, _, qdrant = env
    out = _create(env)
    report = backup.drill(out, qdrant_url="http://q", transport=qdrant.transport)
    assert report["collections"] == {"mhia_drive_docs": 42}
    assert len(qdrant.uploads) == 1 and qdrant.uploads[0].startswith("drill_mhia_drive_docs_")
    assert qdrant.deleted_collections == qdrant.uploads
    assert set(qdrant.collections) == {"mhia_drive_docs"}  # la real sigue intacta


def test_drill_fails_when_point_count_differs(env):
    _, _, qdrant = env
    out = _create(env)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["qdrant"]["mhia_drive_docs"]["points_count"] = 99
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup.BackupError, match="Simulacro fallido"):
        backup.drill(out, qdrant_url="http://q", transport=qdrant.transport)
    assert qdrant.deleted_collections  # la colección temporal se borra aun si falla


def test_restore_requires_force(env):
    out = _create(env)
    with pytest.raises(backup.BackupError, match="--force"):
        backup.restore_backup(out, force=False)


def test_restore_replaces_auth_db_and_collections(env):
    _, paths, qdrant = env
    out = _create(env)
    conn = sqlite3.connect(paths.auth_db)
    conn.execute("UPDATE users SET name = 'cambiado'")
    conn.commit()
    conn.close()
    qdrant.collections.clear()

    report = backup.restore_backup(
        out, force=True, paths=paths, qdrant_url="http://q", transport=qdrant.transport
    )
    assert report == {
        "files": ["auth.db", "sync_manifest.json"],
        "collections": ["mhia_drive_docs"],
    }
    conn = sqlite3.connect(paths.auth_db)
    assert conn.execute("SELECT name FROM users").fetchall() == [("original",)]
    conn.close()
    assert qdrant.collections["mhia_drive_docs"][0] == 42


def test_restore_refuses_tampered_backup(env):
    _, paths, qdrant = env
    out = _create(env)
    (out / "auth.db").write_bytes(b"basura")
    with pytest.raises(backup.BackupError, match="problemas"):
        backup.restore_backup(
            out, force=True, paths=paths, qdrant_url="http://q", transport=qdrant.transport
        )


def test_prune_keeps_most_recent(env):
    dest, paths, qdrant = env
    made = [_create(env, now=1_700_000_000.0 + i * 86400) for i in range(4)]
    (dest / "no-es-respaldo").mkdir()
    removed = backup.prune(dest, keep=2)
    assert removed == made[:2]
    assert backup.list_backups(dest) == made[2:]
    assert (dest / "no-es-respaldo").exists()
    with pytest.raises(backup.BackupError):
        backup.prune(dest, keep=0)


def test_invalid_collection_names_are_rejected():
    with pytest.raises(backup.BackupError):
        backup._check_collection_name("../etc")


def test_cli_resolve_only_accepts_names_inside_backup_dir(env, tmp_path):
    out = _create(env)
    dest = out.parent
    assert backup._resolve(dest, "latest") == out
    assert backup._resolve(dest, f"../../{out.name}") == out
    with pytest.raises(backup.BackupError):
        backup._resolve(dest, "mhia-19990101T000000Z")


def test_cli_list_and_verify(env, capsys):
    out = _create(env)
    assert backup.main(["--dest", str(out.parent), "list"]) == 0
    assert out.name in capsys.readouterr().out
    assert backup.main(["--dest", str(out.parent), "verify"]) == 0
    (out / "auth.db").write_bytes(b"x")
    assert backup.main(["--dest", str(out.parent), "verify", out.name]) == 1


# ── Base de usuarios en Postgres (export JSON) ───────────────────────────
# auth_env corre contra SQLite y, si hay Postgres de pruebas, también contra Postgres: el
# respaldo en JSON es el mismo para los dos.


@pytest.fixture
def store_env(auth_env, make_user, tmp_path):
    from pipeline_ai.security.areas import Area
    from pipeline_ai.security.auth_store import Sensitivity

    store = auth_env.store
    ana = make_user("ana")
    store.set_memberships(ana.id, {Area.TALLER: Sensitivity.CONFIDENTIAL})
    store.record_admin_event("cli:x", "crear_usuario", "ana")
    store.create_session("h" * 64, ana.id, mfa=False, now=1.0, absolute_s=10**9)
    manifest = tmp_path / "data" / "sync_manifest.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")
    paths = backup.Paths(auth_db=tmp_path / "data" / "no-existe.db", sync_manifest=manifest)
    return store, paths, FakeQdrant({"mhia_drive_docs": 3}), tmp_path / "backups"


def _create_with_store(store_env):
    store, paths, qdrant, dest = store_env
    return backup.create_backup(
        dest, paths=paths, qdrant_url="http://q", transport=qdrant.transport, auth_store=store
    )


def test_user_database_is_exported_as_json_without_sessions(store_env):
    out = _create_with_store(store_env)

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "auth.json" in manifest["files"] and "auth.db" not in manifest["files"]
    assert manifest["auth"]["counts"] == {"users": 1, "memberships": 1, "admin_audit": 1}
    data = json.loads((out / "auth.json").read_text(encoding="utf-8"))
    assert "sessions" not in data and "throttle" not in data
    assert backup.verify_backup(out) == []


def test_verify_detects_an_inconsistent_user_export(store_env):
    out = _create_with_store(store_env)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["auth"]["counts"]["users"] = 5  # el manifest no cuadra con el export
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert any("conteos" in p for p in backup.verify_backup(out))


def test_drill_reports_the_user_counts(store_env):
    _, _, qdrant, _ = store_env
    out = _create_with_store(store_env)

    report = backup.drill(out, qdrant_url="http://q", transport=qdrant.transport)

    assert report["auth"] == {"users": 1, "memberships": 1, "admin_audit": 1}


def test_restore_replaces_users_areas_and_audit_and_closes_sessions(store_env, make_user):
    from tests.conftest import db_count

    store, paths, qdrant, _ = store_env
    out = _create_with_store(store_env)
    make_user("intruso")
    store.record_admin_event("cli:x", "despues_del_respaldo", "intruso")

    report = backup.restore_backup(
        out, force=True, paths=paths, qdrant_url="http://q", transport=qdrant.transport,
        auth_store=store,
    )  # fmt: skip

    assert report["auth"] == {"users": 1, "memberships": 1, "admin_audit": 1}
    assert [u.username for u in store.list_users()] == ["ana"]
    assert [e.action for e in store.list_admin_events()] == ["restaurar_respaldo", "crear_usuario"]
    assert store.list_admin_events()[0].detail == {"respaldo": out.name, "origen": "auth.json"}
    assert db_count(store, "sessions") == 0


def test_an_old_sqlite_backup_can_be_restored_into_the_store(env, auth_env, monkeypatch):
    """Respaldo anterior a Postgres (con auth.db) restaurado sobre la base actual."""
    from pipeline_ai.config import settings
    from pipeline_ai.security import clock, passwords
    from pipeline_ai.security.auth_store import SqliteAuthStore

    dest, paths, qdrant = env
    paths.auth_db.unlink()
    old = SqliteAuthStore(paths.auth_db)
    old.create_user("vieja", passwords.hash_password("x" * 12), "consulta", now=clock.now())
    with monkeypatch.context() as m:
        m.setattr(settings, "auth_backend", "sqlite")  # respaldo hecho en modo archivo
        out = backup.create_backup(
            dest, paths=paths, qdrant_url="http://q", transport=qdrant.transport
        )
    assert "auth.db" in json.loads((out / "manifest.json").read_text(encoding="utf-8"))["files"]

    backup.restore_backup(
        out, force=True, paths=paths, qdrant_url="http://q", transport=qdrant.transport,
        auth_store=auth_env.store,
    )  # fmt: skip

    assert [u.username for u in auth_env.store.list_users()] == ["vieja"]
    assert backup.verify_backup(out) == []  # el respaldo no se tocó


def test_restoring_a_json_backup_onto_a_sqlite_file_is_refused(store_env, monkeypatch):
    from pipeline_ai.config import settings

    _, paths, qdrant, _ = store_env
    out = _create_with_store(store_env)
    monkeypatch.setattr(settings, "auth_backend", "sqlite")

    with pytest.raises(backup.BackupError, match="AUTH_BACKEND=postgres"):
        backup.restore_backup(
            out, force=True, paths=paths, qdrant_url="http://q", transport=qdrant.transport
        )
