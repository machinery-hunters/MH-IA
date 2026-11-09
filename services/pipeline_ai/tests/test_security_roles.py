import pytest

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area
from pipeline_ai.security.roles import (
    Permission,
    Principal,
    Role,
    has_permission,
    permissions_for,
    principal_view,
)


def test_control_total_has_every_permission():
    assert permissions_for(Role.CONTROL_TOTAL) == frozenset(Permission)


def test_only_control_total_has_admin_system():
    for role in Role:
        assert has_permission(role, Permission.ADMIN_SYSTEM) == (role is Role.CONTROL_TOTAL)


def test_every_role_that_uses_the_system_can_query():
    # Qué datos ve cada quien lo deciden sus áreas, no su rol (test_access.py).
    for role in Role:
        assert has_permission(role, Permission.QUERY)


def test_only_configuration_roles_manage_connectors():
    managers = {r for r in Role if has_permission(r, Permission.CONFIGURE_CONNECTORS)}
    assert managers == {Role.CONFIGURACION, Role.CONTROL_TOTAL}


def test_principal_has_no_areas_by_default_and_view_lists_them():
    assert Principal(user_id="u", role=Role.CONTROL_TOTAL).memberships == {}
    view = principal_view(
        Principal(
            user_id="u",
            role=Role.LIDER,
            username="ana",
            memberships={
                Area.INVENTARIO: Sensitivity.CONFIDENTIAL,
                Area.GENERAL: Sensitivity.INTERNAL,
            },
        )
    )
    assert view["areas"] == {"general": "INTERNAL", "inventario": "CONFIDENTIAL"}
    assert view["permissions"] == ["query"]


@pytest.mark.parametrize("role", [None, "", "root", "admin", "CONSULTA "])
def test_unknown_role_is_denied_everything(role):
    assert permissions_for(role) == frozenset()
