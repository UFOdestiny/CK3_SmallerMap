"""Bookmark, title/province history and historical-war transforms."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from .script import apply_edits, outermost_blocks, parse, read_document, scalar_assignment_edits


def generate_bookmarks(game: Path, deleted_titles: set[str]) -> tuple[dict[Path, str], int]:
    base = game / "common" / "bookmarks" / "bookmarks"
    output: dict[Path, str] = {}
    count = 0
    if not base.is_dir():
        return output, count
    for path in sorted(base.glob("*.txt")):
        document = read_document(path)
        blocks = [
            block for block in document.blocks()
            if block.key == "character" and document.direct_values(block, "title") & deleted_titles
        ]
        roots = outermost_blocks(blocks)
        if roots:
            count += len(roots)
            source = apply_edits(document.source, [(*document.removal_range(block), "") for block in roots])
            parse(source)
            output[path.relative_to(game)] = source
    return output, count


def generate_title_history(
    game: Path,
    deleted_titles: set[str],
) -> tuple[dict[Path, str], int, int]:
    base = game / "history" / "titles"
    output: dict[Path, str] = {}
    removed = 0
    cleared_lieges = 0
    if not base.is_dir():
        return output, removed, cleared_lieges
    deleted_replacements = {title: "0" for title in deleted_titles}
    for path in sorted(base.glob("*.txt")):
        document = read_document(path)
        blocks = [block for block in document.roots if block.key in deleted_titles]
        surviving_ranges = [
            (block.open_token.end, block.close_token.start)
            for block in document.roots
            if block.key not in deleted_titles and block.close_token is not None
        ]
        reference_edits: list[tuple[int, int, str]] = []
        for key in ("liege", "de_jure_liege"):
            reference_edits.extend(
                scalar_assignment_edits(
                    document,
                    key,
                    deleted_replacements,
                    surviving_ranges,
                )
            )
        if blocks or reference_edits:
            removed += len(blocks)
            cleared_lieges += len(reference_edits)
            edits = [(*document.removal_range(block), "") for block in blocks]
            edits.extend(reference_edits)
            source = apply_edits(document.source, edits)
            parse(source)
            output[path.relative_to(game)] = source
    return output, removed, cleared_lieges


def effective_title_history_audit(
    game: Path,
    generated: dict[Path, str],
    deleted_titles: set[str],
) -> list[str]:
    problems: list[str] = []
    base = game / "history" / "titles"
    if not base.is_dir():
        return problems
    for path in sorted(base.glob("*.txt")):
        relative = path.relative_to(game)
        document = parse(generated[relative]) if relative in generated else read_document(path)
        for root in document.roots:
            if root.key in deleted_titles:
                problems.append(f"{relative}:{root.key}: deleted title history remains")
                continue
            stack = [root]
            while stack:
                block = stack.pop()
                stack.extend(block.children)
                for assignment in document.direct_assignments(block):
                    if (
                        assignment.key in {"liege", "de_jure_liege"}
                        and assignment.value in deleted_titles
                    ):
                        problems.append(
                            f"{relative}:{assignment.key_token.line}: {root.key} "
                            f"{assignment.key} references deleted {assignment.value}"
                        )
    return problems


def generate_province_history(game: Path, deleted_provinces: set[int]) -> tuple[dict[Path, str], int]:
    base = game / "history" / "provinces"
    output: dict[Path, str] = {}
    removed = 0
    if not base.is_dir():
        return output, removed
    for path in sorted(base.glob("*.txt")):
        document = read_document(path)
        blocks = [
            block for block in document.roots
            if block.key and block.key.isdigit() and int(block.key) in deleted_provinces
        ]
        if blocks:
            removed += len(blocks)
            source = apply_edits(document.source, [(*document.removal_range(block), "") for block in blocks])
            parse(source)
            output[path.relative_to(game)] = source
    return output, removed


def generate_wars(game: Path, deleted_titles: set[str], deleted_provinces: set[int]):
    output, counts = {}, Counter()
    for path in sorted((game / "history/wars").glob("*.txt")):
        document = read_document(path)
        edits = []
        for war in document.roots:
            targets = [t for block in war.children if block.key == "targeted_titles"
                       for t in document.list_tokens(block)]
            if targets and all(t.text in deleted_titles for t in targets):
                edits.append((*document.removal_range(war), ""))
                counts["wars"] += 1
                continue
            for token in targets:
                if token.text in deleted_titles:
                    edits.append((token.start, token.end, ""))
                    counts["targets"] += 1
            for block in war.walk():
                for assignment in document.direct_assignments(block):
                    if (assignment.key == "location" and assignment.value.isdigit()
                            and int(assignment.value) in deleted_provinces):
                        edits.append((assignment.key_token.start, assignment.value_token.end, ""))
                        counts["locations"] += 1
        if edits:
            source = apply_edits(document.source, edits)
            parse(source)
            output[path.relative_to(game)] = source
    return output, counts
