"""Coordinate explicit configuration, generation, audits and output in dependency order."""

from pathlib import Path
from dataclasses import asdict

from .common import UpdateError, log
from .config import MapConfig, DEFAULT_CONFIG
from .dependencies import audit_map_dependencies, generate_map_dependencies, generate_roads, generate_startup, generate_adjacencies
from .history import generate_bookmarks, generate_title_history, effective_title_history_audit, generate_province_history, generate_wars
from .installation import source_snapshot
from .religion import generate_holy_sites, audit_religion_main_sites, generate_faiths, audit_faiths
from .storage import updated_descriptor, write_outputs
from .terrain import generate_province_map
from .titles import load_title_catalog, dependent_merchant_titles, generate_landed_titles, effective_landed_audit


def run(game: Path, repo: Path, config: MapConfig = DEFAULT_CONFIG, *,
        dry_run: bool = False, check: bool = False) -> int:
    game = game.resolve()
    repo = repo.resolve()
    mod = (repo / "3488444772").resolve()
    if mod == game or mod.is_relative_to(game) or game.is_relative_to(mod):
        raise UpdateError("Game input and mod output directories must not overlap")
    if not (game / "map_data" / "provinces.png").is_file():
        raise UpdateError(f"Not a CK3 game data directory: {game}")
    descriptor_text = updated_descriptor(game, mod)
    source = source_snapshot(game)
    log(f"Game source: {source['branch']}; Steam build {source['steam_build_id'] or 'unknown'}")
    catalog, duplicates = load_title_catalog(game)
    if duplicates:
        log("WARNING: vanilla has duplicate title definitions: " + ", ".join(duplicates))
    log(f"Parsed {len(catalog.entries):,} landed titles from {len(catalog.documents)} files")
    selected = set(config.delete_titles)
    merchants = dependent_merchant_titles(catalog, selected)
    log(f"Selected {len(merchants)} additional merchant families based in removed counties")
    landed, deleted_titles, deleted_provinces, replacement_counts = generate_landed_titles(
        catalog, selected | merchants, config.capital_replacements,
    )
    log(f"Selected {len(deleted_titles):,} titles and {len(deleted_provinces):,} barony provinces")
    map_result = generate_province_map(
        game, deleted_provinces, deleted_titles, catalog, config.province_receivers,
    )
    deleted_provinces = map_result.removed_provinces
    holy, removed_sites, all_sites = generate_holy_sites(game, deleted_titles)
    audit_religion_main_sites(game, all_sites - removed_sites)
    faiths, site_replacements, removed_references = generate_faiths(
        game, removed_sites, all_sites - removed_sites,
        config.holy_site_replacements, config.faith_fallbacks,
    )
    duplicate_sites, sparse_faiths, undefined_sites = audit_faiths(
        game, faiths, all_sites - removed_sites
    )
    if duplicate_sites or undefined_sites:
        raise UpdateError(
            "Invalid generated religion data:\n" + "\n".join((duplicate_sites + undefined_sites)[:30])
        )
    if sparse_faiths:
        log(
            f"WARNING: {len(sparse_faiths)} faiths retain fewer than 3 holy sites after map reduction; "
            "add holy_site_replacements if this is not intended"
        )
    bookmarks, bookmark_count = generate_bookmarks(game, deleted_titles)
    history, history_count, cleared_history_lieges = generate_title_history(game, deleted_titles)
    province_history, province_history_count = generate_province_history(game, deleted_provinces)
    adjacencies, adjacency_count = generate_adjacencies(game, deleted_provinces)
    dependencies, dependency_counts = generate_map_dependencies(game, deleted_titles, deleted_provinces)
    roads, removed_roads = generate_roads(game, deleted_provinces)
    startup, startup_count = generate_startup(game, deleted_titles, deleted_provinces)
    wars, war_counts = generate_wars(game, deleted_titles, deleted_provinces)
    text_outputs = (landed | holy | faiths | bookmarks | history | province_history | adjacencies
                    | dependencies | roads | startup | wars)
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
    dependency_problems = audit_map_dependencies(
        game, text_outputs, deleted_titles, deleted_provinces, removed_roads,
    )
    if dependency_problems:
        raise UpdateError("Invalid generated map dependencies:\n" + "\n".join(dependency_problems[:30]))
    for old in config.capital_replacements:
        if not replacement_counts[old]:
            log(f"WARNING: capital replacement source did not occur in surviving landed titles: {old!r}")
    unused_site_replacements = sorted(set(config.holy_site_replacements) & removed_sites - site_replacements.keys())
    if unused_site_replacements:
        log("WARNING: configured holy-site replacement sources were removed but not referenced: " + ", ".join(unused_site_replacements))
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
        f"{map_result.absorbed_special_provinces}; repainted pixels: {map_result.repainted_pixels:,}"
    )
    log(f"Removed roads: {len(removed_roads)}; map/trade list references: {sum(dependency_counts.values())}; "
        f"startup effects: {startup_count}; historical wars: {war_counts['wars']}")
    if source_snapshot(game) != source:
        raise UpdateError("Game source changed during generation; wait for the update to finish and rerun")
    report = {
        "source": source,
        "configuration": asdict(config),
        "audit": {
            "status": "passed", "runtime_verified": False,
            "landed_titles": len(catalog.entries), "landed_title_files": len(catalog.documents),
            "vanilla_duplicate_titles": duplicates,
            "deleted_titles": len(deleted_titles), "dependent_merchant_titles": sorted(merchants),
            "removed_provinces": len(deleted_provinces), "map_size": list(map_result.image.size),
            "repainted_pixels": map_result.repainted_pixels, "absorbed_special_provinces": map_result.absorbed_special_provinces,
            "removed_holy_sites": len(removed_sites), "sparse_faiths": sparse_faiths,
            "removed_bookmark_characters": bookmark_count, "removed_title_history_blocks": history_count,
            "cleared_title_history_lieges": cleared_history_lieges,
            "removed_province_history_blocks": province_history_count,
            "removed_adjacencies": adjacency_count, "removed_roads": len(removed_roads),
            "removed_map_list_references": dict(dependency_counts),
            "removed_startup_effects": startup_count, "removed_war_data": dict(war_counts),
        },
    }
    written, stale = write_outputs(repo, mod, text_outputs, map_result.image, dry_run, check, report)
    action = "verified" if check else "planned" if dry_run else "written"
    log(f"Complete: {written} generated files {action}; {stale} stale files")
    return 0
