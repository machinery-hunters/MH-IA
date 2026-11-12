"""
Respaldo, verificación, simulacro y restauración de los datos de MH IA.

Corre DENTRO del contenedor mhia-api, así que funciona igual en la laptop
Windows y en la Mac mini (no depende de rutas ni herramientas del host):

    docker compose ... exec mhia-api python -m pipeline_ai.ops.backup create
    docker compose ... exec mhia-api python -m pipeline_ai.ops.backup verify
    docker compose ... exec mhia-api python -m pipeline_ai.ops.backup drill
    docker compose ... exec mhia-api python -m pipeline_ai.ops.backup prune --keep 14
    # restaurar: con la API detenida, ver DEPLOYMENT.md
    docker compose ... run --rm --no-deps mhia-api python -m pipeline_ai.ops.backup restore <dir> --force

Qué se respalda (ver docs/PLAN-MAESTRO.md §3.5):
  - La base de usuarios. Con Postgres (despliegues con Docker): `auth.json` con usuarios,
    áreas y registro de cambios, leídos en una sola transacción (sin sesiones: tras
    restaurar todos vuelven a iniciar sesión). Con SQLite: auth.db con la API de respaldo en
    línea, que da una copia consistente aunque la API esté escribiendo.
  - El manifest de sincronización de Drive.
  - Un snapshot de cada colección de Qdrant (vía su API REST interna).
  - manifest.json con sha256 y tamaño de cada archivo, para verificar integridad.

El respaldo se escribe primero en un directorio temporal y se renombra al final: un
respaldo a medias nunca parece completo. Los respaldos contienen datos del negocio:
el directorio ./backups está fuera de git y el disco de la Mac va cifrado (FileVault).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from pipeline_ai.config import settings
from pipeline_ai.security.auth_store import (
    DB_ERRORS,
    AuthStore,
    ImportDataError,
    SqliteAuthStore,
    get_store,
)

FORMAT_VERSION = 1
BACKUP_PREFIX = "mhia-"
BACKUP_NAME_RE = re.compile(r"^mhia-\d{8}T\d{6}Z$")
MANIFEST_NAME = "manifest.json"
AUTH_DB_NAME = "auth.db"
AUTH_EXPORT_NAME = "auth.json"
SYNC_MANIFEST_NAME = "sync_manifest.json"
QDRANT_DIR = "qdrant"
DEFAULT_DEST = os.getenv("MHIA_BACKUP_DIR", "/app/backups")
_CHUNK = 1024 * 1024
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class BackupError(RuntimeError):
    """Fallo de respaldo/restauración con un mensaje apto para el operador."""


@dataclass(frozen=True)
class Paths:
    auth_db: Path
    sync_manifest: Path


def default_paths() -> Paths:
    return Paths(
        auth_db=Path(settings.auth_db_path), sync_manifest=Path(settings.sync_manifest_path)
    )


# ── Utilidades ───────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_stamp(now: float | None = None) -> str:
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _check_collection_name(name: str) -> str:
    # El nombre viaja en la URL: se valida para no construir rutas arbitrarias.
    if not _COLLECTION_RE.fullmatch(name):
        raise BackupError(f"Nombre de colección inválido: {name!r}")
    return name


def _sqlite_backup(src: Path, dst: Path) -> None:
    # Modo solo lectura sobre el origen: el respaldo nunca crea ni modifica auth.db.
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
            # La copia hereda el modo WAL del origen; en modo DELETE queda en un solo
            # archivo autosuficiente y abrirla nunca crea -wal/-shm dentro del respaldo.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
    finally:
        source.close()


def _sqlite_integrity_ok(path: Path) -> bool:
    # immutable=1: lectura pura, SQLite no crea archivos auxiliares junto al respaldo.
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        return False  # ni siquiera es una base SQLite
    finally:
        conn.close()
    return bool(row) and row[0] == "ok"


def _auth_store(explicit: AuthStore | None) -> AuthStore | None:
    """Almacén a exportar/restaurar como JSON: el indicado o, con Postgres, el configurado.
    None = modo archivo SQLite (auth.db)."""
    if explicit is not None:
        return explicit
    return get_store() if settings.auth_backend == "postgres" else None


def _load_auth_export(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"{path.name} ilegible") from exc
    if not isinstance(data, dict):
        raise BackupError(f"{path.name} con forma inesperada")
    return data


def _check_auth_export(
    path: Path, expected: dict | None, expected_head: dict | None = None
) -> dict[str, int]:
    """Carga el export en una base SQLite desechable: prueba que se puede restaurar (la
    importación también recalcula la cadena del registro de auditoría)."""
    data = _load_auth_export(path)
    if expected_head is not None and data.get("audit_head") != expected_head:
        raise BackupError(
            f"{path.name}: el registro de auditoría no termina donde dice el manifest"
        )
    with tempfile.TemporaryDirectory() as tmp:
        try:
            counts = SqliteAuthStore(Path(tmp) / "check.db").import_data(data, replace=False)
        except ImportDataError as exc:
            raise BackupError(f"{path.name}: {exc}") from exc
    if expected is not None:
        # Respaldos anteriores al registro encadenado contaban la tabla `admin_audit`.
        expected = {("audit_log" if k == "admin_audit" else k): v for k, v in expected.items()}
        if counts != expected:
            raise BackupError(f"{path.name}: conteos {counts}, el manifest dice {expected}")
    return counts


def _client(base_url: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=httpx.Timeout(600.0), transport=transport)


def _result(response: httpx.Response, action: str):
    if response.status_code >= 400:
        raise BackupError(f"Qdrant respondió {response.status_code} al {action}.")
    return response.json().get("result")


# ── Qdrant ───────────────────────────────────────────────────────────────


def _list_collections(client: httpx.Client) -> list[str]:
    result = _result(client.get("/collections"), "listar colecciones") or {}
    return sorted(_check_collection_name(c["name"]) for c in result.get("collections", []))


def _points_count(client: httpx.Client, collection: str) -> int:
    result = _result(client.get(f"/collections/{collection}"), "leer la colección") or {}
    return int(result.get("points_count") or 0)


def _snapshot_collection(client: httpx.Client, collection: str, dest_dir: Path) -> Path:
    created = _result(
        client.post(f"/collections/{collection}/snapshots", params={"wait": "true"}),
        f"crear el snapshot de {collection}",
    )
    snap_name = created["name"]
    if "/" in snap_name or "\\" in snap_name or snap_name.startswith("."):
        raise BackupError("Qdrant devolvió un nombre de snapshot inesperado.")
    target = dest_dir / f"{collection}.snapshot"
    try:
        with client.stream("GET", f"/collections/{collection}/snapshots/{snap_name}") as resp:
            if resp.status_code >= 400:
                raise BackupError(f"Qdrant respondió {resp.status_code} al descargar el snapshot.")
            with target.open("wb") as fh:
                for block in resp.iter_bytes(_CHUNK):
                    fh.write(block)
    finally:
        # El snapshot ya está en el respaldo: se borra del almacenamiento de Qdrant para no
        # llenar el volumen. Si falla el borrado, no se pierde el respaldo.
        try:
            client.delete(
                f"/collections/{collection}/snapshots/{snap_name}", params={"wait": "true"}
            )
        except httpx.HTTPError:
            pass
    expected = created.get("checksum")
    if expected and _sha256(target) != expected:
        raise BackupError(f"El snapshot de {collection} llegó corrupto (checksum distinto).")
    return target


def _upload_snapshot(client: httpx.Client, collection: str, snapshot: Path) -> None:
    with snapshot.open("rb") as fh:
        response = client.post(
            f"/collections/{collection}/snapshots/upload",
            params={"priority": "snapshot", "wait": "true"},
            files={"snapshot": (snapshot.name, fh, "application/octet-stream")},
        )
    _result(response, f"restaurar el snapshot en {collection}")


# ── Operaciones ──────────────────────────────────────────────────────────


def create_backup(
    dest_root: Path,
    *,
    paths: Paths | None = None,
    qdrant_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
    auth_store: AuthStore | None = None,
) -> Path:
    """Crea un respaldo completo y devuelve su directorio final."""
    paths = paths or default_paths()
    store = _auth_store(auth_store)
    dest_root.mkdir(parents=True, exist_ok=True)
    final = dest_root / f"{BACKUP_PREFIX}{_utc_stamp(now)}"
    if final.exists():
        raise BackupError(f"Ya existe un respaldo con ese nombre: {final.name}")
    staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=dest_root))
    try:
        manifest: dict = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.fromtimestamp(now or time.time(), tz=UTC).isoformat(),
            "files": {},
            "qdrant": {},
            "skipped": [],
        }
        if store is not None:
            data = store.export_data()
            (staging / AUTH_EXPORT_NAME).write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            manifest["auth"] = {
                "backend": store.backend,
                "counts": {t: len(data[t]) for t in ("users", "memberships", "audit_log")},
                # Última fila del registro encadenado: si alguien rehace la cadena en la base,
                # deja de coincidir con la de los respaldos.
                "audit_head": data["audit_head"],
            }
        elif paths.auth_db.exists():
            _sqlite_backup(paths.auth_db, staging / AUTH_DB_NAME)
        else:
            manifest["skipped"].append(AUTH_DB_NAME)
        if paths.sync_manifest.exists():
            shutil.copy2(paths.sync_manifest, staging / SYNC_MANIFEST_NAME)
        else:
            manifest["skipped"].append(SYNC_MANIFEST_NAME)

        (staging / QDRANT_DIR).mkdir()
        with _client(qdrant_url or settings.qdrant_url, transport) as client:
            for collection in _list_collections(client):
                count = _points_count(client, collection)
                _snapshot_collection(client, collection, staging / QDRANT_DIR)
                manifest["qdrant"][collection] = {"points_count": count}

        for file in sorted(p for p in staging.rglob("*") if p.is_file()):
            rel = file.relative_to(staging).as_posix()
            manifest["files"][rel] = {"sha256": _sha256(file), "bytes": file.stat().st_size}
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def list_backups(dest_root: Path) -> list[Path]:
    if not dest_root.exists():
        return []
    return sorted(p for p in dest_root.iterdir() if p.is_dir() and BACKUP_NAME_RE.fullmatch(p.name))


def latest_backup(dest_root: Path) -> Path:
    backups = list_backups(dest_root)
    if not backups:
        raise BackupError(f"No hay respaldos en {dest_root}")
    return backups[-1]


def _load_manifest(backup_dir: Path) -> dict:
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise BackupError(f"{backup_dir.name}: falta {MANIFEST_NAME} (respaldo incompleto)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError(f"{backup_dir.name}: versión de formato no soportada")
    return manifest


def verify_backup(backup_dir: Path) -> list[str]:
    """Devuelve la lista de problemas; vacía si el respaldo está íntegro."""
    try:
        manifest = _load_manifest(backup_dir)
    except BackupError as exc:
        return [str(exc)]
    problems: list[str] = []
    for rel, meta in manifest["files"].items():
        file = backup_dir / rel
        if not file.is_file():
            problems.append(f"falta {rel}")
        elif _sha256(file) != meta["sha256"]:
            problems.append(f"{rel}: sha256 no coincide")
    listed = set(manifest["files"])
    for file in backup_dir.rglob("*"):
        rel = file.relative_to(backup_dir).as_posix()
        if file.is_file() and rel != MANIFEST_NAME and rel not in listed:
            problems.append(f"archivo no esperado: {rel}")
    auth = backup_dir / AUTH_DB_NAME
    if AUTH_DB_NAME in listed and auth.is_file() and not _sqlite_integrity_ok(auth):
        problems.append("auth.db no pasa PRAGMA integrity_check")
    export = backup_dir / AUTH_EXPORT_NAME
    if AUTH_EXPORT_NAME in listed and export.is_file() and not problems:
        try:
            auth_meta = manifest.get("auth") or {}
            _check_auth_export(export, auth_meta.get("counts"), auth_meta.get("audit_head"))
        except BackupError as exc:
            problems.append(str(exc))
    return problems


def drill(
    backup_dir: Path,
    *,
    qdrant_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Simulacro NO destructivo: verifica el respaldo y restaura cada snapshot en una
    colección temporal, compara el número de puntos y la borra. Las colecciones reales
    no se tocan."""
    problems = verify_backup(backup_dir)
    if problems:
        raise BackupError("Respaldo con problemas: " + "; ".join(problems))
    manifest = _load_manifest(backup_dir)
    report: dict = {"backup": backup_dir.name, "collections": {}}
    if AUTH_EXPORT_NAME in manifest["files"]:
        # verify_backup ya lo cargó en una base desechable; el conteo va al informe.
        report["auth"] = _check_auth_export(backup_dir / AUTH_EXPORT_NAME, None)
    with _client(qdrant_url or settings.qdrant_url, transport) as client:
        for collection, meta in manifest["qdrant"].items():
            _check_collection_name(collection)
            temp = f"drill_{collection[:80]}_{uuid.uuid4().hex[:8]}"
            try:
                _upload_snapshot(client, temp, backup_dir / QDRANT_DIR / f"{collection}.snapshot")
                restored = _points_count(client, temp)
            finally:
                try:
                    client.delete(f"/collections/{temp}")
                except httpx.HTTPError:
                    pass
            expected = int(meta["points_count"])
            if restored != expected:
                raise BackupError(
                    f"Simulacro fallido en {collection}: {restored} puntos, se esperaban {expected}"
                )
            report["collections"][collection] = restored
    return report


def restore_backup(
    backup_dir: Path,
    *,
    force: bool,
    paths: Paths | None = None,
    qdrant_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    auth_store: AuthStore | None = None,
) -> dict:
    """Restaura la base de usuarios, el manifest de Drive y las colecciones de Qdrant.

    Destructivo: reemplaza los datos actuales. Exige force=True y debe correr con la API
    detenida (con Postgres, el servicio postgres sí debe estar arriba). Un respaldo viejo
    con auth.db también se puede restaurar sobre Postgres."""
    if not force:
        raise BackupError("La restauración reemplaza los datos actuales: repite con --force.")
    problems = verify_backup(backup_dir)
    if problems:
        raise BackupError("Respaldo con problemas: " + "; ".join(problems))
    paths = paths or default_paths()
    store = _auth_store(auth_store)
    manifest = _load_manifest(backup_dir)
    restored: dict = {"files": [], "collections": []}
    files = manifest["files"]
    if store is not None and (AUTH_EXPORT_NAME in files or AUTH_DB_NAME in files):
        if AUTH_EXPORT_NAME in files:
            data = _load_auth_export(backup_dir / AUTH_EXPORT_NAME)
            source = AUTH_EXPORT_NAME
        else:  # respaldo anterior a Postgres: se lee de una copia, nunca del respaldo
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / AUTH_DB_NAME
                shutil.copy2(backup_dir / AUTH_DB_NAME, copy)
                data = SqliteAuthStore(copy).export_data()
            source = AUTH_DB_NAME
        try:
            restored["auth"] = store.import_data(data, replace=True)
        except ImportDataError as exc:
            raise BackupError(f"{source}: {exc}") from exc
        # La restauración deshace los cambios posteriores al respaldo: queda constancia.
        store.record_admin_event(
            "sistema", "restaurar_respaldo", None, {"respaldo": backup_dir.name, "origen": source}
        )
        restored["files"].append(source)
    elif AUTH_EXPORT_NAME in files:
        raise BackupError(
            "El respaldo trae auth.json (Postgres) y la base configurada es un archivo SQLite: "
            "restaura con AUTH_BACKEND=postgres."
        )
    for name, target in ((AUTH_DB_NAME, paths.auth_db), (SYNC_MANIFEST_NAME, paths.sync_manifest)):
        if name not in files or (name == AUTH_DB_NAME and store is not None):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.restore-tmp")
        shutil.copy2(backup_dir / name, tmp)
        os.replace(tmp, target)  # reemplazo atómico
        for suffix in ("-wal", "-shm", "-journal"):
            # Restos del WAL de la base anterior corromperían la restaurada.
            stale = target.with_name(target.name + suffix)
            if name == AUTH_DB_NAME and stale.exists():
                stale.unlink()
        restored["files"].append(name)
    with _client(qdrant_url or settings.qdrant_url, transport) as client:
        for collection in manifest["qdrant"]:
            _check_collection_name(collection)
            _upload_snapshot(client, collection, backup_dir / QDRANT_DIR / f"{collection}.snapshot")
            restored["collections"].append(collection)
    return restored


def prune(dest_root: Path, keep: int) -> list[Path]:
    """Borra los respaldos más viejos y conserva los `keep` más recientes."""
    if keep < 1:
        raise BackupError("--keep debe ser al menos 1")
    backups = list_backups(dest_root)
    doomed = backups[:-keep] if len(backups) > keep else []
    for path in doomed:
        shutil.rmtree(path)
    return doomed


# ── CLI ──────────────────────────────────────────────────────────────────


def _resolve(dest_root: Path, name: str | None) -> Path:
    if not name or name == "latest":
        return latest_backup(dest_root)
    candidate = dest_root / Path(name).name  # solo nombres dentro del directorio de respaldos
    if not candidate.is_dir():
        raise BackupError(f"No existe el respaldo {candidate.name}")
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline_ai.ops.backup")
    parser.add_argument("--dest", default=DEFAULT_DEST, help="directorio de respaldos")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create", help="crea un respaldo completo")
    sub.add_parser("list", help="lista los respaldos")
    for name, help_text in (
        ("verify", "verifica sha256 e integridad"),
        ("drill", "simulacro de restauración no destructivo"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("backup", nargs="?", default="latest")
    restore = sub.add_parser("restore", help="restaura (destructivo, con la API detenida)")
    restore.add_argument("backup")
    restore.add_argument("--force", action="store_true")
    pr = sub.add_parser("prune", help="conserva solo los N respaldos más recientes")
    pr.add_argument("--keep", type=int, default=14)
    args = parser.parse_args(argv)
    dest_root = Path(args.dest)
    try:
        if args.command == "create":
            print(f"Respaldo creado: {create_backup(dest_root).name}")
        elif args.command == "list":
            for path in list_backups(dest_root):
                print(path.name)
        elif args.command == "verify":
            target = _resolve(dest_root, args.backup)
            problems = verify_backup(target)
            if problems:
                for problem in problems:
                    print(f"ERROR {target.name}: {problem}", file=sys.stderr)
                return 1
            print(f"Respaldo íntegro: {target.name}")
        elif args.command == "drill":
            report = drill(_resolve(dest_root, args.backup))
            print(json.dumps(report, ensure_ascii=False))
        elif args.command == "restore":
            report = restore_backup(_resolve(dest_root, args.backup), force=args.force)
            print(json.dumps(report, ensure_ascii=False))
        elif args.command == "prune":
            for path in prune(dest_root, args.keep):
                print(f"Borrado: {path.name}")
    except (BackupError, httpx.HTTPError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except DB_ERRORS as exc:
        print(f"ERROR: base de usuarios no disponible ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
