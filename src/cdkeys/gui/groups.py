"""The "Manage groups" dialog: nested product groups and their products (#653)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cdkeys.db import DBConn
from cdkeys.groups import (
    Group,
    GroupError,
    Product,
    add_product,
    create_group,
    delete_group,
    get_group,
    group_tree,
    list_products,
    move_group,
    products_in_group,
    remove_product,
    rename_group,
    subgroup_ids,
)

TITLE = "Manage groups"
TOP_LEVEL = "(top level)"


class DeleteChoice(Enum):
    MOVE_UP = auto()  # products move into the parent group
    UNGROUP = auto()  # products leave the branch


def delete_message(group: Group, parent: Group | None, products: int, subs: int) -> str:
    """What deleting ``group`` will do, for the confirmation."""
    lines = [f'Delete the group "{group.name}"?']
    noun = "product" if products == 1 else "products"
    if subs:
        where = f'into "{parent.name}"' if parent else "to the top level"
        lines.append(f"Its {subs} subgroup(s) will move {where}.")
    if parent is None:
        lines.append(f"{products} {noun} will lose this group.")
    else:
        lines.append(
            f'{products} {noun} can move up into "{parent.name}", or be '
            "ungrouped from this branch."
        )
    lines.append("No products or licences are deleted.")
    return "\n".join(lines)


def _ask_name(parent: QWidget, title: str, initial: str) -> str | None:
    text, ok = QInputDialog.getText(parent, title, "Group name:", text=initial)
    return text if ok else None


def _choose_parent(parent: QWidget, title: str, options: list[str]) -> str | None:
    choice, ok = QInputDialog.getItem(
        parent, title, "Put the group inside:", options, 0, False
    )
    return choice if ok else None


def _choose_products(parent: QWidget, names: list[str]) -> list[str]:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Add products")
    listing = QListWidget()
    listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    listing.addItems(names)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("Select the products to add (Ctrl or Shift for several):"))
    layout.addWidget(listing)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    return [item.text() for item in listing.selectedItems()]


def _ask_delete(parent: QWidget, message: str, nested: bool) -> DeleteChoice | None:
    box = QMessageBox(parent)
    box.setWindowTitle(TITLE)
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(message)
    choices: dict[object, DeleteChoice] = {}
    if nested:
        up = box.addButton("Move products up", QMessageBox.ButtonRole.AcceptRole)
        choices[up] = DeleteChoice.MOVE_UP
        out = box.addButton("Ungroup products", QMessageBox.ButtonRole.ActionRole)
        choices[out] = DeleteChoice.UNGROUP
    else:
        delete = box.addButton("Delete", QMessageBox.ButtonRole.AcceptRole)
        choices[delete] = DeleteChoice.UNGROUP
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return choices.get(box.clickedButton())


def _show_error(parent: QWidget, message: str) -> None:
    QMessageBox.warning(parent, TITLE, message)


@dataclass(frozen=True)
class GroupPrompts:
    """The modal questions the dialog asks; injected so tests need no clicks."""

    ask_name: Callable[[QWidget, str, str], str | None] = _ask_name
    choose_parent: Callable[[QWidget, str, list[str]], str | None] = _choose_parent
    choose_products: Callable[[QWidget, list[str]], list[str]] = _choose_products
    ask_delete: Callable[[QWidget, str, bool], DeleteChoice | None] = _ask_delete
    show_error: Callable[[QWidget, str], None] = _show_error


class GroupsDialog(QDialog):
    """Create, rename, nest and delete groups, and choose their products.

    Each change is committed immediately, like the main window's edits.
    """

    def __init__(
        self, parent: QWidget | None, con: DBConn, prompts: GroupPrompts | None = None
    ) -> None:
        super().__init__(parent)
        self.con = con
        self.prompts = prompts or GroupPrompts()
        self.setWindowTitle(TITLE)
        self.resize(640, 420)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._show_products)
        self.products = QListWidget()
        self.products.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.products_label = QLabel()

        self.new_button = QPushButton("New group…")
        self.new_button.clicked.connect(self.new_group)
        self.sub_button = QPushButton("New subgroup…")
        self.sub_button.clicked.connect(self.new_subgroup)
        self.rename_button = QPushButton("Rename…")
        self.rename_button.clicked.connect(self.rename)
        self.move_button = QPushButton("Move…")
        self.move_button.clicked.connect(self.move_group)
        self.delete_button = QPushButton("Delete…")
        self.delete_button.clicked.connect(self.delete)
        self.add_button = QPushButton("Add products…")
        self.add_button.clicked.connect(self.add_products)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_products)

        group_buttons = QHBoxLayout()
        for button in (
            self.new_button,
            self.sub_button,
            self.rename_button,
            self.move_button,
            self.delete_button,
        ):
            group_buttons.addWidget(button)
        product_buttons = QHBoxLayout()
        product_buttons.addWidget(self.add_button)
        product_buttons.addWidget(self.remove_button)
        product_buttons.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)

        grid = QGridLayout()
        grid.addWidget(QLabel("Groups"), 0, 0)
        grid.addWidget(self.products_label, 0, 1)
        grid.addWidget(self.tree, 1, 0)
        grid.addWidget(self.products, 1, 1)
        grid.addLayout(group_buttons, 2, 0)
        grid.addLayout(product_buttons, 2, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(close)

        self.reload()

    # --- state --------------------------------------------------------------

    def _selected_id(self) -> int | None:
        items = self.tree.selectedItems()
        return int(items[0].data(0, Qt.ItemDataRole.UserRole)) if items else None

    def selected_group(self) -> Group | None:
        group_id = self._selected_id()
        return None if group_id is None else get_group(self.con, group_id)

    def group_names(self) -> list[tuple[str, int]]:
        """(name, depth) of each group as shown in the tree, for tests."""
        return [(g.name, depth) for g, depth in group_tree(self.con)]

    def product_names(self) -> list[str]:
        return [self.products.item(i).text() for i in range(self.products.count())]

    def select_group(self, group_id: int | None) -> None:
        self.tree.clearSelection()
        for item in self._items():
            if item.data(0, Qt.ItemDataRole.UserRole) == group_id:
                item.setSelected(True)
                self.tree.setCurrentItem(item)
                return

    def _items(self) -> list[QTreeWidgetItem]:
        found: list[QTreeWidgetItem] = []
        top = self.tree.topLevelItemCount()
        stack = [self.tree.topLevelItem(i) for i in range(top)]
        while stack:
            item = stack.pop()
            if item is None:  # the stubs allow it for an out-of-range index
                continue
            found.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        return found

    def reload(self, select_id: int | None = None) -> None:
        """Rebuild the tree from the DB, keeping or setting the selection."""
        # Read the id from the tree, not the DB: the group may just have gone.
        keep = select_id if select_id is not None else self._selected_id()
        self.tree.clear()
        items: dict[int, QTreeWidgetItem] = {}
        for group, _depth in group_tree(self.con):
            parent = items.get(group.parent_id) if group.parent_id else None
            item = QTreeWidgetItem([group.name])
            item.setData(0, Qt.ItemDataRole.UserRole, group.id)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            items[group.id] = item
        self.tree.expandAll()
        self.select_group(keep)
        self._show_products()

    def _show_products(self) -> None:
        group = self.selected_group()
        self.products.clear()
        if group is None:
            self.products_label.setText("Select a group to see its products")
        else:
            self.products_label.setText(f'Products in "{group.name}"')
            for product in products_in_group(self.con, group.id):
                item = QListWidgetItem(product.name)
                item.setData(Qt.ItemDataRole.UserRole, product.id)
                self.products.addItem(item)
        for button in (
            self.sub_button,
            self.rename_button,
            self.move_button,
            self.delete_button,
            self.add_button,
            self.remove_button,
        ):
            button.setEnabled(group is not None)

    def _write[T](self, action: Callable[[], T]) -> tuple[bool, T | None]:
        """Run a group write and commit it; roll back on any failure."""
        try:
            result = action()
            self.con.commit()
            return True, result
        except GroupError as e:
            self.con.rollback()
            self.prompts.show_error(self, str(e))
            return False, None
        except Exception:
            self.con.rollback()
            raise

    # --- actions ------------------------------------------------------------

    def _create(self, title: str, parent_id: int | None) -> None:
        name: str | None = ""
        # After a refusal (e.g. a duplicate) ask again with what was typed.
        while (name := self.prompts.ask_name(self, title, name or "")) is not None:
            ok, group_id = self._write(partial(create_group, self.con, name, parent_id))
            if ok:
                self.reload(select_id=group_id)
                return

    def new_group(self) -> None:
        self._create("New group", None)

    def new_subgroup(self) -> None:
        group = self.selected_group()
        if group is not None:
            self._create(f'New group inside "{group.name}"', group.id)

    def rename(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        name: str | None = group.name
        while (
            name := self.prompts.ask_name(self, "Rename group", name or "")
        ) is not None:
            ok, _ = self._write(partial(rename_group, self.con, group.id, name))
            if ok:
                self.reload(select_id=group.id)
                return

    def move_group(self) -> None:  # not move(): QWidget.move positions the window
        group = self.selected_group()
        if group is None:
            return
        # A group cannot go inside itself or anything below it.
        excluded = {group.id, *subgroup_ids(self.con, group.id)}
        targets = {
            g.name: g.id for g, _ in group_tree(self.con) if g.id not in excluded
        }
        choice = self.prompts.choose_parent(
            self, f'Move "{group.name}"', [TOP_LEVEL, *targets]
        )
        if choice is None:
            return
        parent_id = None if choice == TOP_LEVEL else targets[choice]
        ok, _ = self._write(lambda: move_group(self.con, group.id, parent_id))
        if ok:
            self.reload(select_id=group.id)

    def delete(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        parent = get_group(self.con, group.parent_id) if group.parent_id else None
        message = delete_message(
            group,
            parent,
            len(products_in_group(self.con, group.id)),
            len([g for g, _ in group_tree(self.con) if g.parent_id == group.id]),
        )
        choice = self.prompts.ask_delete(self, message, parent is not None)
        if choice is None:
            return
        ok, _ = self._write(
            lambda: delete_group(
                self.con, group.id, ungroup_products=choice is DeleteChoice.UNGROUP
            )
        )
        if ok:
            self.reload(select_id=parent.id if parent else None)

    def add_products(self) -> None:
        group = self.selected_group()
        if group is None:
            return
        # A product in a subgroup already counts as in this group, as in the
        # main window's filter, so it is not offered again (#657).
        members = {
            p.id
            for gid in (group.id, *subgroup_ids(self.con, group.id))
            for p in products_in_group(self.con, gid)
        }
        candidates: dict[str, Product] = {
            p.name: p for p in list_products(self.con) if p.id not in members
        }
        if not candidates:
            self.prompts.show_error(self, "Every product is already in this group.")
            return
        chosen = self.prompts.choose_products(self, list(candidates))
        if not chosen:
            return

        def add_all() -> None:
            for name in chosen:
                add_product(self.con, group.id, candidates[name].id)

        ok, _ = self._write(add_all)
        if ok:
            self.reload(select_id=group.id)

    def remove_products(self) -> None:
        group = self.selected_group()
        items = self.products.selectedItems()
        if group is None or not items:
            return
        ids = [int(item.data(Qt.ItemDataRole.UserRole)) for item in items]

        def remove_all() -> None:
            for product_id in ids:
                remove_product(self.con, group.id, product_id)

        ok, _ = self._write(remove_all)
        if ok:
            self.reload(select_id=group.id)


ManageGroups = Callable[[QWidget, DBConn], None]


def manage_groups(parent: QWidget, con: DBConn) -> None:
    GroupsDialog(parent, con).exec()
