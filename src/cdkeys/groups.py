"""
Product groups (#653): named tags on products, which can nest.

A product can be in any number of groups; a group can sit inside another.
Grouping never touches licences or their ids. No UI code here; callers own
the connection and commit or roll back, as in cdkeys.store.
"""

from __future__ import annotations

from dataclasses import dataclass

from cdkeys.db import DBConn
from cdkeys.licenses import canon, norm


class GroupError(Exception):
    """Base class for group operations that cannot be carried out."""


class GroupNotFoundError(GroupError):
    pass


class ProductNotFoundError(GroupError):
    pass


class InvalidGroupError(GroupError):
    pass


class DuplicateGroupError(GroupError):
    pass


@dataclass(frozen=True)
class Group:
    id: int
    name: str
    parent_id: int | None


@dataclass(frozen=True)
class Product:
    id: int
    name: str


def _by_name[T: (Group, Product)](items: list[T]) -> list[T]:
    return sorted(items, key=lambda item: (canon(item.name) or "", item.id))


def list_groups(con: DBConn) -> list[Group]:
    """All groups, sorted by name."""
    rows = con.execute("SELECT id, name, parent_id FROM product_group").fetchall()
    return _by_name([Group(*row) for row in rows])


def group_tree(con: DBConn) -> list[tuple[Group, int]]:
    """All groups with their depth, parents before children, each level by name."""
    groups = list_groups(con)
    children: dict[int | None, list[Group]] = {}
    for group in groups:
        children.setdefault(group.parent_id, []).append(group)
    ordered: list[tuple[Group, int]] = []

    def walk(parent_id: int | None, depth: int) -> None:
        for group in children.get(parent_id, []):
            ordered.append((group, depth))
            walk(group.id, depth + 1)

    walk(None, 0)
    return ordered


def get_group(con: DBConn, group_id: int) -> Group:
    row = con.execute(
        "SELECT id, name, parent_id FROM product_group WHERE id = ?", (group_id,)
    ).fetchone()
    if row is None:
        raise GroupNotFoundError(f"No group with id {group_id}")
    return Group(*row)


def _checked_name(con: DBConn, name: str, exclude_id: int | None = None) -> str:
    """The trimmed name, refused if empty or already used (ignoring case)."""
    cleaned = norm(name)
    if cleaned is None:
        raise InvalidGroupError("Enter a name for the group.")
    for group in list_groups(con):
        if group.id != exclude_id and canon(group.name) == canon(cleaned):
            raise DuplicateGroupError(f'A group named "{group.name}" already exists.')
    return cleaned


def create_group(con: DBConn, name: str, parent_id: int | None = None) -> int:
    """Create a group, at the top level or inside ``parent_id``; returns its id."""
    if parent_id is not None:
        get_group(con, parent_id)
    cleaned = _checked_name(con, name)
    con.execute(
        "INSERT INTO product_group (name, parent_id) VALUES (?, ?)",
        (cleaned, parent_id),
    )
    row = con.execute(
        "SELECT id FROM product_group WHERE name = ?", (cleaned,)
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to load group after insert.")
    return int(row[0])


def rename_group(con: DBConn, group_id: int, name: str) -> None:
    get_group(con, group_id)
    cleaned = _checked_name(con, name, exclude_id=group_id)
    con.execute("UPDATE product_group SET name = ? WHERE id = ?", (cleaned, group_id))


def subgroup_ids(con: DBConn, group_id: int) -> set[int]:
    """Ids of every group below ``group_id`` (children, grandchildren, ...)."""
    rows = con.execute(
        """
        WITH RECURSIVE below(id) AS (
            SELECT id FROM product_group WHERE parent_id = ?
            UNION
            SELECT g.id FROM product_group g JOIN below b ON g.parent_id = b.id
        )
        SELECT id FROM below
        """,
        (group_id,),
    ).fetchall()
    return {int(row[0]) for row in rows}


def move_group(con: DBConn, group_id: int, parent_id: int | None) -> None:
    """Put a group inside ``parent_id``, or at the top level when None."""
    get_group(con, group_id)
    if parent_id is not None:
        get_group(con, parent_id)
        if parent_id == group_id or parent_id in subgroup_ids(con, group_id):
            raise InvalidGroupError(
                "A group cannot be moved into itself or one of its subgroups."
            )
    con.execute(
        "UPDATE product_group SET parent_id = ? WHERE id = ?", (parent_id, group_id)
    )


def delete_group(con: DBConn, group_id: int, *, ungroup_products: bool = False) -> None:
    """Delete a group. Products and licences are never deleted.

    Its subgroups move up into its parent (or to the top level). Its products
    move up into its parent too, so removing a nested group keeps them in the
    branch; with ``ungroup_products`` they are dropped from the branch
    instead. Either way products keep their other groups. For a top-level
    group both choices are the same: its products lose this group.
    """
    group = get_group(con, group_id)
    if group.parent_id is not None and not ungroup_products:
        con.execute(
            """
            INSERT OR IGNORE INTO product_group_member (group_id, product_id)
            SELECT ?, product_id FROM product_group_member WHERE group_id = ?
            """,
            (group.parent_id, group_id),
        )
    con.execute(
        "UPDATE product_group SET parent_id = ? WHERE parent_id = ?",
        (group.parent_id, group_id),
    )
    # Explicit rather than relying on ON DELETE CASCADE, which only works on
    # connections with foreign_keys enabled.
    con.execute("DELETE FROM product_group_member WHERE group_id = ?", (group_id,))
    con.execute("DELETE FROM product_group WHERE id = ?", (group_id,))


def list_products(con: DBConn) -> list[Product]:
    """All products, sorted by name."""
    rows = con.execute("SELECT id, name FROM product").fetchall()
    return _by_name([Product(*row) for row in rows])


def _get_product(con: DBConn, product_id: int) -> Product:
    row = con.execute(
        "SELECT id, name FROM product WHERE id = ?", (product_id,)
    ).fetchone()
    if row is None:
        raise ProductNotFoundError(f"No product with id {product_id}")
    return Product(*row)


def products_in_group(con: DBConn, group_id: int) -> list[Product]:
    """The products tagged with this group itself (not its subgroups)."""
    get_group(con, group_id)
    rows = con.execute(
        """
        SELECT p.id, p.name FROM product p
        JOIN product_group_member m ON m.product_id = p.id
        WHERE m.group_id = ?
        """,
        (group_id,),
    ).fetchall()
    return _by_name([Product(*row) for row in rows])


def add_product(con: DBConn, group_id: int, product_id: int) -> None:
    """Tag a product with a group; adding it again changes nothing."""
    get_group(con, group_id)
    _get_product(con, product_id)
    con.execute(
        "INSERT OR IGNORE INTO product_group_member (group_id, product_id) "
        "VALUES (?, ?)",
        (group_id, product_id),
    )


def remove_product(con: DBConn, group_id: int, product_id: int) -> None:
    group = get_group(con, group_id)
    product = _get_product(con, product_id)
    found = con.execute(
        "SELECT 1 FROM product_group_member WHERE group_id = ? AND product_id = ?",
        (group_id, product_id),
    ).fetchone()
    if found is None:
        raise InvalidGroupError(f'{product.name} is not in the group "{group.name}".')
    con.execute(
        "DELETE FROM product_group_member WHERE group_id = ? AND product_id = ?",
        (group_id, product_id),
    )


def product_names_under(con: DBConn, group_id: int) -> set[str]:
    """Names of the products in this group or any of its subgroups."""
    ids = {group_id, *subgroup_ids(con, group_id)}
    names: set[str] = set()
    for gid in ids:
        names.update(p.name for p in products_in_group(con, gid))
    return names


def ungrouped_product_names(con: DBConn) -> set[str]:
    rows = con.execute(
        """
        SELECT name FROM product
        WHERE id NOT IN (SELECT product_id FROM product_group_member)
        """
    ).fetchall()
    return {str(row[0]) for row in rows}
