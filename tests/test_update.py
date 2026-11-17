"""Regression cases for map dependencies that cannot be safely text-replaced."""

from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
from PIL import Image

from smaller_map import dependencies, history, titles, terrain, storage
from smaller_map.script import parse
from smaller_map.titles import load_title_catalog
from smaller_map.common import UpdateError
from smaller_map.installation import find_game_path
from smaller_map.script import ParseError


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.game = Path(self.temporary.name)

    def put(self, name, source):
        path = self.game / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8-sig")
        return Path(name)

    def test_indexed_assignments_match_original_scanner(self):
        document = parse('h_world = { capital = c_home color = hsv { 0 1 1 } '
                         'e_keep = { capital = c_keep b_keep = { province = 8 } } '
                         'note = "# quoted braces { }" capital = c_last }')
        for block in document.blocks():
            tokens = [t for t in document.tokens if t.depth == block.inner_depth
                      and block.open_token.end <= t.start < block.close_token.start]
            expected = []
            index = 0
            while index + 2 < len(tokens):
                key, equals, value = tokens[index:index + 3]
                after = tokens[index + 3] if index + 3 < len(tokens) else None
                if equals.text == "=" and value.text not in {"{", "}"} and (after is None or after.text != "{"):
                    expected.append((key.text, value.text))
                    index += 3
                else:
                    index += 1
            self.assertEqual(expected, [(a.key, a.value) for a in document.direct_assignments(block)])
        self.assertIn("h_world", [b.key for b in document.title_blocks()])

    def test_merchant_selection_follows_removed_home_only(self):
        self.put("common/landed_titles/world.txt", """
h_world = { e_remove = { k_remove = { c_home = { b_home = { province = 1 } } } } }
c_keep = { b_keep = { province = 2 } }
c_merchant_removed = { capital = c_home }
c_merchant_dependent = { capital = c_merchant_removed }
c_merchant_keep = { capital = c_keep }
d_holy_order = { capital = c_home }
""")
        catalog, _ = load_title_catalog(self.game)
        self.assertEqual({"c_merchant_removed", "c_merchant_dependent"},
                         titles.dependent_merchant_titles(catalog, {"e_remove"}))

    def test_road_deleted_middle_never_creates_shortcut(self):
        rel = self.put("common/roads/road_types/roads.txt", """
road_crossing = { tier = 2 provinces = { 1 2 3 } }
road_sea = { spline_asset_id = 1 provinces = { 1 9 3 } }
road_keep = { tier = 1 provinces = { 1 4 3 } } # preserved
""")
        output, removed = dependencies.generate_roads(self.game, {2, 9})
        self.assertEqual({"road_crossing", "road_sea"}, removed)
        self.assertEqual(["road_keep"], [b.key for b in parse(output[rel]).roots])
        self.assertIn("provinces = { 1 4 3 }", output[rel])

    def test_typed_lists_preserve_regions_comments_and_numbers(self):
        rel = self.put("map_data/geographical_regions/regions.txt", """
region = { counties = { c_removed c_keep } provinces = { 1 2 } color = { 1 2 3 } }
empty_region = { counties = { c_removed } } # used by scripts
""")
        goods = self.put("common/trade/county_potential_trade_goods/goods.txt",
                         "good = { counties = { c_removed c_keep } }")
        output, counts = dependencies.generate_map_dependencies(self.game, {"c_removed"}, {1})
        self.assertEqual(4, sum(counts.values()))
        self.assertIn("color = { 1 2 3 }", output[rel])
        self.assertIn("empty_region = { counties = {  } } # used by scripts", output[rel])
        self.assertNotIn("c_removed", output[goods])
        self.assertFalse(dependencies.audit_map_dependencies(self.game, output, {"c_removed"}, {1}, set()))

    def test_startup_handles_dotted_scopes_and_nested_removal(self):
        rel = self.put("common/on_action/game_start.txt", """
on_game_start = { effect = {
title:b_removed.title_province = { title:c_removed = { add_gold = 1 } }
title:c_keep = { add_gold = 2 }
gdp_historical_company_setup_effect = { TITLE = c_removed FOUNDED = 849 }
gdp_historical_company_setup_effect = { TITLE = c_keep FOUNDED = 900 }
province:1 = { add_gold = 3 }
} }
""")
        output, count = dependencies.generate_startup(self.game, {"b_removed", "c_removed"}, {1})
        self.assertEqual(3, count)
        self.assertIn("TITLE = c_keep FOUNDED = 900", output[rel])
        self.assertNotIn("removed", output[rel])

    def test_wars_remove_empty_and_keep_mixed_targets(self):
        rel = self.put("history/wars/wars.txt", """
war = { name = removed targeted_titles = { c_removed } }
war = { name = keep targeted_titles = { c_removed c_keep }
    123 = { 867.1.1 = { location = 1 } } }
""")
        output, counts = history.generate_wars(self.game, {"c_removed"}, {1})
        self.assertEqual({"wars": 1, "targets": 1, "locations": 1}, dict(counts))
        self.assertEqual(1, len(parse(output[rel]).roots))
        self.assertIn("c_keep", output[rel])
        self.assertNotIn("location", output[rel])

    def test_check_detects_single_changed_pixel_without_writing(self):
        mod = self.game / "mod"
        mod.mkdir()
        image = Image.new("RGB", (2, 2), (10, 20, 30))
        texts = {Path("descriptor.mod"): 'supported_version="1.21.*"\n'}
        storage.write_outputs(self.game, mod, texts, image, False)
        storage.write_outputs(self.game, mod, texts, image, False, check=True)
        pixels = np.array(image)
        pixels[1, 1] = (10, 20, 31)
        Image.fromarray(pixels).save(mod / "map_data/provinces.png")
        before = (mod / "map_data/provinces.png").read_bytes()
        with self.assertRaisesRegex(UpdateError, "pixel mismatch"):
            storage.write_outputs(self.game, mod, texts, image, False, check=True)
        self.assertEqual(before, (mod / "map_data/provinces.png").read_bytes())

    def test_wrapped_components_protect_surviving_land(self):
        labels = np.array([[1, 0, 2], [0, 0, 0]], dtype=np.int32)
        flags = np.array([False, True, False])
        terrain.propagate_seam_flags(labels, flags)
        self.assertTrue(flags[2])
        codes = np.array([[9, 9, 9, 9], [9, 1, 2, 9], [9, 9, 9, 9]], dtype=np.uint32)
        protected = codes == 2
        region = codes == 9
        self.assertFalse(terrain.wasteland_enclaves(codes, 9, protected, region).any())

    def test_parser_still_rejects_unbalanced_input(self):
        for source in ('x = {', 'x = { } }', 'x = "unfinished'):
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse(source)

    def test_capital_replacements_are_token_scoped_and_explicit(self):
        rel = self.put("common/landed_titles/world.txt", '''
c_old = { b_old = { province = 1 } }
c_keep = { capital\t= c_old note = "capital = c_old" } # capital = c_old
c_other = { capital = c_old_extra }
''')
        catalog, _ = load_title_catalog(self.game)
        output, _, _, counts = titles.generate_landed_titles(catalog, set(), {"c_old": "c_new"})
        self.assertIn('capital\t= c_new', output[rel])
        self.assertIn('note = "capital = c_old"', output[rel])
        self.assertIn('# capital = c_old', output[rel])
        self.assertIn('capital = c_old_extra', output[rel])
        self.assertEqual({"c_old": 1}, dict(counts))
        # A different policy uses the same catalog without inheriting prior edits.
        self.assertFalse(titles.generate_landed_titles(catalog, set(), {})[0])

    def test_unknown_selected_title_has_a_clear_error(self):
        self.put("common/landed_titles/world.txt", "c_keep = { }")
        catalog, _ = load_title_catalog(self.game)
        with self.assertRaisesRegex(UpdateError, "c_missing"):
            titles.dependent_merchant_titles(catalog, {"c_missing"})

    def test_steam_discovery_follows_libraries_and_rejects_ambiguity(self):
        steam = self.game / "Steam"
        steamapps = steam / "steamapps"
        steamapps.mkdir(parents=True)
        library = self.game / "Other Library"
        folders = steamapps / "libraryfolders.vdf"
        folders.write_text('"libraryfolders" { "1" { "path" "' + library.as_posix() + '" } }')
        game = library / "steamapps/common/Crusader Kings III/game"
        (game / "map_data").mkdir(parents=True)
        (game / "map_data/provinces.png").touch()
        self.assertEqual(game.resolve(), find_game_path([steam]))
        other = steam / "steamapps/common/Crusader Kings III/game"
        (other / "map_data").mkdir(parents=True)
        (other / "map_data/provinces.png").touch()
        with self.assertRaisesRegex(UpdateError, "Multiple CK3"):
            find_game_path([steam])

    def test_output_modes_handle_stale_files_without_touching_unmanaged_content(self):
        mod = self.game / "mod"
        mod.mkdir()
        image = Image.new("RGB", (2, 2))
        texts = {Path("descriptor.mod"): 'name="test"\n'}
        storage.write_outputs(self.game, mod, texts, image, False)
        (mod / "old.txt").write_text("obsolete")
        (mod / "custom.txt").write_text("keep")
        manifest = self.game / storage.MANIFEST_NAME
        data = json.loads(manifest.read_text())
        data["generated_files"].append("old.txt")
        manifest.write_text(json.dumps(data))
        before = {p.relative_to(self.game): p.read_bytes() for p in self.game.rglob('*') if p.is_file()}
        self.assertEqual((2, 1), storage.write_outputs(self.game, mod, texts, image, True))
        with self.assertRaisesRegex(UpdateError, "stale"):
            storage.write_outputs(self.game, mod, texts, image, False, check=True)
        after = {p.relative_to(self.game): p.read_bytes() for p in self.game.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        storage.write_outputs(self.game, mod, texts, image, False)
        self.assertFalse((mod / "old.txt").exists())
        self.assertEqual("keep", (mod / "custom.txt").read_text())

    def test_all_manifest_paths_are_validated_before_deleting_anything(self):
        mod = self.game / "mod"
        mod.mkdir()
        (mod / "old.txt").write_text("keep until validation passes")
        (self.game / storage.MANIFEST_NAME).write_text(json.dumps({
            "generated_files": ["old.txt", "../outside.txt"],
        }))
        with self.assertRaisesRegex(UpdateError, "escapes"):
            storage.write_outputs(self.game, mod, {}, Image.new("RGB", (1, 1)), False)
        self.assertTrue((mod / "old.txt").is_file())


if __name__ == "__main__":
    unittest.main()
