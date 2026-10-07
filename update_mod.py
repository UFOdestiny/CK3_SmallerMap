"""Regenerate CK3 Smaller Map from the currently installed game data.

Edit the settings below, then run this file. The generator keeps the game
files' original formatting, removes exact AST blocks, updates holy sites/history/
bookmarks, and paints every deleted barony province into a nearby impassable province.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import csv
import json
from pathlib import Path
import re
import sys
from typing import Iterable

import numpy as np
from PIL import Image

from ck3parser import (
    Block,
    Document,
    apply_edits,
    nearest_title_parent,
    parse,
    read_document,
    scalar_assignment_edits,
)
from ck3titles import TitleCatalog, TitleError, load_title_catalog


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------

GAME_PATH = Path(r"E:\SteamLibrary\steamapps\common\Crusader Kings III\game")
REPO_PATH = Path(__file__).resolve().parent
MOD_PATH = REPO_PATH / "3488444772"

title_delete = [
    "e_azania",
    "e_deccan",
    "e_guinea",
    "e_mali",
    "e_kanem_bornu",
    "e_abyssinia",
    "e_siberia",
    "e_srivijaya",
    "e_brunei",
    "e_majapahit",
    "k_lusung",
    "k_kabisay-an",
    "k_tanjungnagara",
    "k_sulawesi",
    "k_maluku",
    "k_permia",
    "k_sahara",
    "k_angara",
    "k_anbiya",
    "k_bjarmaland",
    "k_buryatia",
    "k_khakassia",
    "k_orissa",
    "d_bahitawi",
    "d_laamp_shunkan",
    "d_laamp_sirafi_mariners",
    "d_laamp_leaf_hermits",
    "d_laamp_lembongs_band",
]

# Paint deleted Southeast Asian land, including untitled mountains and lakes,
# with the same RGB (10, 13, 16) receiver used for Kalimantan.
province_receiver_overrides = dict.fromkeys(
    ("e_srivijaya", "e_brunei", "k_lusung", "k_kabisay-an",
     "k_tanjungnagara", "k_sulawesi", "k_maluku"),
    11215,
)

replace_title = {
    "capital = c_PHI_tondo": "capital = c_hoanya",
    "capital = c_semien": "capital = c_aswan",
    "capital = c_tigre": "capital = c_aswan",
    "capital = c_jenne": "capital = c_tinmallal",
    "capital = c_kongu": "capital = c_bost",
    "capital = c_attie": "capital = c_tinmallal",
    "capital = c_toro": "capital = c_tinmallal",
}

rlps = {
    "bono": "aswan",
    "danakil": "carthage",
    "awkar": "aswan",
    "daura": "aswan",
    "ife": "tinmallal",
    "kisi": "tinmallal",
}

# Faiths whose entire original holy-site region is outside the reduced map.
# Keep at least one valid site so CK3 1.20's holy_sites_min = 1 remains satisfied.
faith_fallbacks = {
    "aluk": "hoanya",
    "dayawism": "hoanya",
    "kaharingan": "hoanya",
    "tolotang": "hoanya",
    "siberian_pagan": "novgorod",
    "ngaiism_pagan": "aswan",
}


MANIFEST_NAME = ".smaller_map_generated.json"
VERSION_RE = re.compile(r"(?:^|/)release/(\d+)\.(\d+)(?:\.\d+)?\s*$")


class UpdateError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def replace_configured_text(source: str, replacements: dict[str, str], counts: Counter) -> str:
    for old, new in replacements.items():
        found = source.count(old)
        if found:
            source = source.replace(old, new)
            counts[old] += found
    return source


def generate_landed_titles(
    catalog: TitleCatalog,
    selected: set[str],
) -> tuple[dict[Path, str], set[str], set[int], Counter]:
    missing = sorted(selected - catalog.entries.keys())
    if missing:
        raise UpdateError(
            "Configured title(s) no longer exist in this game version: " + ", ".join(missing)
            + ". Update title_delete for the installed game version."
        )
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
        source = apply_edits(document.source, edits)
        source = replace_configured_text(source, replace_title, replacement_counts)
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


def direct_title_set(document: Document, block: Block, key: str) -> set[str]:
    return {item.value for item in document.direct_assignments(block) if item.key == key}


def generate_holy_sites(
    game: Path,
    deleted_titles: set[str],
) -> tuple[dict[Path, str], set[str], set[str], Path]:
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
            locations = direct_title_set(document, block, "county") | direct_title_set(document, block, "barony")
            if locations & deleted_titles:
                removed_sites.add(block.key)
                removable.append(block)
        if removable:
            source = apply_edits(document.source, [(*document.removal_range(block), "") for block in removable])
            parse(source)
            output[path.relative_to(game)] = source
    bad_targets = sorted(set(rlps.values()) - (all_sites - removed_sites))
    if bad_targets:
        raise UpdateError("rlps points to missing/deleted holy site(s): " + ", ".join(bad_targets))
    return output, removed_sites, all_sites, faith_dir


def faith_site_tokens(document: Document, block: Block):
    return [
        token for token in document.tokens
        if block.open_token.end <= token.start < block.close_token.start
        and token.depth == block.inner_depth and token.text not in {"{", "}", "="}
    ]


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
    faith_dir: Path,
    removed_sites: set[str],
    valid_sites: set[str],
) -> tuple[dict[Path, str], Counter, Counter]:
    invalid = (set(rlps.values()) | set(faith_fallbacks.values())) - valid_sites
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
                rlps.get(token.text, token.text)
                for block in site_blocks for token in faith_site_tokens(document, block)
                if token.text not in removed_sites or token.text in rlps
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
            tokens = faith_site_tokens(document, block)
            listed_sites += len(tokens)
            for token in tokens:
                if token.text not in removed_sites:
                    continue
                replacement = rlps.get(token.text, "")
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
    faith_dir: Path,
    generated: dict[Path, str],
    valid_sites: set[str],
) -> tuple[list[str], list[str], list[str]]:
    duplicates: list[str] = []
    sparse: list[str] = []
    undefined: list[str] = []
    for path in sorted(faith_dir.glob("*.txt")):
        relative = path.relative_to(game)
        effective = parse(generated.get(relative, read_document(path).source))
        for faith in effective.roots:
            values = [token.text for block in faith.children
                      if block.key in {"holy_sites", "eminent_holy_sites"}
                      for token in faith_site_tokens(effective, block)]
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
            if block.key == "character" and direct_title_set(document, block, "title") & deleted_titles
        ]
        # If a parent character is removed, do not also edit its nested alternate character.
        selected = set(id(block) for block in blocks)
        roots = [
            block for block in blocks
            if not any(id(parent) in selected for parent in _parents(block))
        ]
        if roots:
            count += len(roots)
            source = apply_edits(document.source, [(*document.removal_range(block), "") for block in roots])
            parse(source)
            output[path.relative_to(game)] = source
    return output, count


def _parents(block: Block):
    parent = block.parent
    while parent:
        yield parent
        parent = parent.parent


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
        document = parse(generated.get(relative, read_document(path).source))
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


def read_definitions(path: Path) -> tuple[dict[int, tuple[int, int, int]], dict[int, int]]:
    ids: dict[int, tuple[int, int, int]] = {}
    color_to_id: dict[int, int] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.reader(stream, delimiter=";"):
            if len(row) < 4 or not row[0].strip().isdigit():
                continue
            province = int(row[0])
            rgb = tuple(int(item) for item in row[1:4])
            code = rgb[0] << 16 | rgb[1] << 8 | rgb[2]
            ids[province] = rgb
            # Vanilla contains duplicate black placeholders (IDs 0 and 12946).
            # The first definition owns ambiguous pixels; such later IDs cannot safely
            # be used as paint receivers and therefore stay out of color_to_id.
            color_to_id.setdefault(code, province)
    return ids, color_to_id


def parse_map_province_groups(default_map: Path) -> dict[str, set[int]]:
    source = default_map.read_text(encoding="utf-8-sig")
    source = re.sub(r"#.*", "", source)
    result: dict[str, set[int]] = defaultdict(set)
    pattern = re.compile(
        r"(sea_zones|impassable_seas|river_provinces|lakes|impassable_mountains)"
        r"\s*=\s*(LIST|RANGE)\s*\{([^}]*)\}",
        re.I,
    )
    for name, kind, body in pattern.findall(source):
        numbers = [int(item) for item in re.findall(r"\d+", body)]
        if kind.upper() == "RANGE" and len(numbers) == 2:
            values = range(numbers[0], numbers[1] + 1)
        else:
            values = numbers
        result[name.lower()].update(values)
    if not result["impassable_mountains"]:
        raise UpdateError(f"No impassable_mountains entries found in {default_map}")
    if not result["sea_zones"]:
        raise UpdateError(f"No sea_zones entries found in {default_map}")
    return result


def deleted_history_file_provinces(
    game: Path,
    deleted_titles: set[str],
) -> set[int]:
    """Include holding-none/road provinces stored in deleted kingdom history files."""
    result: set[int] = set()
    base = game / "history" / "provinces"
    if not base.is_dir():
        return result
    for path in sorted(base.glob("*.txt")):
        if path.stem not in deleted_titles:
            continue
        document = read_document(path)
        result.update(
            int(block.key)
            for block in document.roots
            if block.key and block.key.isdigit()
        )
    return result


def absorb_enclosed_special_provinces(
    deleted: set[int],
    surviving_titled: set[int],
    present: set[int],
    groups: dict[str, set[int]],
    graph: dict[int, set[int]],
    boundaries: Counter,
) -> set[int]:
    """Remove rivers/lakes/wastelands stranded inside deleted land.

    These provinces have no landed title, so a title-only map rewrite leaves colored
    slivers and islands. Only directly enclosed special provinces are absorbed; sea
    zones and anything touching a surviving titled province remain unchanged.
    """
    candidates = (
        groups["river_provinces"]
        | groups["lakes"]
        | groups["impassable_mountains"]
    ) & present - deleted
    absorbed: set[int] = set()
    # A lake can sit behind an impassable province which itself sits behind deleted
    # baronies. A few bounded passes close those one- or two-province holes without
    # allowing deletion to flood indefinitely through a long mountain chain.
    for _ in range(3):
        frontier: set[int] = set()
        deleted_now = deleted | absorbed
        for province in candidates - absorbed:
            neighbors = graph.get(province, set())
            if not neighbors or neighbors & surviving_titled:
                continue
            total_boundary = sum(boundaries[(province, neighbor)] for neighbor in neighbors)
            deleted_boundary = sum(
                boundaries[(province, neighbor)]
                for neighbor in neighbors
                if neighbor in deleted_now
            )
            if total_boundary and deleted_boundary / total_boundary >= 0.5:
                frontier.add(province)
        if not frontier:
            break
        absorbed.update(frontier)
    return absorbed


def regional_receiver_assignments(
    game: Path,
    catalog: TitleCatalog,
    deleted_titles: set[str],
    present: set[int],
    groups: dict[str, set[int]],
    graph: dict[int, set[int]],
) -> dict[int, int]:
    """Include untitled land components adjoining a configured deleted region.

    Definition-only mountains are absent from both landed titles and some
    default.map mountain lists. Exclude seas explicitly and reject components
    touching retained baronies, instead of relying on coastline boundary ratios.
    """
    titled = catalog.province_ids(catalog.entries)
    retained = catalog.province_ids(catalog.entries.keys() - deleted_titles)
    water = groups["sea_zones"] | groups["impassable_seas"]
    targets = set(province_receiver_overrides.values())
    regions: dict[int, set[int]] = defaultdict(set)
    for title, receiver in province_receiver_overrides.items():
        if title not in deleted_titles:
            raise UpdateError(f"Map receiver override refers to a surviving title: {title}")
        if receiver not in groups["impassable_mountains"] or receiver not in present:
            raise UpdateError(f"Map receiver override is not a present impassable province: {receiver}")
        if receiver in titled:
            raise UpdateError(f"Map receiver override has a landed title: {receiver}")
        descendants = catalog.descendants({title})
        regions[receiver].update(catalog.province_ids(descendants))
        regions[receiver].update(deleted_history_file_provinces(game, descendants))
    assignments = {province: receiver for receiver, region in regions.items()
                   for province in region & present}
    unseen = present - titled - water - targets - {0}
    while unseen:
        component, queue, boundary = set(), [unseen.pop()], set()
        while queue:
            province = queue.pop()
            component.add(province)
            for neighbor in graph.get(province, ()):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
                else:
                    boundary.add(neighbor)
        boundary.difference_update(component)
        if boundary & retained:
            continue
        receivers = {receiver for receiver, region in regions.items() if boundary & region}
        if len(receivers) > 1:
            raise UpdateError(f"Untitled land touches conflicting map receivers: {sorted(receivers)}")
        if receivers:
            receiver = receivers.pop()
            assignments.update(dict.fromkeys(component, receiver))
    if assignments.keys() & water:
        raise UpdateError("Regional map fill would repaint sea provinces")
    return assignments


class UnionFind:
    def __init__(self, values: Iterable[int]):
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[right] = left


def image_codes(rgb: np.ndarray) -> np.ndarray:
    wide = rgb.astype(np.uint32)
    return (wide[:, :, 0] << 16) | (wide[:, :, 1] << 8) | wide[:, :, 2]


def collect_adjacency(codes: np.ndarray, color_to_id: dict[int, int]) -> tuple[dict[int, set[int]], Counter]:
    """Build province adjacency and boundary lengths without allocating huge pair arrays."""
    graph: dict[int, set[int]] = defaultdict(set)
    boundaries: Counter = Counter()

    def add(left: np.ndarray, right: np.ndarray) -> None:
        mask = left != right
        if not np.any(mask):
            return
        a, b = left[mask].astype(np.uint64), right[mask].astype(np.uint64)
        low, high = np.minimum(a, b), np.maximum(a, b)
        packed = (low << 24) | high
        values, counts = np.unique(packed, return_counts=True)
        for packed_value, count in zip(values.tolist(), counts.tolist()):
            left_code = int(packed_value >> 24)
            right_code = int(packed_value & 0xFFFFFF)
            left_id, right_id = color_to_id.get(left_code), color_to_id.get(right_code)
            if left_id is None or right_id is None:
                continue
            graph[left_id].add(right_id)
            graph[right_id].add(left_id)
            boundaries[(left_id, right_id)] += count
            boundaries[(right_id, left_id)] += count

    chunk = 256
    for top in range(0, codes.shape[0], chunk):
        part = codes[top:min(top + chunk, codes.shape[0])]
        add(part[:, :-1], part[:, 1:])
        if part.shape[0] > 1:
            add(part[:-1, :], part[1:, :])
        if top:
            add(codes[top - 1, :], codes[top, :])
        add(part[:, -1], part[:, 0])  # CK3's world map wraps east/west.
    return graph, boundaries


def choose_receivers(
    deleted: set[int],
    impassable: set[int],
    graph: dict[int, set[int]],
    boundaries: Counter,
) -> dict[int, int]:
    union = UnionFind(deleted)
    for province in deleted:
        for neighbor in graph.get(province, ()):
            if neighbor in deleted:
                union.union(province, neighbor)
    components: dict[int, set[int]] = defaultdict(set)
    for province in deleted:
        components[union.find(province)].add(province)
    assignments: dict[int, int] = {}
    valid_impassable = impassable & graph.keys() - deleted
    if not valid_impassable:
        raise UpdateError("No impassable province from default.map is present in provinces.png")
    for component in components.values():
        candidates: Counter = Counter()
        for province in component:
            for neighbor in graph.get(province, ()):
                if neighbor in valid_impassable:
                    candidates[neighbor] += boundaries[(province, neighbor)]
        if candidates:
            receiver = candidates.most_common(1)[0][0]
        else:
            # Search the province graph, not raw pixel distance. This handles islands
            # while preferring a geographically local impassable province.
            queue = deque(component)
            seen = set(component)
            receiver = -1
            while queue and receiver < 0:
                province = queue.popleft()
                for neighbor in graph.get(province, ()):
                    if neighbor in seen:
                        continue
                    if neighbor in valid_impassable:
                        receiver = neighbor
                        break
                    seen.add(neighbor)
                    queue.append(neighbor)
            if receiver < 0:
                raise UpdateError(f"No impassable receiver reachable for deleted component {sorted(component)[:8]}")
        for province in component:
            assignments[province] = receiver
    return assignments


def generate_province_map(
    game: Path,
    deleted_provinces: set[int],
    deleted_titles: set[str],
    catalog: TitleCatalog,
) -> tuple[
    Image.Image,
    int,
    set[int],
    int,
]:
    definition_path = game / "map_data" / "definition.csv"
    provinces_path = game / "map_data" / "provinces.png"
    id_to_rgb, color_to_id = read_definitions(definition_path)
    with Image.open(provinces_path) as image:
        if image.mode != "RGB":
            raise UpdateError(f"Game provinces.png must be RGB, got {image.mode}")
        rgb = np.asarray(image).copy()
    codes = image_codes(rgb)
    present_codes = set(np.unique(codes).tolist())
    unknown = present_codes - color_to_id.keys()
    if unknown:
        raise UpdateError(f"provinces.png contains {len(unknown)} RGB color(s) absent from definition.csv")
    present_ids = {color_to_id[code] for code in present_codes}
    graph, boundaries = collect_adjacency(codes, color_to_id)
    groups = parse_map_province_groups(game / "map_data" / "default.map")
    deleted_provinces = set(deleted_provinces)
    deleted_provinces.update(deleted_history_file_provinces(game, deleted_titles))
    forced_receivers = regional_receiver_assignments(
        game, catalog, deleted_titles, present_ids, groups, graph,
    )
    regional_special = forced_receivers.keys() - deleted_provinces
    deleted_provinces.update(forced_receivers)
    titled_provinces = {
        entry.province for entry in catalog.entries.values() if entry.province is not None
    }
    surviving_titled = titled_provinces - deleted_provinces
    absorbed = absorb_enclosed_special_provinces(
        deleted_provinces,
        surviving_titled,
        present_ids - set(province_receiver_overrides.values()),
        groups,
        graph,
        boundaries,
    )
    deleted_provinces.update(absorbed)
    missing_definitions = sorted(deleted_provinces - id_to_rgb.keys())
    if missing_definitions:
        raise UpdateError("Deleted province IDs are undefined: " + ", ".join(map(str, missing_definitions)))
    physical_deleted = deleted_provinces & present_ids
    absent = deleted_provinces - physical_deleted
    if absent:
        log(f"WARNING: {len(absent)} deleted province IDs have no pixels in the base map")
    receivers = choose_receivers(
        physical_deleted,
        groups["impassable_mountains"],
        graph,
        boundaries,
    )
    if set(forced_receivers.values()) & physical_deleted:
        raise UpdateError("Regional map fill receiver is selected for deletion")
    receivers.update(forced_receivers)
    changed = 0
    lookup = np.arange(1 << 24, dtype=np.uint32)
    for province, receiver in receivers.items():
        source_rgb = id_to_rgb[province]
        target_rgb = id_to_rgb[receiver]
        source_code = source_rgb[0] << 16 | source_rgb[1] << 8 | source_rgb[2]
        target_code = target_rgb[0] << 16 | target_rgb[1] << 8 | target_rgb[2]
        lookup[source_code] = target_code
    # One lookup per pixel is ~3,000 times faster than scanning this 42 MP image
    # once for every deleted province. Work in strips to cap peak memory.
    for top in range(0, codes.shape[0], 256):
        bottom = min(top + 256, codes.shape[0])
        original = codes[top:bottom]
        mapped = lookup[original]
        changed += int(np.count_nonzero(mapped != original))
        rgb[top:bottom, :, 0] = (mapped >> 16).astype(np.uint8)
        rgb[top:bottom, :, 1] = (mapped >> 8).astype(np.uint8)
        rgb[top:bottom, :, 2] = mapped.astype(np.uint8)
    result_codes = image_codes(rgb)
    result_palette = set(np.unique(result_codes).tolist())
    remaining = [province for province in physical_deleted if (
        (id_to_rgb[province][0] << 16 | id_to_rgb[province][1] << 8 | id_to_rgb[province][2]) in result_palette
    )]
    if remaining:
        raise UpdateError("Deleted province colors remain after painting: " + ", ".join(map(str, remaining[:20])))
    return (
        Image.fromarray(rgb),
        changed,
        deleted_provinces,
        len(absorbed | regional_special),
    )


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


def managed_existing(repo: Path) -> set[Path]:
    manifest_path = repo / MANIFEST_NAME
    if not manifest_path.is_file():
        return set()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {Path(item) for item in data.get("generated_files", [])}


def mod_target(mod: Path, relative: Path) -> Path:
    if relative.is_absolute():
        raise UpdateError(f"Generated path must be relative: {relative}")
    root = mod.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise UpdateError(f"Generated path escapes the mod directory: {relative}") from error
    return target


def updated_descriptor(game: Path, mod: Path) -> str:
    branch = game.parent / "titus_branch.txt"
    if not branch.is_file():
        raise UpdateError(f"Cannot determine installed CK3 version: {branch} is missing")
    match = VERSION_RE.search(branch.read_text(encoding="utf-8-sig"))
    if not match:
        raise UpdateError(f"Unrecognized CK3 release branch in {branch}")
    descriptor = mod / "descriptor.mod"
    source = descriptor.read_text(encoding="utf-8-sig")
    updated, count = re.subn(
        r'^supported_version="[^\r\n]*"(\r?)$',
        lambda line: f'supported_version="{match.group(1)}.{match.group(2)}.*"{line.group(1)}',
        source,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise UpdateError(f"Expected exactly one supported_version in {descriptor}, found {count}")
    return updated


def write_outputs(
    repo: Path,
    mod: Path,
    text_outputs: dict[Path, str],
    province_image: Image.Image,
    dry_run: bool,
) -> tuple[int, int]:
    outputs = set(text_outputs) | {Path("map_data/provinces.png")}
    stale = managed_existing(repo) - outputs
    if dry_run:
        log(f"DRY RUN: would write {len(outputs)} files and delete {len(stale)} stale generated files")
        return len(outputs), len(stale)
    for relative in sorted(stale):
        path = mod_target(mod, relative)
        if path.is_file():
            path.unlink()
    for relative, source in sorted(text_outputs.items(), key=lambda item: str(item[0])):
        path = mod_target(mod, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            source,
            encoding="utf-8" if relative == Path("descriptor.mod") else "utf-8-sig",
            newline="",
        )
    map_path = mod_target(mod, Path("map_data/provinces.png"))
    map_path.parent.mkdir(parents=True, exist_ok=True)
    temp = map_path.with_suffix(".png.tmp")
    province_image.save(temp, format="PNG", compress_level=6)
    temp.replace(map_path)
    manifest = {
        "generated_files": sorted(str(path).replace("\\", "/") for path in outputs),
        "note": "Generated by update_mod.py; files not listed here are not managed by the generator.",
    }
    (repo / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for relative, expected in text_outputs.items():
        path = mod_target(mod, relative)
        encoding = "utf-8" if relative == Path("descriptor.mod") else "utf-8-sig"
        if path.read_bytes() != expected.encode(encoding):
            raise UpdateError(f"Generated text did not round-trip correctly: {path}")
    with Image.open(map_path) as written_map:
        if written_map.mode != "RGB" or written_map.size != province_image.size:
            raise UpdateError(f"Generated province map has wrong mode or dimensions: {map_path}")
        written_map.verify()
    if any(mod_target(mod, path).exists() for path in stale):
        raise UpdateError("Stale generated files remain after update")
    return len(outputs), len(stale)


def run(game: Path, dry_run: bool) -> int:
    game = game.resolve()
    mod = MOD_PATH.resolve()
    if mod == game or mod.is_relative_to(game) or game.is_relative_to(mod):
        raise UpdateError("Game input and mod output directories must not overlap")
    if not (game / "map_data" / "provinces.png").is_file():
        raise UpdateError(f"Not a CK3 game data directory: {game}")
    descriptor_text = updated_descriptor(game, mod)
    catalog, duplicates = load_title_catalog(game)
    if duplicates:
        log("WARNING: vanilla has duplicate title definitions: " + ", ".join(duplicates))
    log(f"Parsed {len(catalog.entries):,} landed titles from {len(catalog.documents)} files")
    landed, deleted_titles, deleted_provinces, replacement_counts = generate_landed_titles(
        catalog, set(title_delete)
    )
    log(f"Selected {len(deleted_titles):,} titles and {len(deleted_provinces):,} barony provinces")
    (
        image,
        changed_pixels,
        deleted_provinces,
        absorbed_special_count,
    ) = generate_province_map(game, deleted_provinces, deleted_titles, catalog)
    holy, removed_sites, all_sites, faith_dir = generate_holy_sites(game, deleted_titles)
    audit_religion_main_sites(game, all_sites - removed_sites)
    faiths, site_replacements, removed_references = generate_faiths(
        game, faith_dir, removed_sites, all_sites - removed_sites
    )
    duplicate_sites, sparse_faiths, undefined_sites = audit_faiths(
        game, faith_dir, faiths, all_sites - removed_sites
    )
    if duplicate_sites or undefined_sites:
        raise UpdateError(
            "Invalid generated religion data:\n" + "\n".join((duplicate_sites + undefined_sites)[:30])
        )
    if sparse_faiths:
        log(
            f"WARNING: {len(sparse_faiths)} faiths retain fewer than 3 holy sites after map reduction; "
            "add entries to rlps if this is not intended"
        )
    bookmarks, bookmark_count = generate_bookmarks(game, deleted_titles)
    history, history_count, cleared_history_lieges = generate_title_history(game, deleted_titles)
    province_history, province_history_count = generate_province_history(game, deleted_provinces)
    adjacencies, adjacency_count = generate_adjacencies(game, deleted_provinces)
    text_outputs = landed | holy | faiths | bookmarks | history | province_history | adjacencies
    text_outputs[Path("descriptor.mod")] = descriptor_text
    problems = effective_landed_audit(catalog, landed, deleted_titles)
    if problems:
        raise UpdateError("Surviving landed-title references point into deleted land:\n" + "\n".join(problems[:30]))
    history_problems = effective_title_history_audit(game, history, deleted_titles)
    if history_problems:
        raise UpdateError(
            "Generated title history still references deleted land:\n"
            + "\n".join(history_problems[:30])
        )
    for old in replace_title:
        if not replacement_counts[old]:
            log(f"WARNING: replace_title source did not occur in surviving landed titles: {old!r}")
    unused_rlps = sorted(set(rlps) & removed_sites - site_replacements.keys())
    if unused_rlps:
        log("WARNING: configured rlps sources were removed but not referenced: " + ", ".join(unused_rlps))
    log(
        f"Removed holy sites: {len(removed_sites)}; religion references replaced: "
        f"{sum(site_replacements.values())}; unmatched references removed: {sum(removed_references.values())}"
    )
    log(
        f"Removed bookmark characters: {bookmark_count}; title-history blocks: {history_count}; "
        f"cleared title-history lieges: {cleared_history_lieges}; "
        f"province-history blocks: {province_history_count}"
    )
    log(
        f"Removed special adjacencies: {adjacency_count}; absorbed enclosed map provinces: "
        f"{absorbed_special_count}; repainted pixels: {changed_pixels:,}"
    )
    written, stale = write_outputs(REPO_PATH, mod, text_outputs, image, dry_run)
    log(f"Complete: {written} generated files; {stale} stale files removed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", type=Path, default=GAME_PATH, help="CK3 game directory containing common/, history/, map_data/")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing files")
    args = parser.parse_args()
    try:
        return run(args.game_path, args.dry_run)
    except (UpdateError, TitleError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
