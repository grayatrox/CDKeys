from collections.abc import Iterator

import pytest
import sqlcipher3

from cdkeys.db import DBConn, ensure_schema
from cdkeys.groups import (
    DuplicateGroupError,
    GroupNotFoundError,
    InvalidGroupError,
    ProductNotFoundError,
    add_product,
    create_group,
    delete_group,
    get_group,
    group_tree,
    list_groups,
    list_products,
    move_group,
    product_names_under,
    products_in_group,
    remove_product,
    rename_group,
    subgroup_ids,
    ungrouped_product_names,
)
from cdkeys.store import LicenceFields, add_licence, delete_licence, list_licences


@pytest.fixture
def con() -> Iterator[DBConn]:
    c: DBConn = sqlcipher3.connect(":memory:")
    ensure_schema(c)
    for product, key in [("Windows 11", "W1"), ("Office 2021", "O1"), ("Game", "G")]:
        add_licence(c, LicenceFields(product_name=product, product_key=key))
    yield c
    c.close()


def _pid(con: DBConn, name: str) -> int:
    return next(p.id for p in list_products(con) if p.name == name)


def _names(con: DBConn, group_id: int) -> list[str]:
    return [p.name for p in products_in_group(con, group_id)]


# --- create / rename ----------------------------------------------------------


def test_create_group_trims_and_lists_sorted(con: DBConn) -> None:
    create_group(con, "  microsoft ")
    create_group(con, "Games")

    assert [g.name for g in list_groups(con)] == ["Games", "microsoft"]


@pytest.mark.parametrize("name", ["", "   "])
def test_create_group_refuses_empty_name(con: DBConn, name: str) -> None:
    with pytest.raises(InvalidGroupError, match="Enter a name"):
        create_group(con, name)


def test_group_names_are_unique_ignoring_case(con: DBConn) -> None:
    create_group(con, "Microsoft")

    with pytest.raises(DuplicateGroupError, match='"Microsoft" already exists'):
        create_group(con, "MICROSOFT")


def test_unique_names_cover_non_ascii_case(con: DBConn) -> None:
    # SQLite's NOCASE folds ASCII only; canon() folds the rest.
    create_group(con, "Ärger")

    with pytest.raises(DuplicateGroupError):
        create_group(con, "ärger")


def test_create_subgroup_in_unknown_parent_is_refused(con: DBConn) -> None:
    with pytest.raises(GroupNotFoundError):
        create_group(con, "Child", parent_id=999)


def test_rename_group(con: DBConn) -> None:
    gid = create_group(con, "Msft")

    rename_group(con, gid, "Microsoft")

    assert get_group(con, gid).name == "Microsoft"


def test_rename_to_own_name_in_other_case_is_allowed(con: DBConn) -> None:
    gid = create_group(con, "microsoft")

    rename_group(con, gid, "Microsoft")

    assert get_group(con, gid).name == "Microsoft"


def test_rename_refuses_another_groups_name_and_empty(con: DBConn) -> None:
    create_group(con, "Games")
    gid = create_group(con, "Microsoft")

    with pytest.raises(DuplicateGroupError):
        rename_group(con, gid, "games")
    with pytest.raises(InvalidGroupError):
        rename_group(con, gid, " ")


def test_rename_unknown_group_is_refused(con: DBConn) -> None:
    with pytest.raises(GroupNotFoundError):
        rename_group(con, 999, "X")


# --- nesting ------------------------------------------------------------------


def test_group_tree_puts_children_under_parents(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    create_group(con, "Windows", parent_id=ms)
    create_group(con, "Office", parent_id=ms)
    create_group(con, "Games")

    tree = [(g.name, depth) for g, depth in group_tree(con)]

    assert tree == [("Games", 0), ("Microsoft", 0), ("Office", 1), ("Windows", 1)]


def test_move_group_into_another_and_back_to_top(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows")

    move_group(con, win, ms)
    assert get_group(con, win).parent_id == ms
    assert subgroup_ids(con, ms) == {win}

    move_group(con, win, None)
    assert get_group(con, win).parent_id is None


def test_move_group_into_itself_or_a_descendant_is_refused(con: DBConn) -> None:
    top = create_group(con, "Top")
    mid = create_group(con, "Mid", parent_id=top)
    low = create_group(con, "Low", parent_id=mid)

    for target in (top, mid, low):
        with pytest.raises(InvalidGroupError, match="cannot be moved into itself"):
            move_group(con, top, target)


def test_move_to_unknown_parent_is_refused(con: DBConn) -> None:
    gid = create_group(con, "G")

    with pytest.raises(GroupNotFoundError):
        move_group(con, gid, 999)


# --- membership ---------------------------------------------------------------


def test_a_product_can_be_in_many_groups(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    work = create_group(con, "Work")
    office = _pid(con, "Office 2021")

    add_product(con, ms, office)
    add_product(con, work, office)
    add_product(con, work, office)  # adding again changes nothing

    assert _names(con, ms) == ["Office 2021"]
    assert _names(con, work) == ["Office 2021"]


def test_remove_product(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")
    office = _pid(con, "Office 2021")
    add_product(con, gid, office)

    remove_product(con, gid, office)

    assert _names(con, gid) == []


def test_remove_product_not_in_group_is_refused(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")

    with pytest.raises(InvalidGroupError, match='not in the group "Microsoft"'):
        remove_product(con, gid, _pid(con, "Game"))


def test_membership_with_unknown_ids_is_refused(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")
    game = _pid(con, "Game")

    with pytest.raises(GroupNotFoundError):
        add_product(con, 999, game)
    with pytest.raises(ProductNotFoundError):
        add_product(con, gid, 999)
    with pytest.raises(ProductNotFoundError):
        remove_product(con, gid, 999)
    with pytest.raises(GroupNotFoundError):
        products_in_group(con, 999)


def test_product_names_under_includes_subgroups(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    add_product(con, ms, _pid(con, "Office 2021"))
    add_product(con, win, _pid(con, "Windows 11"))

    assert product_names_under(con, ms) == {"Office 2021", "Windows 11"}
    assert product_names_under(con, win) == {"Windows 11"}


def test_ungrouped_product_names(con: DBConn) -> None:
    gid = create_group(con, "Microsoft")
    add_product(con, gid, _pid(con, "Office 2021"))

    assert ungrouped_product_names(con) == {"Windows 11", "Game"}


def test_deleting_last_licence_of_a_grouped_product_drops_its_membership(
    con: DBConn,
) -> None:
    gid = create_group(con, "Games")
    add_product(con, gid, _pid(con, "Game"))
    game = next(lic for lic in list_licences(con) if lic.product_name == "Game")

    delete_licence(con, game.id)  # also deletes the now-unused product

    assert _names(con, gid) == []
    assert con.execute("SELECT count(*) FROM product_group_member").fetchone() == (0,)


# --- delete -------------------------------------------------------------------


def test_delete_nested_group_moves_products_and_subgroups_up(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    old = create_group(con, "Old Windows", parent_id=win)
    add_product(con, win, _pid(con, "Windows 11"))
    licences_before = list_licences(con)

    delete_group(con, win)

    assert [g.name for g in list_groups(con)] == ["Microsoft", "Old Windows"]
    assert get_group(con, old).parent_id == ms
    assert _names(con, ms) == ["Windows 11"]
    assert list_licences(con) == licences_before


def test_delete_nested_group_can_ungroup_products_instead(con: DBConn) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    other = create_group(con, "Work")
    windows = _pid(con, "Windows 11")
    add_product(con, win, windows)
    add_product(con, other, windows)

    delete_group(con, win, ungroup_products=True)

    assert _names(con, ms) == []
    assert _names(con, other) == ["Windows 11"]  # other groups are kept
    assert "Windows 11" not in ungrouped_product_names(con)


def test_delete_top_level_group_ungroups_and_keeps_everything_else(
    con: DBConn,
) -> None:
    ms = create_group(con, "Microsoft")
    win = create_group(con, "Windows", parent_id=ms)
    add_product(con, ms, _pid(con, "Office 2021"))
    licences_before = list_licences(con)

    delete_group(con, ms)

    assert get_group(con, win).parent_id is None
    assert "Office 2021" in ungrouped_product_names(con)
    assert [p.name for p in list_products(con)] == ["Game", "Office 2021", "Windows 11"]
    assert list_licences(con) == licences_before


def test_delete_unknown_group_is_refused(con: DBConn) -> None:
    with pytest.raises(GroupNotFoundError):
        delete_group(con, 999)
