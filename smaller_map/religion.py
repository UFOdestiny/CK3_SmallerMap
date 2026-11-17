"""Holy-site removal, faith-site repair and effective religion audits."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Mapping

from .common import UpdateError
from .script import Block, apply_edits, parse, read_document


def generate_holy_sites(
    game: Path,
    deleted_titles: set[str],
) -> tuple[dict[Path, str], set[str], set[str]]:
    holy_dir = game / "common" / "religion" / "holy_site_types"
    faith_dir = game / "common" / "religion" / "faith_types"
    if not holy_dir.is_dir():
        raise UpdateError(f"Missing CK3 holy-site directory: {holy_dir}")
    if not faith_dir.is_dir():
        raise UpdateError(f"Missing CK3 faith directory: {faith_dir}")
    output: dict[Path, str] = {}
    removed_sites: set[str] = set()
    all_sites: set[str] = set()
    for path in sorted(holy_dir.glob("*.txt")):
        document = read_document(path)
        removable: list[Block] = []
        for block in document.roots:
            if not block.key:
                continue
            all_sites.add(block.key)
            locations = document.direct_values(block, "county") | document.direct_values(block, "barony")
            if locations & deleted_titles:
                removed_sites.add(block.key)
                removable.append(block)
        if removable:
            source = apply_edits(document.source, [(*document.removal_range(block), "") for block in removable])
            parse(source)
            output[path.relative_to(game)] = source
    return output, removed_sites, all_sites


def site_removal_range(source: str, start: int, end: int) -> tuple[int, int]:
    line_start = source.rfind("\n", 0, start) + 1
    line_break = source.find("\n", end)
    line_end = len(source) if line_break < 0 else line_break + 1
    if not source[line_start:start].strip() and (
        not source[end:line_end].strip() or source[end:line_end].lstrip().startswith("#")
    ):
        return line_start, line_end
    return start, end


def generate_faiths(
    game: Path,
    removed_sites: set[str],
    valid_sites: set[str],
    holy_site_replacements: Mapping[str, str],
    faith_fallbacks: Mapping[str, str],
) -> tuple[dict[Path, str], Counter, Counter]:
    faith_dir = game / "common/religion/faith_types"
    invalid = (set(holy_site_replacements.values()) | set(faith_fallbacks.values())) - valid_sites
    if invalid:
        raise UpdateError("Holy-site replacement points outside the retained map: " + ", ".join(sorted(invalid)))
    output: dict[Path, str] = {}
    replaced: Counter = Counter()
    removed: Counter = Counter()
    listed_sites = 0
    for path in sorted(faith_dir.glob("*.txt")):
        document = read_document(path)
        edits: list[tuple[int, int, str]] = []
        for faith in document.roots:
            site_blocks = [block for block in faith.children if block.key in {"holy_sites", "eminent_holy_sites"}]
            surviving = [
                holy_site_replacements.get(token.text, token.text)
                for block in site_blocks for token in document.list_tokens(block)
                if token.text not in removed_sites or token.text in holy_site_replacements
            ]
            if not surviving and faith.key in faith_fallbacks:
                ordinary = next((block for block in site_blocks if block.key == "holy_sites"), None)
                if ordinary is None:
                    raise UpdateError(f"Faith {faith.key} has no ordinary holy-sites block")
                position = document.source.rfind("\n", 0, ordinary.close_token.start) + 1
                edits.append((position, position, f"\t\t{faith_fallbacks[faith.key]}\n"))
        for block in document.blocks():
            if block.key not in {"holy_sites", "eminent_holy_sites"}:
                continue
            tokens = document.list_tokens(block)
            listed_sites += len(tokens)
            for token in tokens:
                if token.text not in removed_sites:
                    continue
                replacement = holy_site_replacements.get(token.text, "")
                if replacement:
                    edits.append((token.start, token.end, replacement))
                    replaced[token.text] += 1
                else:
                    start, end = site_removal_range(document.source, token.start, token.end)
                    edits.append((start, end, ""))
                    removed[token.text] += 1
        if edits:
            source = apply_edits(document.source, edits)
            parse(source)
            output[path.relative_to(game)] = source
    if not listed_sites or (removed_sites and not replaced and not removed):
        raise UpdateError(
            "No affected faith holy-site references were found; CK3 may have changed its faith format"
        )
    return output, replaced, removed


def audit_religion_main_sites(game: Path, valid_sites: set[str]) -> None:
    base = game / "common" / "religion" / "religion_types"
    if not base.is_dir():
        raise UpdateError(f"Missing CK3 religion directory: {base}")
    for path in sorted(base.glob("*.txt")):
        document = read_document(path)
        for block in document.blocks():
            for assignment in document.direct_assignments(block):
                if assignment.key == "main_holy_site" and assignment.value not in valid_sites:
                    raise UpdateError(
                        f"Religion main_holy_site references removed/unknown site "
                        f"{assignment.value} in {path}:{assignment.key_token.line}"
                    )


def audit_faiths(
    game: Path,
    generated: dict[Path, str],
    valid_sites: set[str],
) -> tuple[list[str], list[str], list[str]]:
    faith_dir = game / "common/religion/faith_types"
    duplicates: list[str] = []
    sparse: list[str] = []
    undefined: list[str] = []
    for path in sorted(faith_dir.glob("*.txt")):
        relative = path.relative_to(game)
        effective = parse(generated[relative]) if relative in generated else read_document(path)
        for faith in effective.roots:
            values = [token.text for block in faith.children
                      if block.key in {"holy_sites", "eminent_holy_sites"}
                      for token in effective.list_tokens(block)]
            label = f"{relative}:{faith.key}"
            if len(values) != len(set(values)):
                duplicates.append(label)
            if len(values) < 3:
                sparse.append(f"{label} has {len(values)} holy sites")
            if not values:
                undefined.append(f"{label} has no holy sites")
            for value in values:
                if value not in valid_sites:
                    undefined.append(f"{label} references undefined holy site {value}")
    return duplicates, sparse, undefined
