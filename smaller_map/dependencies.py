"""Typed map/trade lists, complete road paths, startup scopes and their audits."""

from collections import Counter
from pathlib import Path
import re
from .script import apply_edits, outermost_blocks, parse, read_document

TITLE_LISTS = {"hegemonies", "empires", "kingdoms", "duchies", "counties"}


def generate_map_dependencies(game: Path, deleted_titles: set[str], deleted_provinces: set[int]):
    """Filter typed lists; retain region keys so vanilla scripts can use empty regions."""
    paths = sorted((game / "map_data/geographical_regions").glob("*.txt"))
    paths += sorted((game / "common/trade/county_potential_trade_goods").glob("*.txt"))
    paths += [game / "map_data/island_region.txt", game / "map_data/climate.txt"]
    output = {}
    counts = Counter()
    for path in paths:
        if not path.is_file():
            continue
        document = read_document(path)
        edits = []
        for block in document.blocks():
            is_climate = path.name == "climate.txt" and block.parent is None
            if block.key not in TITLE_LISTS | {"provinces"} and not is_climate:
                continue
            for token in document.list_tokens(block):
                removed = (token.text in deleted_titles if block.key in TITLE_LISTS else
                           token.text.isdigit() and int(token.text) in deleted_provinces)
                if removed:
                    # Token-only removal preserves inline lists, comments and other values.
                    edits.append((token.start, token.end, ""))
        if edits:
            source = apply_edits(document.source, edits)
            parse(source)
            relative = path.relative_to(game)
            output[relative] = source
            counts[relative.as_posix()] = len(edits)
    return output, counts


def generate_roads(game: Path, deleted_provinces: set[int]):
    """Keep complete original paths; never bridge across a deleted intermediate node."""
    output, removed = {}, set()
    for path in sorted((game / "common/roads/road_types").glob("*.txt")):
        document = read_document(path)
        blocks = [road for road in document.roots
                  if any(token.text.isdigit() and int(token.text) in deleted_provinces
                         for block in road.children if block.key == "provinces"
                         for token in document.list_tokens(block))]
        if blocks:
            removed.update(block.key for block in blocks)
            source = apply_edits(document.source, [(*document.removal_range(b), "") for b in blocks])
            parse(source)
            output[path.relative_to(game)] = source
    return output, removed


def generate_startup(game: Path, deleted_titles: set[str], deleted_provinces: set[int]):
    path = game / "common/on_action/game_start.txt"
    if not path.is_file():
        return {}, 0
    document = read_document(path)
    blocks = []
    for block in document.blocks():
        key = block.key or ""
        if (key.startswith("title:") and key[6:].split(".")[0] in deleted_titles
                or key.startswith("province:") and key[9:].isdigit()
                and int(key[9:]) in deleted_provinces
                or key in {"gdp_historical_company_setup_effect", "gdp_historical_company_expertise_effect"}
                and document.direct_value(block, "TITLE") in deleted_titles):
            blocks.append(block)
    roots = outermost_blocks(blocks)
    if not roots:
        return {}, 0
    source = apply_edits(document.source, [(*document.removal_range(b), "") for b in roots])
    parse(source)
    return {path.relative_to(game): source}, len(roots)


def audit_map_dependencies(game: Path, generated: dict[Path, str], deleted_titles: set[str],
                           deleted_provinces: set[int], removed_roads: set[str]) -> list[str]:
    """Inspect effective vanilla plus overrides, including unaffected files."""
    problems = []
    paths = set((game / "map_data/geographical_regions").glob("*.txt"))
    paths.update((game / "common/trade/county_potential_trade_goods").glob("*.txt"))
    paths.update((game / "common/roads/road_types").glob("*.txt"))
    paths.update((game / "history/wars").glob("*.txt"))
    paths.update(game / name for name in ("map_data/island_region.txt", "map_data/climate.txt",
                                        "common/on_action/game_start.txt"))
    for path in sorted(paths):
        if not path.is_file():
            continue
        relative = path.relative_to(game)
        document = parse(generated[relative]) if relative in generated else read_document(path)
        for block in document.blocks():
            is_climate = path.name == "climate.txt" and block.parent is None
            if block.key in TITLE_LISTS | {"targeted_titles", "provinces"} or is_climate:
                for token in document.list_tokens(block):
                    if (token.text in deleted_titles or token.text.isdigit()
                            and (block.key == "provinces" or is_climate)
                            and int(token.text) in deleted_provinces):
                        problems.append(f"{relative}:{token.line}: removed list member {token.text}")
        for token in document.tokens:
            if (token.text.startswith("title:") and token.text[6:].split(".")[0] in deleted_titles
                    or token.text.startswith("road:") and token.text[5:].split(".")[0] in removed_roads):
                problems.append(f"{relative}:{token.line}: removed scope {token.text}")
    return problems


def generate_adjacencies(game: Path, deleted_provinces: set[int]) -> tuple[dict[Path, str], int]:
    path = game / "map_data" / "adjacencies.csv"
    if not path.is_file():
        return {}, 0
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines(keepends=True)
    kept: list[str] = []
    removed = 0
    for line in lines:
        fields = line.rstrip("\r\n").split(";")
        ids = {
            int(value) for value in fields[:4]
            if re.fullmatch(r"\d+", value.strip()) and int(value) > 0
        }
        if ids & deleted_provinces:
            removed += 1
        else:
            kept.append(line)
    if not removed:
        return {}, 0
    return {path.relative_to(game): "".join(kept)}, removed
