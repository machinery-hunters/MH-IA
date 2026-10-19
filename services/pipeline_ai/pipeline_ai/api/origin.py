"""
Defensa CSRF para peticiones que cambian estado (POST/PUT/PATCH/DELETE).

La cookie de sesión ya es SameSite=Strict; esto es la segunda capa (defensa en profundidad):
- Si el navegador envía `Origin`, su host debe estar en la lista de hosts permitidos.
- Si no hay `Origin` pero sí `Sec-Fetch-Site`, debe ser same-origin o none.
- Clientes que no son navegador (curl, scripts) no envían ninguno: no pueden usar la cookie de
  otro usuario, así que no hay nada que proteger.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


def host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    host = host.lower()
    return any(fnmatchcase(host, pattern.lower()) for pattern in allowed_hosts)


def request_origin_ok(
    method: str,
    origin: str | None,
    sec_fetch_site: str | None,
    allowed_hosts: Sequence[str],
) -> bool:
    if method.upper() not in UNSAFE_METHODS:
        return True
    if origin is not None:
        if origin == "null":
            return False
        try:
            hostname = urlsplit(origin).hostname
        except ValueError:
            return False
        return bool(hostname) and host_allowed(hostname or "", allowed_hosts)
    if sec_fetch_site is not None:
        return sec_fetch_site.lower() in _SAFE_FETCH_SITES
    return True
