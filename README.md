# CK3 Smaller Map

CK3 Smaller Map reduces the playable Crusader Kings III map for improved
performance. It is published on the [Steam
Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3488444772).

The repository includes a source-preserving update tool. When CK3 is updated,
you can rebuild the mod from the installed game data instead of editing
`provinces.png` manually in Photoshop.

The current selection keeps Taiwan and Ryukyu under the Nusantara empire and
restores Ryukyu's titular empire. The Philippines and the selected Southeast
Asian kingdoms remain outside the playable map.

## Requirements

- Python 3.10 or newer.
- A local Crusader Kings III installation.
- The packages in `requirements.txt`.

Install the Python dependencies once:

```powershell
python -m pip install -r requirements.txt
```

## Rebuild and validate

Edit the settings near the top of
[`update_mod.py`](update_mod.py):

- `title_delete`: landed titles to remove. A selected empire, kingdom, duchy,
  county, or barony removes its complete descendant tree.
- `province_receiver_overrides`: give each deleted region one impassable
  receiver, including its adjoining untitled land. The Southeast Asian islands
  and Malay Peninsula use province `11215`, RGB `(10, 13, 16)`, matching
  Kalimantan. Deleted northern regions use Siberian Wastes (`1464`), RGB
  `(127, 177, 4)`. Both receivers are already impassable in the game's
  `default.map`; merged land no longer retains its former province ID.
- `replace_title`: exact text replacements for surviving landed titles whose
  capital would otherwise point to a deleted county. Nusantara's capital is
  moved from Tondo to Taiwan in the current configuration.
- `rlps`: replacements for removed holy sites. Keys are removed holy-site IDs;
  values must be holy sites that remain on the map.
- `faith_fallbacks`: one surviving holy site for faiths whose entire original
  holy-site region has been removed. CK3 1.20 requires at least one per faith.

The default game path is declared as `GAME_PATH`. It can be overridden for one
run with `--game-path`. From the repository root, run:

```powershell
python update_mod.py
```

For a different CK3 installation:

```powershell
python update_mod.py --game-path "E:\SteamLibrary\steamapps\common\Crusader Kings III\game"
```

To validate without writing or deleting files:

```powershell
python update_mod.py --dry-run
```

`--dry-run` performs the complete parse, map rewrite, and integrity audit but
does not write or delete any mod files.

## Generated output

The generator reads the installed game as read-only input and rebuilds affected
files only inside this repository's `3488444772` directory:

- every affected `common/landed_titles/*.txt` file, not only
  `00_landed_titles.txt`;
- holy-site definitions and the CK3 1.20 faith definitions (including eminent
  and ordinary holy-site lists);
- bookmarks, title history, province history, and map adjacencies;
- `map_data/provinces.png`;
- `descriptor.mod`, with `supported_version` derived from the installed
  game's `titus_branch.txt` (for example, `1.20.*`).

Deleted provinces are recolored with a nearby impassable province color, except
for regions with an explicit receiver override.
The tool preserves valid RGB values from `definition.csv`; it does not create
anti-aliased colors or new province colors. It also removes enclosed rivers,
lakes, impassable terrain, and holding-less/road provinces that would otherwise
leave colored fragments inside deleted land.

For overridden regions, the tool also follows connected untitled land in the
original map. This includes mountains missing from `default.map`'s mountain
lists. Components touching surviving baronies are retained; sea zones and
impassable seas are excluded. This fills island interiors and coastal fragments
without relying on hand-painted masks or colors from an older mod image.
Untitled pockets adjoining the receiver itself are included too, so enclosed
Siberian rivers, lakes, and mountain provinces use the surrounding wasteland
color.

Map cleanup uses the same pipeline across all generated fill regions:

1. Read deleted-title province history once, then build the original province
   adjacency graph (including the east/west world-map seam).
2. Apply regional overrides and bounded absorption of untitled special terrain.
   Other deleted components use the nearest reachable impassable receiver;
   equal choices are resolved deterministically by province ID.
3. Recolor whole provinces with a single lookup per pixel, then check **every
   receiver actually used**, not just the Southeast Asia/Siberia overrides.
   One connected-component labeling pass per receiver fills enclosed untitled
   pixels, including disconnected fragments of a shared river/lake province.
   Pixel connectivity also respects the world-map seam.
4. Protect retained baronies, sea zones, existing receiver pixels, and complete
   enclosed components containing any of them. Compare protected pixels against
   the original game map before writing. Convert back to RGB only once.
5. Remove shared province history and adjacencies only when all pixels of that
   province disappear; a partly filled lake retains its outside shapes and data.

No old hand-painted map, province-name guesswork, or one-off island mask is used.

For surviving title history, `liege` and `de_jure_liege` references to deleted
titles are explicitly set to `0`. This prevents a restored region from losing
its history because one historical entry still references a deleted liege.

## Validation and safety

The pre-write checks reject, among other issues:

- a configured title missing from the installed CK3 version;
- a capital or de-jure liege that still references deleted land;
- title history that still points to a deleted liege;
- a changed parent or province assignment for a surviving landed title;
- a title-less landed-title override that still contains executable text;
- malformed Clausewitz braces or unknown map colors;
- invalid province RGB definitions or map ranges, ambiguous receiver colors,
  or any modification of protected original map pixels;
- duplicate or undefined faith holy sites, or a faith left with no holy site;
- no reachable or configured impassable province to receive deleted map pixels.

After writing, the command reopens every generated text file and the PNG to
verify that they are readable and have the expected content or dimensions.
Some faiths intentionally retain only one or two sites after their original
region is removed; none are left with zero. These are static checks, not proof
of correct in-game rendering. Fully restart CK3 and start a new game before
publishing a new release. Existing saves do not reinitialize map and title data.

Generated file paths are recorded in `.smaller_map_generated.json`. On a normal
run, obsolete files previously generated by this tool are removed from the Mod
folder. Files outside the manifest are left alone.

The command line cannot redirect output to the game or Workshop directories,
and overlapping game/mod paths are rejected. To test a rebuilt mod through the
Steam installation, sync the generated directory to the local Workshop copy
separately. This script does not commit, push, or upload to Steam Workshop.

## Project Layout

- `update_mod.py` — configuration, generation workflow, CK3 text transforms,
  validation, and output writing.
- `ck3parser.py` — source-preserving Clausewitz tokenizer and edit helpers.
- `ck3titles.py` — landed-title catalog, parent hierarchy, and province index.
- `3488444772/` — generated Mod content.

## Acknowledgement

This work is inspired by [Smaller World
Map](https://steamcommunity.com/sharedfiles/filedetails/?id=2882223898) by
[Starmender](https://steamcommunity.com/id/starmender), whose work taught me
the basic map-modding process.
