"""Redactor determinista: reemplaza por marcadores, restaura y deja el texto verificable."""

import pytest

from pipeline_ai.privacy.redactor import (
    Entity,
    Redaction,
    known_name_strings,
    name_variants,
    redact_names,
    redact_sensitive,
)
from pipeline_ai.privacy.scrubber import normalize_for_scan, normalize_with_map, scan

# Datos falsos con forma de credencial, armados en tiempo de ejecución para que el archivo
# no contenga ningún literal que un escáner de secretos pueda confundir con uno real.
_FAKE_API_KEY = "sk-" + "ant-api03-" + "abcdefghijklmnopqrstuv"


# -- normalize_with_map: mismo texto que normalize_for_scan, con posiciones ------------------


@pytest.mark.parametrize(
    "text",
    [
        "  Hola   MUNDO ",
        "ＡＢＣ １２３",
        "Ñandú café​!",
        "a b",
        "",
        "   ",
        "tab\tsep\nnl",
        "ﬁnal",
        "éxito",
        "RFC AAA​010101AB1",
        "Cotización de José: correo a@b.com",
    ],
)
def test_normalize_with_map_matches_normalize_for_scan(text):
    normalized, starts, ends = normalize_with_map(text)

    assert normalized == normalize_for_scan(text)
    assert len(starts) == len(ends) == len(normalized)
    assert all(0 <= s < e <= len(text) for s, e in zip(starts, ends, strict=True))


# -- Datos con forma reconocible -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "original", "label"),
    [
        ("correo juan.perez@acme.com.mx hoy", "juan.perez@acme.com.mx", "CORREO"),
        ("RFC AAA010101AB1 vigente", "AAA010101AB1", "RFC"),
        ("CURP PEPJ850101HJCRRN09 listo", "PEPJ850101HJCRRN09", "CURP"),
        ("llama al 653 133 2053 por favor", "653 133 2053", "TELEFONO"),
        ("CLABE 012345678901234567 del proveedor", "012345678901234567", "CLABE"),
        ("tarjeta 4111 1111 1111 1111 vence", "4111 1111 1111 1111", "TARJETA"),
        ("pagó $1,250,000.00 ayer", "$1,250,000.00", "MONTO"),
        ("costó 50 mil pesos", "50 mil pesos", "MONTO"),
        (f"clave {_FAKE_API_KEY} activa", _FAKE_API_KEY, "CREDENCIAL"),
        ("folio 202500112 abierto", "202500112", "NUMERO"),
    ],
)
def test_sensitive_data_is_replaced_by_a_marker_and_restored_exactly(text, original, label):
    redaction = Redaction()

    redacted = redact_sensitive(text, redaction)

    assert original not in redacted
    assert f"[{label}_1]" in redacted
    assert scan(redacted).clean
    assert redaction.restore(redacted) == text


def test_the_same_value_gets_the_same_marker_across_texts_and_new_values_get_new_ones():
    redaction = Redaction()

    first = redact_sensitive("escribe a ventas@acme.com", redaction)
    second = redact_sensitive("o a VENTAS@ACME.COM, no a otro@acme.com", redaction)

    assert first == "escribe a [CORREO_1]"
    assert second == "o a [CORREO_1], no a [CORREO_2]"


def test_harmless_numbers_and_text_are_left_alone():
    text = (
        "Oficina de 8:00 a 17:00, 12 cajones, año 2026, 30% de anticipo, "
        "versión 3.1, extensión 120, vence el 2026-11-27."
    )

    assert redact_sensitive(text, Redaction()) == text
    assert scan(text).clean


def test_accents_and_case_outside_the_findings_are_preserved():
    redacted = redact_sensitive("Cotización de José: correo a@b.com", Redaction())

    assert redacted == "Cotización de José: correo [CORREO_1]"


def test_evasions_that_the_scrubber_catches_are_also_redacted():
    redaction = Redaction()

    zero_width = redact_sensitive("RFC AAA​010101AB1", redaction)
    full_width = redact_sensitive("celular ６５３１３３２０５３", redaction)
    obfuscated = redact_sensitive("juan [at] acme [dot] com", redaction)

    assert "010101" not in zero_width and "[RFC_1]" in zero_width
    assert "６５３" not in full_width and "[TELEFONO_1]" in full_width
    assert "acme" not in obfuscated and "[CORREO_1]" in obfuscated


def test_a_text_full_of_sensitive_data_passes_the_scrubber_after_redaction():
    text = (
        "Cliente CFN010101AB1, correo laura@constructora.example, tel 6531234567, "
        "CLABE 012345678901234567, tarjeta 4111111111111111, pagó USD 85,000 y "
        "$2,450,000 MXN, folio 202500112."
    )

    redacted = redact_sensitive(text, Redaction())

    assert scan(redacted).clean


# -- Mapa local y restauración -----------------------------------------------------------------


def test_the_restore_map_is_not_in_the_repr_and_unknown_markers_are_left_untouched():
    redaction = Redaction()
    redaction.placeholder("RFC", "AAA010101AB1")

    assert "AAA010101AB1" not in repr(redaction)
    assert redaction.restore("[RFC_1] y [RFC_9]") == "AAA010101AB1 y [RFC_9]"


# -- Nombres propios (los detecta el modelo; el reemplazo lo hace el código) --------------------


def test_company_and_person_names_are_replaced_with_all_their_variants():
    redaction = Redaction()
    entities = [
        Entity("Constructora Ficticia del Norte SA de CV", "empresa"),
        Entity("Laura Mendoza Prieto", "persona"),
    ]
    text = (
        "Cliente: Constructora Ficticia del Norte S.A. de C.V. Contacto: Laura Mendoza Prieto. "
        "Llamar a laura mendoza el lunes; la CONSTRUCTORA FICTICIA DEL NORTE pidió cotización."
    )

    redacted = redact_names(text, entities, redaction)

    for forbidden in ("Constructora", "CONSTRUCTORA", "Ficticia", "Laura", "laura", "Mendoza"):
        assert forbidden not in redacted
    assert redacted.count("[EMPRESA_1]") == 2 and redacted.count("[PERSONA_1]") == 2
    # Lo que no es nombre se conserva tal cual (incluida la forma legal "S.A. de C.V.").
    assert "Llamar a" in redacted and "pidió cotización" in redacted
    assert "[EMPRESA_1] S.A. de C.V." in redacted
    # Al restaurar vuelve el nombre de la entidad, sin duplicar la forma legal.
    restored = redaction.restore(redacted)
    assert "Laura Mendoza Prieto" in restored
    assert "Constructora Ficticia del Norte S.A. de C.V." in restored


def test_names_match_without_regard_to_accents_or_case():
    redacted = redact_names(
        "Hablé con JOSE NANDU LOPEZ y con José Ñandú.",
        [Entity("José Ñandú López", "persona")],
        Redaction(),
    )

    assert "JOSE" not in redacted and "José" not in redacted
    assert redacted.count("[PERSONA_1]") == 2


def test_a_name_only_matches_whole_words():
    text = "Acmeco y Pacme no son Acme; Acme sí."

    redacted = redact_names(text, [Entity("Acme", "empresa")], Redaction())

    assert redacted == "Acmeco y Pacme no son [EMPRESA_1]; [EMPRESA_1] sí."


@pytest.mark.parametrize("name", ["Al", "x", "12", "de la"])
def test_names_too_short_or_meaningless_are_ignored(name):
    text = "Al norte de la ciudad, 12 equipos."

    assert redact_names(text, [Entity(name, "persona")], Redaction()) == text


def test_markers_for_names_and_data_share_one_numbering_per_label():
    redaction = Redaction()
    step1 = redact_sensitive("Acme escribió a ventas@acme.com", redaction)
    step2 = redact_names(step1, [Entity("Acme", "empresa")], redaction)

    assert step2 == "[EMPRESA_1] escribió a [CORREO_1]"


def test_name_variants_cover_legal_suffixes_and_partial_person_names():
    company = name_variants(Entity("Grupo Industrial Ejemplo SA de CV", "empresa"))
    person = name_variants(Entity("Laura Mendoza Prieto", "persona"))

    assert ("grupo", "industrial", "ejemplo") in company
    assert ("grupo", "industrial") in company
    assert ("laura", "mendoza") in person and ("mendoza", "prieto") in person
    assert ("laura",) not in person  # un nombre de pila suelto no se redacta por sí solo


def test_known_name_strings_let_the_scrubber_verify_nothing_was_left():
    entities = [Entity("Acme S.A. de C.V.", "empresa")]
    redaction = Redaction()
    redacted = redact_names("Acme S.A. de C.V. debe", entities, redaction)
    leaked = "Acme debe"

    assert scan(redacted, known_name_strings(entities)).clean
    assert "nombre_conocido" in scan(leaked, known_name_strings(entities)).kinds
