"""The key manager main window: search, licence table, details, add/edit/delete."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cdkeys.db import DBConn
from cdkeys.gui.editor import MULTILINE, ask_licence
from cdkeys.gui.format import (
    COLUMNS,
    DETAIL_FIELDS,
    SECRET_FIELDS,
    copied_message,
    row_values,
)
from cdkeys.store import (
    Licence,
    LicenceError,
    LicenceFields,
    add_licence,
    delete_licence,
    get_licence,
    list_licences,
    update_licence,
)

TITLE = "CD Key Manager"
STATUS_TIMEOUT_MS = 5000

# Injected so tests can drive the window without blocking modal dialogs.
AskLicence = Callable[[QWidget | None, str, LicenceFields | None], LicenceFields | None]
Confirm = Callable[[QWidget, str], bool]
ShowError = Callable[[QWidget, str], None]

ModelIndex = QModelIndex | QPersistentModelIndex


def _confirm(parent: QWidget, message: str) -> bool:
    answer = QMessageBox.question(parent, TITLE, message)
    return answer == QMessageBox.StandardButton.Yes


def _show_error(parent: QWidget, message: str) -> None:
    QMessageBox.warning(parent, TITLE, message)


class LicenceTableModel(QAbstractTableModel):
    """Rows of licences, displayed with secrets masked (see row_values)."""

    def __init__(self) -> None:
        super().__init__()
        self.licences: list[Licence] = []

    def set_licences(self, licences: list[Licence]) -> None:
        self.beginResetModel()
        self.licences = licences
        self.endResetModel()

    def rowCount(self, parent: ModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.licences)

    def columnCount(self, parent: ModelIndex | None = None) -> int:
        return 0 if parent is not None and parent.isValid() else len(COLUMNS)

    def data(
        self, index: ModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if role == Qt.ItemDataRole.DisplayRole and index.isValid():
            return row_values(self.licences[index.row()])[index.column()]
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return COLUMNS[section][1]
        return None

    def row_of(self, licence_id: str) -> int | None:
        for row, lic in enumerate(self.licences):
            if lic.id == licence_id:
                return row
        return None


class KeyManagerWindow(QMainWindow):
    """Main window. The caller owns ``con`` and closes it."""

    def __init__(
        self,
        con: DBConn,
        db_path: Path,
        ask: AskLicence = ask_licence,
        confirm: Confirm = _confirm,
        show_error: ShowError = _show_error,
    ) -> None:
        super().__init__()
        self.con = con
        self.ask = ask
        self.confirm = confirm
        self.show_error = show_error
        self.selected: Licence | None = None
        self.setWindowTitle(TITLE)
        self.resize(1000, 560)

        self._build_toolbar()
        self.model = LicenceTableModel()
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.table = self._build_table()
        details = self._build_details()

        splitter = QSplitter()
        splitter.addWidget(self.table)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([640, 420])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(f"Database: {db_path}")

        self.refresh()
        # Fit columns to the data once; after that the user's widths stand.
        self.table.resizeColumnsToContents()

    # --- layout -------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = QToolBar("Actions")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.add_action = QAction("Add…", self)
        self.add_action.setShortcut(QKeySequence.StandardKey.New)
        self.add_action.triggered.connect(self.add)
        self.edit_action = QAction("Edit…", self)
        self.edit_action.setShortcut(QKeySequence("F2"))
        self.edit_action.triggered.connect(self.edit)
        self.delete_action = QAction("Delete", self)
        self.delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        self.delete_action.triggered.connect(self.delete)
        for action in (self.add_action, self.edit_action, self.delete_action):
            bar.addAction(action)
        spacer = QWidget()
        spacer.setFixedWidth(16)
        bar.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all fields…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.refresh())
        bar.addWidget(self.search)

    def _build_table(self) -> QTableView:
        table = QTableView()
        table.setModel(self.proxy)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(True)
        table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        table.horizontalHeader().setStretchLastSection(True)
        table.selectionModel().selectionChanged.connect(
            lambda *_args: self._on_select()
        )
        table.doubleClicked.connect(lambda _index: self.edit())
        return table

    def _build_details(self) -> QGroupBox:
        box = QGroupBox("Details")
        form = QFormLayout()
        self.fields: dict[str, QLineEdit | QPlainTextEdit] = {}
        for name, label in DETAIL_FIELDS:
            field: QLineEdit | QPlainTextEdit
            if name == MULTILINE:
                field = QPlainTextEdit()
                field.setReadOnly(True)
                field.setFixedHeight(72)
            else:
                field = QLineEdit()
                field.setReadOnly(True)
            copy = QToolButton()
            copy.setText("Copy")
            copy.setToolTip(f"Copy {label.lower()} to the clipboard")
            copy.clicked.connect(partial(self.copy_field, name, label))
            row = QHBoxLayout()
            row.addWidget(field)
            row.addWidget(copy)
            form.addRow(label, row)
            self.fields[name] = field
        self.show_secrets = QCheckBox("Show key and serial")
        self.show_secrets.toggled.connect(lambda _on: self._apply_secret_visibility())
        layout = QVBoxLayout(box)
        layout.addLayout(form)
        layout.addWidget(self.show_secrets)
        layout.addStretch()
        self._apply_secret_visibility()
        return box

    # --- behaviour ----------------------------------------------------------

    def refresh(self, select_id: str | None = None) -> None:
        """Reload rows from the DB, keeping or setting the selection."""
        keep = select_id or (self.selected.id if self.selected else None)
        self.model.set_licences(list_licences(self.con, self.search.text()))
        row = self.model.row_of(keep) if keep else None
        if row is None:
            self._show(None)
            return
        view_index = self.proxy.mapFromSource(self.model.index(row, 0))
        self.table.selectRow(view_index.row())
        self.table.scrollTo(view_index)
        self._show(self.model.licences[row])

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._show(None)
            return
        source = self.proxy.mapToSource(rows[0])
        lic = self.model.licences[source.row()]
        if self.selected is None or lic.id != self.selected.id:
            self._show(get_licence(self.con, lic.id))

    def _show(self, lic: Licence | None) -> None:
        self.selected = lic
        for name, field in self.fields.items():
            value = (getattr(lic, name) if lic else None) or ""
            if isinstance(field, QPlainTextEdit):
                field.setPlainText(value)
            else:
                field.setText(value)
        self.edit_action.setEnabled(lic is not None)
        self.delete_action.setEnabled(lic is not None)

    def _apply_secret_visibility(self) -> None:
        mode = (
            QLineEdit.EchoMode.Normal
            if self.show_secrets.isChecked()
            else QLineEdit.EchoMode.Password
        )
        for name in SECRET_FIELDS:
            field = self.fields[name]
            if isinstance(field, QLineEdit):
                field.setEchoMode(mode)

    def status(self) -> str:
        return self.statusBar().currentMessage()

    def copy_field(self, name: str, label: str) -> None:
        """Put one field of the selected licence on the clipboard."""
        lic = self.selected
        if lic is None:
            self.statusBar().showMessage("Select a licence first.", STATUS_TIMEOUT_MS)
            return
        value = getattr(lic, name)
        if not value:
            self.statusBar().showMessage(
                f"{lic.product_name} has no {label.lower()}.", STATUS_TIMEOUT_MS
            )
            return
        QGuiApplication.clipboard().setText(value)
        self.statusBar().showMessage(copied_message(label, lic), STATUS_TIMEOUT_MS)

    def _write[T](self, action: Callable[[], T]) -> tuple[bool, T | None]:
        """Run a store write and commit it; roll back on any failure.

        Returns (True, result), or (False, None) after showing a LicenceError
        (an expected refusal, e.g. a duplicate). Other errors propagate to
        the application's error reporting after the rollback.
        """
        try:
            result = action()
            self.con.commit()
            return True, result
        except LicenceError as e:
            self.con.rollback()
            self.show_error(self, str(e))
            return False, None
        except Exception:
            self.con.rollback()
            raise

    def _edit_loop(
        self,
        title: str,
        initial: LicenceFields | None,
        save: Callable[[LicenceFields], str],
    ) -> tuple[str, LicenceFields] | None:
        """Ask until the fields save or the user cancels.

        After a refusal the form reopens with what the user typed.
        Returns (licence id, fields) on success.
        """
        fields = initial
        while (fields := self.ask(self, title, fields)) is not None:
            ok, licence_id = self._write(partial(save, fields))
            if ok and licence_id is not None:
                return licence_id, fields
        return None

    def add(self) -> None:
        done = self._edit_loop("Add licence", None, lambda f: add_licence(self.con, f))
        if done:
            licence_id, fields = done
            self.search.clear()  # make sure the new licence is visible
            self.refresh(select_id=licence_id)
            self.statusBar().showMessage(
                f"Added licence for {fields.product_name}.", STATUS_TIMEOUT_MS
            )

    def edit(self) -> None:
        current = self.selected
        if current is None:
            return
        done = self._edit_loop(
            "Edit licence",
            current.fields,
            lambda f: update_licence(self.con, current.id, f),
        )
        if done:
            licence_id, fields = done
            self.selected = None  # the id may have changed
            self.refresh(select_id=licence_id)
            self.statusBar().showMessage(
                f"Saved licence for {fields.product_name}.", STATUS_TIMEOUT_MS
            )

    def delete(self) -> None:
        current = self.selected
        if current is None:
            return
        if not self.confirm(self, f"Delete the licence for {current.product_name}?"):
            return
        ok, _ = self._write(partial(delete_licence, self.con, current.id))
        if ok:
            self.selected = None
            self.refresh()
            self.statusBar().showMessage(
                f"Deleted licence for {current.product_name}.", STATUS_TIMEOUT_MS
            )
