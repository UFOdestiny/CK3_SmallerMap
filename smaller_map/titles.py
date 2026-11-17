"""Title hierarchy, dependent merchant selection, generation and integrity audits."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .common import UpdateError
from .script import Block, Document, apply_edits, nearest_title_parent, parse, read_document


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
        names = set(names)
        missing = names - self.entries.keys()
        if missing:
            raise UpdateError("Unknown selected title(s): " + ", ".join(sorted(missing)))
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
        raise UpdateError(f"Missing landed title directory: {base}")
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


def dependent_merchant_titles(catalog: TitleCatalog, selected: set[str]) -> set[str]:
    """A landless company needs a surviving home county, not an invented capital."""
    deleted = catalog.descendants(selected)
    additional: set[str] = set()
    while True:
        found = {
            name for name, entry in catalog.entries.items()
            if name.startswith("c_merchant_") and name not in deleted
            and catalog.documents[entry.relative_path].direct_value(entry.block, "capital") in deleted
        }
        if not found:
            return additional
        additional.update(found)
        deleted.update(catalog.descendants(found))


def generate_landed_titles(
    catalog: TitleCatalog,
    selected: set[str],
    capital_replacements: Mapping[str, str],
) -> tuple[dict[Path, str], set[str], set[int], Counter]:
    deleted_titles = catalog.descendants(selected)
    deleted_provinces = catalog.province_ids(deleted_titles)
    roots = {name for name in selected if not catalog.ancestor_selected(name, selected)}
    by_file: dict[Path, list[Block]] = defaultdict(list)
    for name in roots:
        entry = catalog.entries[name]
        by_file[entry.relative_path].append(entry.block)
    output: dict[Path, str] = {}
    replacement_counts: Counter = Counter()
    for relative, document in catalog.documents.items():
        edits = [(*document.removal_range(block), "") for block in by_file.get(relative, [])]
        for block in document.title_blocks():
            if block.key in deleted_titles:
                continue
            for assignment in document.direct_assignments(block):
                if assignment.key == "capital" and assignment.value in capital_replacements:
                    edits.append((assignment.value_token.start, assignment.value_token.end,
                                  capital_replacements[assignment.value]))
                    replacement_counts[assignment.value] += 1
        source = apply_edits(document.source, edits)
        if source != document.source:
            generated_document = parse(source)  # fail before writing an unbalanced file
            # An override must still exist when every title in a vanilla file is
            # removed. Leftover @variables are not valid standalone landed titles:
            # CK3 interprets their values as title names and rejects the file.
            if next(generated_document.title_blocks(), None) is None:
                source = "# All landed titles removed by CK3 Smaller Map.\n"
            else:
                source = source.rstrip("\r\n") + "\n"
            output[relative] = source
    return output, deleted_titles, deleted_provinces, replacement_counts


def effective_landed_audit(
    catalog: TitleCatalog,
    generated: dict[Path, str],
    deleted_titles: set[str],
) -> list[str]:
    problems: list[str] = []
    effective: dict[str, tuple[str | None, int | None]] = {}
    effective_counts: Counter = Counter()
    original_counts: Counter = Counter(
        block.key
        for document in catalog.documents.values()
        for block in document.title_blocks()
    )
    for relative, original in catalog.documents.items():
        source = generated.get(relative, original.source)
        document = parse(source)
        blocks = list(document.title_blocks())
        if relative in generated and not blocks and document.tokens:
            problems.append(f"{relative}: title-less override contains executable text")
        for block in blocks:
            assert block.key is not None
            effective_counts[block.key] += 1
            parent_block = nearest_title_parent(block)
            province_text = document.direct_value(block, "province")
            province = int(province_text) if province_text and province_text.isdigit() else None
            effective.setdefault(
                block.key,
                (parent_block.key if parent_block else None, province),
            )
            for assignment in document.direct_assignments(block):
                if assignment.key in {"capital", "de_jure_liege"} and assignment.value in deleted_titles:
                    problems.append(
                        f"{relative}:{assignment.key_token.line}: {block.key} {assignment.key} references deleted {assignment.value}"
                    )
    for name, entry in catalog.entries.items():
        if name in deleted_titles:
            continue
        actual = effective.get(name)
        expected = (entry.parent, entry.province)
        if actual is None:
            problems.append(f"{entry.relative_path}: surviving title {name} is missing")
        elif actual != expected:
            problems.append(
                f"{entry.relative_path}: {name} expected parent/province {expected}, got {actual}"
            )
    for name in deleted_titles & effective_counts.keys():
        problems.append(f"Deleted title remains in landed titles: {name}")
    for name, count in effective_counts.items():
        if count > original_counts[name]:
            problems.append(f"Title {name} duplicated during generation: {count}/{original_counts[name]}")
    return problems
