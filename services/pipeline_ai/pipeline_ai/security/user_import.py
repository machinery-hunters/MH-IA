"""
Importación de usuarios desde la lista de personas (Excel o CSV) del área de Sistemas.

    python -m pipeline_ai.security.manage import-users --file /app/data/usuarios.xlsx
    python -m pipeline_ai.security.manage import-users --file /app/data/usuarios.xlsx --apply

El archivo tiene datos personales: vive SOLO en el directorio de datos (./data en el host,
/app/data en el contenedor), que git ignora (`**/data/*.xlsx`, `**/data/*.csv`). La
importación se niega a leer un archivo fuera de ese directorio.

Del archivo solo se usan el correo (que pasa a ser el usuario) y el puesto (que elige el
preset de rol y áreas, `security.presets`). El NOMBRE no se lee ni se guarda: minimización.

Sin --apply solo muestra el plan. Con --apply:
  - Usuario nuevo: se crea DESACTIVADO, con una contraseña aleatoria que nadie conoce. Para
    habilitarlo: `reset-password` (la persona elige la suya en el equipo de Sistemas) y
    `enable-user`.
  - Usuario existente: se actualizan rol y áreas si cambiaron y se cierran sus sesiones.
    Su contraseña, segundo factor y estado no se tocan.
  - Nadie se borra: quien está en la base y no en el archivo solo se reporta.
"""

from __future__ import annotations

import csv
import secrets
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pipeline_ai.config import settings
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security import auth, clock, passwords
from pipeline_ai.security.areas import Area, format_memberships
from pipeline_ai.security.auth_store import AuthStore
from pipeline_ai.security.presets import preset_for

_EMAIL_HEADERS = ("correo", "email", "e-mail", "mail", "correo electronico")
_ROLE_HEADERS = ("puesto", "cargo", "rol", "posicion")


class ImportError_(ValueError):  # noqa: N801 - no sombrear el builtin ImportError
    """Archivo inválido o fuera del directorio de datos."""


@dataclass(frozen=True)
class PlannedUser:
    row: int
    username: str
    role: str
    memberships: dict[Area, Sensitivity]
    preset: str
    action: str  # "crear" | "actualizar" | "sin cambios"
    note: str = ""


@dataclass(frozen=True)
class SkippedRow:
    row: int
    reason: str


@dataclass(frozen=True)
class ImportPlan:
    users: list[PlannedUser]
    skipped: list[SkippedRow]
    not_in_file: list[str]


def data_dir() -> Path:
    return Path(settings.auth_db_path).resolve().parent


def _norm(text: object) -> str:
    raw = str(text or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", raw) if unicodedata.category(c) != "Mn")


def _check_location(path: Path) -> Path:
    resolved = path.resolve()
    allowed = data_dir()
    if not resolved.is_relative_to(allowed):
        raise ImportError_(
            f"El archivo debe estar en el directorio de datos ({allowed}), que git ignora."
        )
    if resolved.suffix.lower() not in {".xlsx", ".csv"}:
        raise ImportError_("Formato no soportado: usa .xlsx o .csv.")
    if not resolved.is_file():
        raise ImportError_(f"No existe el archivo {resolved.name}.")
    return resolved


def _delimiter(sample: str) -> str:
    """Separador del CSV: Excel en español suele exportar con ';'. El Sniffer de la librería
    falla con un título de una sola columna arriba de la tabla; si falla, gana el separador
    más frecuente."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return max(",;\t", key=sample.count)


def _read_rows(path: Path) -> list[list[object]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            return [list(r) for r in csv.reader(fh, delimiter=_delimiter(sample))]
    from openpyxl import load_workbook  # solo se necesita al importar Excel

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()


def _find_columns(rows: list[list[object]]) -> tuple[int, int, int]:
    """(fila del encabezado, columna de correo, columna de puesto). Busca en las 10 primeras
    filas para tolerar un título arriba de la tabla."""
    for index, row in enumerate(rows[:10]):
        headers = [_norm(c) for c in row]
        email = next((i for i, h in enumerate(headers) if h in _EMAIL_HEADERS), None)
        role = next((i for i, h in enumerate(headers) if h in _ROLE_HEADERS), None)
        if email is not None and role is not None:
            return index, email, role
    raise ImportError_("No encontré columnas de correo y puesto en el encabezado.")


def build_plan(path: Path, store: AuthStore) -> ImportPlan:
    rows = _read_rows(_check_location(path))
    header, email_col, role_col = _find_columns(rows)
    planned: list[PlannedUser] = []
    skipped: list[SkippedRow] = []
    seen: set[str] = set()
    for offset, row in enumerate(rows[header + 1 :], start=header + 2):
        cells = list(row) + [None] * (max(email_col, role_col) + 1 - len(row))
        email, puesto = str(cells[email_col] or "").strip(), str(cells[role_col] or "").strip()
        if not email and not puesto:
            continue  # fila vacía
        username = auth.normalize_username(email)
        if username is None or "@" not in username:
            skipped.append(SkippedRow(offset, "correo inválido"))
            continue
        if username in seen:
            skipped.append(SkippedRow(offset, f"{username} repetido en el archivo"))
            continue
        seen.add(username)
        preset = preset_for(puesto)
        if preset is None:
            skipped.append(SkippedRow(offset, f"{username}: puesto sin preset ({puesto!r})"))
            continue
        existing = store.get_user_by_username(username)
        if existing is None:
            action = "crear"
        elif existing.role == preset.role.value and (
            store.get_memberships(existing.id) == preset.memberships
        ):
            action = "sin cambios"
        else:
            action = "actualizar"
        planned.append(
            PlannedUser(
                row=offset,
                username=username,
                role=preset.role.value,
                memberships=dict(preset.memberships),
                preset=preset.name,
                action=action,
                note=preset.note,
            )
        )
    not_in_file = sorted(u.username for u in store.list_users() if u.username not in seen)
    return ImportPlan(planned, skipped, not_in_file)


def apply_plan(plan: ImportPlan, store: AuthStore) -> dict[str, int]:
    counts = {"creados": 0, "actualizados": 0, "sin_cambios": 0}
    for item in plan.users:
        if item.action == "crear":
            # Contraseña aleatoria descartada: la cuenta no sirve hasta reset-password.
            unknown = passwords.hash_password(secrets.token_urlsafe(32))
            user = store.create_user(item.username, unknown, item.role, now=clock.now())
            store.set_active(user.id, False)
            store.set_memberships(user.id, item.memberships)
            counts["creados"] += 1
        elif item.action == "actualizar":
            user = store.get_user_by_username(item.username)
            assert user is not None
            store.set_role(user.id, item.role)
            store.set_memberships(user.id, item.memberships)
            store.delete_user_sessions(user.id)
            counts["actualizados"] += 1
        else:
            counts["sin_cambios"] += 1
    return counts


def describe(plan: ImportPlan) -> Iterable[str]:
    for item in plan.users:
        areas = format_memberships(item.memberships) or "(sin áreas de datos)"
        line = (
            f"  fila {item.row:>3}  {item.action:<12} {item.username:<36} {item.role:<14} {areas}"
        )
        yield line + (f"   [{item.note}]" if item.note else "")
    for skip in plan.skipped:
        yield f"  fila {skip.row:>3}  OMITIDA      {skip.reason}"
    for username in plan.not_in_file:
        yield f"  en la base y no en el archivo (no se toca): {username}"
