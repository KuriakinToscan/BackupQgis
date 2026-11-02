# -*- coding: utf-8 -*-
"""
Prévia da restauração: o que será criado, alterado ou mantido no QSettings.
"""

from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                                 QVBoxLayout, QWidget)

from ..core.keys import is_sensitive_key
from .widgets import CheckTable, text_item

STATUS_NEW = "Nova"
STATUS_CHANGED = "Alterada"
STATUS_SAME = "Igual"

PreviewRow = Tuple[str, str, str, str]  # chave, situação, valor atual, valor do backup


def _short(text: str, limit: int = 160) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RestorePreviewDialog(QDialog):
    """
    Lista as chaves do plano de restauração com a situação de cada uma.

    Em modo de confirmação exibe os botões Restaurar/Cancelar; em modo de
    simulação, apenas Fechar.
    """

    def __init__(self, rows: List[PreviewRow], extra_notes: List[str], confirm: bool,
                 parent: Optional[QWidget] = None) -> None:
        """
        :param rows: linhas calculadas pelo diálogo principal.
        :param extra_notes: avisos exibidos acima da tabela (plugins a instalar, senhas ausentes...).
        :param confirm: ``True`` para pedir confirmação da restauração.
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("Restaurar backup") if confirm else self.tr("Simulação da restauração"))
        self.resize(900, 520)
        self._rows = rows

        counts: Dict[str, int] = {STATUS_NEW: 0, STATUS_CHANGED: 0, STATUS_SAME: 0}
        for _, status, _, _ in rows:
            counts[status] = counts.get(status, 0) + 1

        layout = QVBoxLayout(self)
        summary = QLabel(self.tr(
            "{new} chaves novas, {changed} alteradas e {same} iguais ao valor atual."
        ).format(new=counts[STATUS_NEW], changed=counts[STATUS_CHANGED], same=counts[STATUS_SAME]), self)
        summary.setWordWrap(True)
        font = summary.font()
        font.setBold(True)
        summary.setFont(font)
        layout.addWidget(summary)

        for note in extra_notes:
            label = QLabel("• " + note, self)
            label.setWordWrap(True)
            layout.addWidget(label)

        tools = QHBoxLayout()
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(self.tr("Filtrar chaves..."))
        self.filter_edit.setClearButtonEnabled(True)
        self.only_changes = QCheckBox(self.tr("Somente novas e alteradas"), self)
        self.only_changes.setChecked(True)
        tools.addWidget(self.filter_edit)
        tools.addWidget(self.only_changes)
        layout.addLayout(tools)

        self.table = CheckTable([self.tr("Chave"), self.tr("Situação"), self.tr("Valor atual"),
                                 self.tr("Valor no backup")], stretch_column=0, sort_column=0, parent=self)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(self)
        if confirm:
            ok = buttons.addButton(self.tr("Restaurar"), QDialogButtonBox.ButtonRole.AcceptRole)
            ok.setDefault(True)
            buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        else:
            buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.filter_edit.textChanged.connect(self.table.set_filter)
        self.only_changes.toggled.connect(self._fill)
        self._fill()

    def _fill(self) -> None:
        only = self.only_changes.isChecked()
        rows = [r for r in self._rows if not (only and r[1] == STATUS_SAME)]
        self.table.set_rows(
            ([text_item(k), text_item(self.tr(s)), text_item(_short(cur)), text_item(_short(new))]
             for k, s, cur, new in rows),
            (r[0] for r in rows),
        )


def build_preview_rows(plan_values: Dict[str, Tuple[str, Any]], current: Dict[str, Optional[str]],
                       display_current: Dict[str, str], display_new: Dict[str, str]) -> List[PreviewRow]:
    """
    Monta as linhas da prévia.

    :param plan_values: ``{chave: (forma canônica do valor novo, valor)}``.
    :param current: ``{chave: forma canônica do valor atual ou None se não existe}``.
    :param display_current: texto exibido para o valor atual.
    :param display_new: texto exibido para o valor do backup.
    """
    rows: List[PreviewRow] = []
    for key in sorted(plan_values):
        canon_new = plan_values[key][0]
        canon_cur = current.get(key)
        if canon_cur is None:
            status = STATUS_NEW
        elif canon_cur == canon_new:
            status = STATUS_SAME
        else:
            status = STATUS_CHANGED
        if is_sensitive_key(key):
            cur_txt = "••••" if canon_cur is not None else ""
            new_txt = "••••"
        else:
            cur_txt = display_current.get(key, "")
            new_txt = display_new.get(key, "")
        rows.append((key, status, cur_txt, new_txt))
    return rows
