"""Behaviour tests for the groups dialog and the main window's group filter."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlcipher3
from PySide6.QtWidgets import QWidget

from cdkeys.db import DBConn, ensure_schema, open_db
from cdkeys.groups import (
    Group,
    add_product,
    create_group,
    get_group,
    group_tree,
    list_products,
    products_in_group,
)
from cdkeys.gui.app import UNGROUPED, KeyManagerWindow
from cdkeys.gui.groups import (
    TOP_LEVEL,
    DeleteChoice,
    GroupPrompts,
    GroupsDialog,
    delete_message,
)
from cdkeys.store import LicenceFields, add_licence

pytestmark = pytest.mark.usefixtures("qapp")

PRODUCTS = ["Game", "Office 2021", "Windows 11"]


def _seed(con: DBConn) -> None:
    ensure_schema(con)
    for i, product in enumerate(PRODUCTS):
        add_licence(con, LicenceFields(product_name=product, product_key=f"K{i}"))
    con.commit()


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    _seed(c)
    yield c
    c.close()


class Script:
    """Answers the dialog's questions from queues and records what it asked."""

    def __init__(
        self,
        names: list[str | None] | None = None,
        parent: str | None = None,
        products: list[str] | None = None,
        delete: DeleteChoice | None = None,
    ) -> None:
        self.names = names or []
        self.parent = parent
        self.products = products or []
        self.delete = delete
        self.asked: list[tuple[str, str]] = []
        self.parent_options: list[str] = []
        self.product_options: list[str] = []
        self.delete_asked: list[tuple[str, bool]] = []
        self.errors: list[str] = []

    def prompts(self) -> GroupPrompts:
        return GroupPrompts(
            ask_name=self.ask_name,
            choose_parent=self.choose_parent,
            choose_products=self.choose_products,
            ask_delete=self.ask_delete,
            show_error=self.show_error,
        )

    def ask_name(self, _p: QWidget, title: str, initial: str) -> str | None:
        self.asked.append((title, initial))
        return self.names.pop(0)

    def choose_parent(self, _p: QWidget, _title: str, options: list[str]) -> str | None:
        self.parent_options = options
        return self.parent

    def choose_products(self, _p: QWidget, names: list[str]) -> list[str]:
        self.product_options = names
        return self.products

    def ask_delete(
        self, _p: QWidget, message: str, nested: bool
    ) -> DeleteChoice | None:
        self.delete_asked.append((message, nested))
        return self.delete

    def show_error(self, _p: QWidget, message: str) -> None:
        self.errors.append(message)


def _dialog(con: DBConn, script: Script) -> GroupsDialog:
    return GroupsDialog(None, con, script.prompts())


def _pid(con: DBConn, name: str) -> int:
    return next(p.id for p in list_products(con) if p.name == name)


def _gid(con: DBConn, name: str) -> int:
    return next(g.id for g, _ in group_tree(con) if g.name == name)


# --- the dialog ---------------------------------------------------------------


def test_new_group_and_subgroup_show_nested(con: DBConn) -> None:
    script = Script(names=["Microsoft", "Windows"])
    dialog = _dialog(con, script)

    dialog.new_group()
    dialog.new_subgroup()  # inside the group just created (it is selected)

    assert dialog.group_names() == [("Microsoft", 0), ("Windows", 1)]
    assert script.asked[1] == ('New group inside "Microsoft"', "")
    top = dialog.tree.topLevelItem(0)
    assert top is not None
    assert top.childCount() == 1


def test_group_buttons_need_a_selection(con: DBConn) -> None:
    dialog = _dialog(con, Script())

    assert not dialog.rename_button.isEnabled()
    assert not dialog.add_button.isEnabled()
    assert dialog.new_button.isEnabled()


def test_duplicate_name_is_explained_and_asked_again(con: DBConn) -> None:
    create_group(con, "Microsoft")
    con.commit()  # the dialog rolls back after the refusal
    script = Script(names=["microsoft", "Games"])
    dialog = _dialog(con, script)

    dialog.new_group()

    assert script.errors == ['A group named "Microsoft" already exists.']
    assert script.asked[1] == ("New group", "microsoft")  # keeps what was typed
    assert [n for n, _ in dialog.group_names()] == ["Games", "Microsoft"]


def test_cancelling_new_group_changes_nothing(con: DBConn) -> None:
    dialog = _dialog(con, Script(names=[None]))

    dialog.new_group()

    assert dialog.group_names() == []


def test_rename_group(con: DBConn) -> None:
    gid = create_group(con, "Msft")
    con.commit()  # the dialog rolls back after the refusal
    script = Script(names=["", "Microsoft"])
    dialog = _dialog(con, script)
    dialog.select_group(gid)

    dialog.rename()

    assert script.errors == ["Enter a name for the group."]
    assert script.asked[0] == ("Rename group", "Msft")
    assert get_group(con, gid).name == "Microsoft"


def test_move_offers_only_valid_parents(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    create_group(con, "Old", parent_id=win)
    games = create_group(con, "Games")
    script = Script(parent="Games")
    dialog = _dialog(con, script)
    dialog.select_group(ms)

    dialog.move_group()

    assert script.parent_options == [TOP_LEVEL, "Games"]
    assert get_group(con, ms).parent_id == games


def test_move_to_top_level(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    dialog = _dialog(con, Script(parent=TOP_LEVEL))
    dialog.select_group(win)

    dialog.move_group()

    assert get_group(con, win).parent_id is None


def test_add_offers_products_not_yet_in_the_group(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")
    add_product(con, gid, _pid(con, "Office 2021"))
    script = Script(products=["Windows 11"])
    dialog = _dialog(con, script)
    dialog.select_group(gid)

    dialog.add_products()

    assert script.product_options == ["Game", "Windows 11"]
    assert dialog.product_names() == ["Office 2021", "Windows 11"]


def test_add_when_every_product_is_already_in_the_group(con: DBConn) -> None:
    gid = create_group(con, "All")
    for name in PRODUCTS:
        add_product(con, gid, _pid(con, name))
    script = Script()
    dialog = _dialog(con, script)
    dialog.select_group(gid)

    dialog.add_products()

    assert script.errors == ["Every product is already in this group."]


def test_remove_selected_products(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")
    for name in ("Office 2021", "Windows 11"):
        add_product(con, gid, _pid(con, name))
    dialog = _dialog(con, Script())
    dialog.select_group(gid)
    dialog.products.item(0).setSelected(True)  # Office 2021

    dialog.remove_products()

    assert dialog.product_names() == ["Windows 11"]


def test_delete_nested_group_moving_products_up(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    add_product(con, win, _pid(con, "Windows 11"))
    script = Script(delete=DeleteChoice.MOVE_UP)
    dialog = _dialog(con, script)
    dialog.select_group(win)

    dialog.delete()

    message, nested = script.delete_asked[0]
    assert nested
    assert 'move up into "Microsoft"' in message
    assert dialog.group_names() == [("Microsoft", 0)]
    assert dialog.product_names() == ["Windows 11"]  # parent now selected


def test_delete_nested_group_ungrouping_products(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    add_product(con, win, _pid(con, "Windows 11"))
    dialog = _dialog(con, Script(delete=DeleteChoice.UNGROUP))
    dialog.select_group(win)

    dialog.delete()

    assert products_in_group(con, ms) == []
    assert [p.name for p in list_products(con)] == PRODUCTS


def test_delete_top_level_group_and_cancel(con: DBConn) -> None:
    gid = create_group(con, "Games")
    add_product(con, gid, _pid(con, "Game"))
    script = Script(delete=None)
    dialog = _dialog(con, script)
    dialog.select_group(gid)

    dialog.delete()
    assert dialog.group_names() == [("Games", 0)]  # cancelled

    script.delete = DeleteChoice.UNGROUP
    dialog.delete()
    message, nested = script.delete_asked[1]
    assert not nested
    assert "1 product will lose this group." in message
    assert dialog.group_names() == []
    assert [p.name for p in list_products(con)] == PRODUCTS


def test_delete_message_mentions_subgroups_and_safety() -> None:
    group = Group(2, "Windows", 1)
    parent = Group(1, "Microsoft", None)

    message = delete_message(group, parent, products=3, subs=2)

    assert message.splitlines() == [
        'Delete the group "Windows"?',
        'Its 2 subgroup(s) will move into "Microsoft".',
        '3 products can move up into "Microsoft", or be ungrouped from this branch.',
        "No products or licences are deleted.",
    ]
    top = delete_message(parent, None, products=0, subs=1)
    assert "will move to the top level" in top


def test_dialog_changes_persist_after_reopening(tmp_path: Path) -> None:
    db = tmp_path / "keys.sqlite3"
    con = open_db(db, "pw", create=True)
    _seed(con)
    dialog = _dialog(con, Script(names=["Microsoft"], products=["Office 2021"]))
    dialog.new_group()
    dialog.add_products()
    dialog.close()
    con.close()

    con = open_db(db, "pw")
    try:
        gid = _gid(con, "Microsoft")
        assert [p.name for p in products_in_group(con, gid)] == ["Office 2021"]
    finally:
        con.close()


# --- the main window filter ---------------------------------------------------


@pytest.fixture
def window(con: DBConn) -> Iterator[KeyManagerWindow]:
    calls: list[None] = []

    def manage(_parent: QWidget, c: DBConn) -> None:
        calls.append(None)
        ms = create_group(c, "Microsoft")
        create_group(c, "Windows", parent_id=ms)
        c.commit()

    w = KeyManagerWindow(con, Path("test.sqlite3"), manage=manage)
    yield w
    w.close()
    w.deleteLater()


def _listed(window: KeyManagerWindow) -> list[str]:
    proxy = window.proxy
    return [str(proxy.index(r, 0).data()) for r in range(proxy.rowCount())]


def _choose(window: KeyManagerWindow, data: object) -> None:
    index = window.group_filter.findData(data)
    assert index >= 0, data
    window.group_filter.setCurrentIndex(index)


def test_filter_starts_on_all_licences(window: KeyManagerWindow) -> None:
    assert window.group_filter.currentText() == "All licences"
    assert _listed(window) == PRODUCTS


def test_manage_groups_refreshes_the_filter(window: KeyManagerWindow) -> None:
    window.groups_action.trigger()

    labels = [
        window.group_filter.itemText(i) for i in range(window.group_filter.count())
    ]
    assert labels == ["All licences", "Ungrouped", "Microsoft", "    Windows"]


def test_filter_by_group_includes_subgroups(
    window: KeyManagerWindow, con: DBConn
) -> None:
    window.groups_action.trigger()
    add_product(con, _gid(con, "Microsoft"), _pid(con, "Office 2021"))
    add_product(con, _gid(con, "Windows"), _pid(con, "Windows 11"))

    _choose(window, _gid(con, "Microsoft"))
    assert _listed(window) == ["Office 2021", "Windows 11"]

    _choose(window, _gid(con, "Windows"))
    assert _listed(window) == ["Windows 11"]

    _choose(window, UNGROUPED)
    assert _listed(window) == ["Game"]


def test_filter_combines_with_search(window: KeyManagerWindow, con: DBConn) -> None:
    window.groups_action.trigger()
    add_product(con, _gid(con, "Microsoft"), _pid(con, "Office 2021"))
    add_product(con, _gid(con, "Microsoft"), _pid(con, "Windows 11"))
    _choose(window, _gid(con, "Microsoft"))

    window.search.setText("office")

    assert _listed(window) == ["Office 2021"]


def test_filter_keeps_its_group_across_manage_and_falls_back_when_gone(
    window: KeyManagerWindow, con: DBConn
) -> None:
    window.groups_action.trigger()
    gid = _gid(con, "Windows")
    _choose(window, gid)

    window.manage = lambda _p, _c: None
    window.manage_groups()
    assert window.group_filter.currentData() == gid

    def drop(_p: QWidget, c: DBConn) -> None:
        c.execute("DELETE FROM product_group WHERE id = ?", (gid,))
        c.commit()

    window.manage = drop
    window.manage_groups()
    assert window.group_filter.currentText() == "All licences"
    assert _listed(window) == PRODUCTS
