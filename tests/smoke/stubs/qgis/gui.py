from PyQt5.QtWidgets import QGroupBox, QLabel
class QgsCollapsibleGroupBox(QGroupBox):
    def setCollapsed(self, c): self.collapsed = c
class QgsMessageBar(QLabel):
    pushed = []
    def pushMessage(self, title, text, level, duration): QgsMessageBar.pushed.append((level, title, text))
