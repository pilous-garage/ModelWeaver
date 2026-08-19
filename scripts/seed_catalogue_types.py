#!/usr/bin/env python3
"""seed_catalogue_types — déclare les 7 data_types du catalogue LLM (block 1)
dans le domaine local, avec leurs registres de tags + la source models.dev.

Usage :
    python3 scripts/seed_catalogue_types.py [--reset]

IDEMPOTENT : re-runnable sans perte (CREATE IF NOT EXISTS + upserts).
--reset : reset_below du domaine (détruit tout, puis re-seed le schéma v4
et les 7 types) — la source models.dev re-déclarée aussi."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sqlite import local as L
from modules.sqlite.local import catalogue_types as CT


def main() -> int:
    reset = "--reset" in sys.argv
    lw = L.get_writer(L.WRITE_CATALOGUE_TOKEN)
    try:
        if reset:
            r = L.reset_below(lw, 4)
            print(f"reset_below(4) -> {r}")
        r = CT.seed_catalogue_types(lw, token="write_catalogue")
        print(f"types: {r['types']}")
        print(f"registres de tags: {r['tag_registries']}")
        print(f"source: {r['source']}")
        return 0
    finally:
        lw.close()


if __name__ == "__main__":
    raise SystemExit(main())