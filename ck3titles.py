"""Landed-title catalog construction shared by CK3 update steps."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ck3parser import Block, Document, nearest_title_parent, read_document


class TitleError(RuntimeError):
    pass


@dataclass
class TitleEntry:
    name: str
    block: Block
    relative_path: Path
    parent: str | None
    children: list[str] = field(default_factory=list)
    province: int | None = None


@dataclass
class TitleCatalog:
    entries: dict[str, TitleEntry]
    documents: dict[Path, Document]

    def descendants(self, names: Iterable[str]) -> set[str]:
        result, queue = set(), list(names)
        while queue:
            name = queue.pop()
            if name not in result:
                result.add(name)
                queue.extend(self.entries[name].children)
        return result

    def province_ids(self, names: Iterable[str]) -> set[int]:
        return {entry.province for name in names if (entry := self.entries[name]).province is not None}

    def ancestor_selected(self, name: str, selected: set[str]) -> bool:
        parent = self.entries[name].parent
        while parent:
            if parent in selected:
                return True
            parent = self.entries[parent].parent
        return False


def load_title_catalog(game: Path) -> tuple[TitleCatalog, list[str]]:
    base = game / "common" / "landed_titles"
    if not base.is_dir():
        raise TitleError(f"Missing landed title directory: {base}")
    documents: dict[Path, Document] = {}
    entries: dict[str, TitleEntry] = {}
    duplicates: set[str] = set()
    for path in sorted(base.glob("*.txt")):
        relative, document = path.relative_to(game), read_document(path)
        documents[relative] = document
        for block in document.title_blocks():
            assert block.key is not None
            parent = nearest_title_parent(block)
            value = document.direct_value(block, "province")
            entry = TitleEntry(block.key, block, relative, parent.key if parent else None, province=int(value) if value and value.isdigit() else None)
            if block.key in entries:
                duplicates.add(block.key)
            else:
                entries[block.key] = entry
    for entry in entries.values():
        if entry.parent in entries:
            entries[entry.parent].children.append(entry.name)
    return TitleCatalog(entries, documents), sorted(duplicates)
