import pytest

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.roles import (
    Permission,
    Role,
    can_view,
    has_permission,
    permissions_for,
)


def test_control_total_has_every_permission():
    assert permissions_for(Role.CONTROL_TOTAL) == frozenset(Permission)


def test_only_control_total_has_admin_system():
    for role in Role:
        assert has_permission(role, Permission.ADMIN_SYSTEM) == (role is Role.CONTROL_TOTAL)


def test_consulta_cannot_see_confidential_but_lider_can():
    assert can_view(Role.CONSULTA, Sensitivity.INTERNAL)
    assert not can_view(Role.CONSULTA, Sensitivity.CONFIDENTIAL)
    assert can_view(Role.LIDER, Sensitivity.CONFIDENTIAL)


def test_restricted_requires_admin_system():
    assert not can_view(Role.LIDER, Sensitivity.RESTRICTED)
    assert not can_view(Role.CONFIGURACION, Sensitivity.RESTRICTED)
    assert can_view(Role.CONTROL_TOTAL, Sensitivity.RESTRICTED)


@pytest.mark.parametrize("role", [None, "", "root", "admin", "CONSULTA "])
def test_unknown_role_is_denied_everything(role):
    assert permissions_for(role) == frozenset()
    assert not can_view(role, Sensitivity.PUBLIC)
