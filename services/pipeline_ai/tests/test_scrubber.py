"""Scrubber determinista: qué detecta, qué deja pasar y cómo resiste evasiones simples."""

import pytest

from pipeline_ai.privacy.scrubber import normalize_for_scan, scan

# Datos falsos con forma de credencial, armados en tiempo de ejecución para que el archivo
# no contenga ningún literal que un escáner de secretos (gitleaks) pueda confundir con uno real.
_FAKE_API_KEY = "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuv"
_FAKE_PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("mi correo es juan.perez@acme.com.mx", "email"),
        ("escríbeme a juan [at] acme [dot] com", "email"),
        ("RFC AAA010101AB1", "rfc"),
        ("RFC persona física PEPJ850101AB3", "rfc"),
        ("CURP PEPJ850101HJCRRN09", "curp"),
        ("llámame al 653 133 2053", "telefono"),
        ("tel (653) 133-2053", "telefono"),
        ("whatsapp +52 1 653 133 2053", "telefono"),
        ("celular 6531332053", "telefono"),
        ("CLABE 012345678901234567", "clabe"),
        ("tarjeta 4111 1111 1111 1111", "tarjeta"),
        ("cuenta 1234 5678 9012 3456", "numero_largo"),
        ("folio 202500112", "numero_largo"),
        ("pagó $1,250,000.00", "monto"),
        ("pagó USD 5000", "monto"),
        ("pagó 5000 usd", "monto"),
        ("pagó 1,250,000", "monto"),
        ("pagó 12.500.000", "monto"),
        ("costó 2 millones", "monto"),
        ("costó 50 mil pesos", "monto"),
        (f"clave {_FAKE_API_KEY}", "credencial"),
        (_FAKE_PEM_HEADER, "credencial"),
    ],
)
def test_detects_each_kind(text, kind):
    assert kind in scan(text).kinds


@pytest.mark.parametrize(
    "text",
    [
        "celular ６５３１３３２０５３",  # dígitos de ancho completo
        "celular 653​133​2053",  # espacios de ancho cero entre los dígitos
        "celular 653\n133\n2053",  # salto de línea entre los dígitos
        "CORREO: JUAN.PEREZ@ACME.COM",  # mayúsculas
        "rfc aaa010101ab1",  # minúsculas
    ],
)
def test_resists_simple_evasions(text):
    assert not scan(text).clean


@pytest.mark.parametrize(
    "text",
    [
        "Cliente_A debe Monto_X por el Folio_X según Fuente_12",
        "La reunión es el 2026-11-27 a las 10:30",
        "Se vendieron 3 equipos en 2025 con 25 toneladas de capacidad",
        "Versión 1.2.3 del sistema",
        "Archivo con fecha 20261127",
        "[Fuente: Fuente_1]\nEl equipo fue enviado a subasta [Fuente: Fuente_2]",
        "Mil gracias por la ayuda",
        "",
    ],
)
def test_does_not_flag_placeholders_dates_or_small_numbers(text):
    assert scan(text).clean


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CURP PEPJ850101HJCRRN09", ("curp",)),
        ("RFC AAA010101AB1", ("rfc",)),
        ("juan123@acme.com", ("email",)),
        ("llámame al 653 133 2053", ("telefono",)),
        (f"clave {_FAKE_API_KEY}", ("credencial",)),
    ],
)
def test_a_finding_is_counted_once_under_its_most_specific_kind(text, expected):
    assert scan(text).kinds == expected


def test_counts_each_occurrence():
    report = scan("a@b.com y c@d.com")
    assert report.kinds == ("email",)
    assert report.findings[0].count == 2


def test_known_names_are_matched_without_accents_or_case():
    report = scan("la deuda es con GRÚAS DEL NORTE, s.a.", known_names=["Grúas del Norte"])
    assert report.kinds == ("nombre_conocido",)


def test_known_names_need_word_boundaries_and_minimum_length():
    assert scan("aplicacion", known_names=["Aplic"]).clean
    assert scan("el equipo", known_names=["el"]).clean


def test_report_never_contains_the_matched_value():
    report = scan("contacto: juan.perez@acme.com")
    assert report.kinds == ("email",)
    assert "juan" not in repr(report) and "acme" not in repr(report)


def test_normalization_strips_accents_invisible_chars_and_width():
    assert normalize_for_scan("ＡＢＣ​ ÁÉ  1") == "abc ae 1"


def test_text_containing_the_internal_mask_character_does_not_break_the_scan():
    assert scan("a\x00b").clean
