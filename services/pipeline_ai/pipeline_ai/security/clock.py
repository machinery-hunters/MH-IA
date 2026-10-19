"""Reloj único de la capa de autenticación (los tests lo sustituyen para viajar en el tiempo)."""

import time


def now() -> float:
    return time.time()
