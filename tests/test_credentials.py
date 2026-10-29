# -*- coding: utf-8 -*-
from core.credentials import apply_authcfg, find_credentials


def _b(v):
    return {"type": "basic", "value": v}


def test_postgres_password_becomes_authcfg():
    vals = {"PostgreSQL/connections/PG/host": _b("db"), "PostgreSQL/connections/PG/username": _b("ana"),
            "PostgreSQL/connections/PG/password": _b("segredo"),
            "PostgreSQL/connections/PG/authcfg": {"type": "null", "value": None}}
    creds = find_credentials("PostGIS: PG", vals)
    assert len(creds) == 1
    c = creds[0]
    assert (c.username, c.password, c.authcfg_key, c.existing_authcfg) == \
        ("ana", "segredo", "PostgreSQL/connections/PG/authcfg", "")
    out, removed = apply_authcfg(vals, creds, {c.password_key: "abc1234"})
    assert removed == ["PostgreSQL/connections/PG/password"]
    assert "PostgreSQL/connections/PG/password" not in out
    assert out["PostgreSQL/connections/PG/authcfg"] == _b("abc1234")
    assert out["PostgreSQL/connections/PG/username"] == _b("ana")


def test_tree_and_legacy_ows_keys():
    vals = {"connections/ows/items/wms/connections/items/IBGE/password": _b("p"),
            "connections/ows/items/wms/connections/items/IBGE/username": _b("u"),
            "qgis/WMS/Velho/password": _b("x"), "qgis/WMS/Velho/authcfg": _b("exist01")}
    creds = {c.password_key: c for c in find_credentials("WMS", vals)}
    tree = creds["connections/ows/items/wms/connections/items/IBGE/password"]
    assert tree.authcfg_key == "connections/ows/items/wms/connections/items/IBGE/authcfg" and tree.username == "u"
    legacy = creds["qgis/WMS/Velho/password"]
    assert legacy.existing_authcfg == "exist01"
    out, _ = apply_authcfg(vals, list(creds.values()), {tree.password_key: "new0001"})
    assert out["qgis/WMS/Velho/authcfg"] == _b("exist01")  # authcfg existente mantido
    assert not any(k.endswith("/password") for k in out)


def test_empty_password_ignored():
    vals = {"PostgreSQL/connections/PG/password": _b(""), "PostgreSQL/connections/PG/host": _b("db")}
    assert find_credentials("PG", vals) == []


def test_referenced_authcfg():
    from core.credentials import referenced_authcfg
    vals = {"connections/xyz/items/A/authcfg": _b("abc1234"), "connections/xyz/items/B/authcfg": _b(""),
            "PostgreSQL/connections/P/authcfg": {"type": "null", "value": None}, "x/url": _b("u")}
    assert referenced_authcfg(vals) == ["abc1234"]
