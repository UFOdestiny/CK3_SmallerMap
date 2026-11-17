"""Rebuild CK3 Smaller Map from installed game files, or verify existing output."""

import argparse
from pathlib import Path
import sys

from smaller_map.common import UpdateError
from smaller_map.installation import find_game_path
from smaller_map.pipeline import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", type=Path,
                        help="CK3 game directory; defaults to discovery in registered Steam libraries")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate without writing any output")
    mode.add_argument("--check", action="store_true", help="Fail on generated-file drift without writing")
    args = parser.parse_args()
    try:
        game = args.game_path if args.game_path is not None else find_game_path()
        return run(game, Path(__file__).resolve().parent, dry_run=args.dry_run, check=args.check)
    except (UpdateError, OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
