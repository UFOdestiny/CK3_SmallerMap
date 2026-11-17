"""Steam discovery and source fingerprints; no writes to the game installation."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .common import UpdateError

VERSION_RE = re.compile(r"(?:^|/)release/(\d+)\.(\d+)(?:\.\d+)?\s*$")


def supported_version(game: Path) -> str:
    branch = game.parent / "titus_branch.txt"
    if not branch.is_file():
        raise UpdateError(f"Cannot determine installed CK3 version: {branch} is missing")
    match = VERSION_RE.search(branch.read_text(encoding="utf-8-sig"))
    if not match:
        raise UpdateError(f"Unrecognized CK3 release branch in {branch}")
    return f"{match.group(1)}.{match.group(2)}.*"


def steam_roots() -> list[Path]:
    """Read registered Steam locations on Windows; other systems use --game-path."""
    try:
        import winreg
    except ImportError:
        return []
    roots = []
    for hive, key, value in (
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as handle:
                roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
        except OSError:
            continue
    return list(dict.fromkeys(roots))


def find_game_path(roots: list[Path] | None = None) -> Path:
    """Follow Steam libraryfolders.vdf, with deterministic ambiguity handling."""
    libraries = set()
    for root in steam_roots() if roots is None else roots:
        libraries.add(root)
        folders = root / "steamapps/libraryfolders.vdf"
        if folders.is_file():
            source = folders.read_text(encoding="utf-8-sig")
            libraries.update(Path(value.replace("\\\\", "\\"))
                             for value in re.findall(r'"path"\s+"([^"]+)"', source))
    candidates = sorted({(library / "steamapps/common/Crusader Kings III/game").resolve()
                         for library in libraries
                         if (library / "steamapps/common/Crusader Kings III/game/map_data/provinces.png").is_file()})
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise UpdateError("Multiple CK3 installations found; select one with --game-path: "
                          + ", ".join(map(str, candidates)))
    raise UpdateError("CK3 was not found in registered Steam libraries; supply --game-path PATH")


def source_snapshot(game: Path) -> dict:
    """Fingerprint every source database used by the generator, including new files."""
    directories = (
        "common/landed_titles", "common/religion/holy_site_types", "common/religion/faith_types",
        "common/religion/religion_types", "common/bookmarks/bookmarks", "history/titles",
        "history/provinces", "history/wars", "common/roads/road_types",
        "common/trade/county_potential_trade_goods", "map_data/geographical_regions",
    )
    paths = {path for directory in directories for path in (game / directory).glob("*.txt")}
    paths.update(game / name for name in (
        "map_data/provinces.png", "map_data/definition.csv", "map_data/default.map",
        "map_data/adjacencies.csv", "map_data/island_region.txt", "map_data/climate.txt",
        "common/on_action/game_start.txt",
    ))
    digest = hashlib.sha256()
    map_hashes = {}
    for path in sorted(paths):
        if not path.is_file():
            continue
        relative = path.relative_to(game).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{relative}\0{file_hash}\n".encode("utf-8"))
        if relative.startswith("map_data/") and "/geographical_regions/" not in relative:
            map_hashes[relative] = file_hash
    manifest = game.parents[2] / "appmanifest_1158310.acf"
    build = re.search(r'"buildid"\s+"(\d+)"', manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
    return {
        "branch": (game.parent / "titus_branch.txt").read_text(encoding="utf-8-sig").strip(),
        "steam_build_id": build.group(1) if build else None,
        "input_file_count": sum(path.is_file() for path in paths),
        "input_sha256": digest.hexdigest(),
        "map_sha256": map_hashes,
    }
