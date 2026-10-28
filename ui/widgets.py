# -*- coding: utf-8 -*-
"""
Componentes reutilizáveis da interface.
"""

from typing import Any, Iterable, List, Optional, Sequence

from qgis.PyQt.QtCore import QSortFilterProxyModel, Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QStandardItem, QStandardItemModel
from qgis.PyQt.QtWidgets import QAbstractItemView, QHeaderView, QTableView, QWidget

CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
DATA_ROLE = Qt.ItemDataRole.UserRole + 1


def check_item(checked: bool, enabled: bool = True, text: str = "", tooltip: str = "") -> QStandardItem:
    """
    Item com caixa de seleção, não editável.

    :param checked: estado inicial.
    :param enabled: ``False`` impede marcar (ex.: plugin do núcleo).
    :param text: texto ao lado da caixa (também usado na ordenação).
    """
    item = QStandardItem(text)
    item.setEditable(False)
    item.setCheckable(enabled)
    item.setCheckState(CHECKED if checked and enabled else UNCHECKED)
    if not enabled:
        item.setEnabled(False)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def text_item(text: Any, icon: Optional[QIcon] = None, tooltip: str = "") -> QStandardItem:
    """Item de texto não editável, com ícone e dica opcionais."""
    item = QStandardItem(str(text) if text is not None else "")
    item.setEditable(False)
    if icon is not None:
        item.setIcon(icon)
    if tooltip:
        item.setToolTip(tooltip)
    return item


class CheckTable(QTableView):
    """
    Tabela com modelo, filtro textual e ordenação por coluna.

    A coluna 0 guarda, em ``DATA_ROLE``, o identificador da linha (pasta do
    plugin, id da conexão etc.). O sinal ``checks_changed`` é emitido uma vez
    por alteração do usuário ou por operação em lote.
    """

    checks_changed = pyqtSignal()

    def __init__(self, headers: Sequence[str], stretch_column: int, sort_column: int,
                 parent: Optional[QWidget] = None) -> None:
        """
        :param headers: títulos das colunas.
        :param stretch_column: coluna que ocupa o espaço livre.
        :param sort_column: coluna da ordenação inicial (crescente).
        """
        super().__init__(parent)
        self.source = QStandardItemModel(0, len(headers), self)
        self.source.setHorizontalHeaderLabels(list(headers))
        self._bulk = False
        self.source.itemChanged.connect(self._on_item_changed)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.source)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setMinimumHeight(140)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(sort_column, Qt.SortOrder.AscendingOrder)

    def set_rows(self, rows: Iterable[List[QStandardItem]], row_ids: Iterable[Any]) -> None:
        """
        Substitui todas as linhas de uma vez (sem redesenhar linha a linha).

        :param rows: listas de itens, uma por linha.
        :param row_ids: identificador de cada linha, gravado na coluna 0.
        """
        self.setSortingEnabled(False)
        self.setUpdatesEnabled(False)
        self._bulk = True
        try:
            self.source.removeRows(0, self.source.rowCount())
            for items, row_id in zip(rows, row_ids):
                items[0].setData(row_id, DATA_ROLE)
                self.source.appendRow(items)
        finally:
            self._bulk = False
            self.setUpdatesEnabled(True)
            self.setSortingEnabled(True)
        self.checks_changed.emit()

    def set_filter(self, text: str) -> None:
        """Filtra as linhas que contenham o texto em qualquer coluna."""
        self.proxy.setFilterFixedString(text)

    def _on_item_changed(self, _item: QStandardItem) -> None:
        if not self._bulk:
            self.checks_changed.emit()

    def set_visible_checks(self, column: int, checked: bool) -> None:
        """Marca ou desmarca a coluna nas linhas visíveis após o filtro."""
        state = CHECKED if checked else UNCHECKED
        self._bulk = True
        try:
            for prow in range(self.proxy.rowCount()):
                src = self.proxy.mapToSource(self.proxy.index(prow, column))
                item = self.source.itemFromIndex(src)
                if item is not None and item.isCheckable() and item.isEnabled():
                    item.setCheckState(state)
        finally:
            self._bulk = False
        self.checks_changed.emit()

    def row_id(self, row: int) -> Any:
        """Identificador gravado na coluna 0 da linha (índice do modelo de origem)."""
        return self.source.item(row, 0).data(DATA_ROLE)

    def is_checked(self, row: int, column: int) -> bool:
        item = self.source.item(row, column)
        return item is not None and item.isCheckable() and item.checkState() == CHECKED

    def checked_ids(self, column: int) -> List[Any]:
        """Identificadores das linhas com a coluna marcada (inclui linhas ocultas pelo filtro)."""
        return [self.row_id(r) for r in range(self.source.rowCount()) if self.is_checked(r, column)]

    def row_count(self) -> int:
        return self.source.rowCount()
