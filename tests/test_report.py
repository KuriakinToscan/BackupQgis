# -*- coding: utf-8 -*-
import pytest

from core.report import ERROR, OK, WARN, RunReport


def test_summary_and_status():
    rep = RunReport("Backup")
    rep.ok("Inventário", "3 arquivos")
    assert rep.status == OK
    rep.warn("Camadas remotas", "1 não copiada", items=["Banco"])
    assert rep.status == WARN
    rep.error("Cópia", "1 falha", items=[f"f{i}" for i in range(60)])
    assert rep.status == ERROR
    rep.finish()
    assert rep.summary().startswith("Resumo: 3 tarefas — 1 com sucesso, 1 com aviso, 1 com erro")
    lines = rep.lines()
    assert lines[0] == "══ Backup ══"
    assert lines[1] == "✔ Inventário: 3 arquivos"
    assert "    · Banco" in lines
    assert "    · ... e mais 10" in lines
    assert lines[-1].startswith("✖ Resumo:")


def test_task_context_records_exception():
    rep = RunReport("X")
    with rep.task("passo 1") as rec:
        rec.detail = "feito"
    with pytest.raises(ValueError):
        with rep.task("passo 2"):
            raise ValueError("quebrou")
    assert [(r.name, r.status, r.detail) for r in rep.records] == [("passo 1", OK, "feito"),
                                                                     ("passo 2", ERROR, "quebrou")]
