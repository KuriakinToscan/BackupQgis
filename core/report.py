# -*- coding: utf-8 -*-
"""
Registro de execução: lista das tarefas executadas numa operação, com
situação (sucesso, aviso, erro), detalhe e duração, e um resumo final.

Usado pelas janelas (log da janela e painel de mensagens do QGIS) e pelas
automações. Não importa QGIS nem Qt.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, List

OK, WARN, ERROR = "ok", "aviso", "erro"
SYMBOLS = {OK: "✔", WARN: "⚠", ERROR: "✖"}
DETAIL_LIMIT = 50  # itens listados por tarefa antes de resumir


def human_size(n: float) -> str:
    """Tamanho em unidades binárias (KiB, MiB, GiB)."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TiB"


@dataclass
class TaskRecord:
    """Uma tarefa executada."""

    name: str
    status: str = OK
    detail: str = ""
    seconds: float = 0.0
    items: List[str] = field(default_factory=list)


class RunReport:
    """Tarefas de uma operação e seu resumo."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.records: List[TaskRecord] = []
        self.started = time.time()
        self.finished = 0.0

    # ------------------------------------------------------------------
    def add(self, name: str, status: str = OK, detail: str = "", items: List[str] = None,
            seconds: float = 0.0) -> TaskRecord:
        record = TaskRecord(name, status, detail, seconds, list(items or []))
        self.records.append(record)
        return record

    def ok(self, name: str, detail: str = "", items: List[str] = None) -> TaskRecord:
        return self.add(name, OK, detail, items)

    def warn(self, name: str, detail: str = "", items: List[str] = None) -> TaskRecord:
        return self.add(name, WARN, detail, items)

    def error(self, name: str, detail: str = "", items: List[str] = None) -> TaskRecord:
        return self.add(name, ERROR, detail, items)

    @contextmanager
    def task(self, name: str) -> Iterator[TaskRecord]:
        """
        Cronometra uma tarefa. Exceções viram registro de erro e são relançadas;
        o bloco pode ajustar ``status``/``detail`` do registro devolvido.
        """
        record = TaskRecord(name)
        self.records.append(record)
        t0 = time.time()
        try:
            yield record
        except BaseException as exc:
            record.status = ERROR
            record.detail = record.detail or str(exc) or exc.__class__.__name__
            raise
        finally:
            record.seconds = time.time() - t0

    def finish(self) -> "RunReport":
        self.finished = time.time()
        return self

    # ------------------------------------------------------------------
    def count(self, status: str) -> int:
        return sum(1 for r in self.records if r.status == status)

    @property
    def status(self) -> str:
        """Pior situação entre as tarefas."""
        if self.count(ERROR):
            return ERROR
        return WARN if self.count(WARN) else OK

    def summary(self) -> str:
        """Linha de resumo: quantidade de tarefas, sucessos, avisos, erros e duração."""
        elapsed = (self.finished or time.time()) - self.started
        return (f"Resumo: {len(self.records)} tarefas — {self.count(OK)} com sucesso, {self.count(WARN)} com aviso, "
                f"{self.count(ERROR)} com erro ({elapsed:.1f} s).")

    def lines(self) -> List[str]:
        """Texto completo para o log: título, uma linha por tarefa (e itens), resumo."""
        out = [f"══ {self.title} ══"]
        for r in self.records:
            text = f"{SYMBOLS[r.status]} {r.name}"
            if r.detail:
                text += f": {r.detail}"
            if r.seconds >= 0.5:
                text += f" ({r.seconds:.1f} s)"
            out.append(text)
            for item in r.items[:DETAIL_LIMIT]:
                out.append(f"    · {item}")
            if len(r.items) > DETAIL_LIMIT:
                out.append(f"    · ... e mais {len(r.items) - DETAIL_LIMIT}")
        out.append(f"{SYMBOLS[self.status]} {self.summary()}")
        return out
