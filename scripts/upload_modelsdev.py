#!/usr/bin/env python3
"""upload_modelsdev — téléverse models.dev → buffer → local catalogue.

Usage :
    python3 scripts/upload_modelsdev.py                 # fetch + push + consume
    python3 scripts/upload_modelsdev.py --file /tmp/opencode/modelsdev/api.json
    python3 scripts/upload_modelsdev.py --version 2026-08-18 --no-consume
    python3 scripts/upload_modelsdev.py --batch 1000

Le flux :
  1. seed_catalogue_types (types + registres + source models.dev — idempotent)
  2. fetch du snapshot (réseau ou --file ; --cache pour réutiliser)
  3. parse + build des ops (toute info non transformable → log_error_modeldev)
  4. push par mini-batchs dans le buffer (importeur : jamais d'écriture local)
  5. consume par le writer local (ops → x_data + tags)
"""
import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sqlite import local as L  # noqa: E402
from modules.sqlite import buffer as B  # noqa: E402
from modules.sqlite.local import catalogue_types as CT  # noqa: E402
from modules.ingest import modelsdev as MD  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", type=Path, help="snapshot local (api.json)")
    ap.add_argument("--cache", type=Path, help="cache disque du snapshot")
    ap.add_argument("--version", default="",
                    help="version du snapshot (défaut : aujourd'hui)")
    ap.add_argument("--batch", type=int, default=MD.DEFAULT_BATCH)
    ap.add_argument("--limit", type=int, default=2000,
                    help="ops consommées par mini-batch local")
    ap.add_argument("--no-consume", action="store_true",
                    help="s'arrêter après le push buffer")
    args = ap.parse_args()

    lw = L.get_writer(L.WRITE_CATALOGUE_TOKEN)
    bw = B.get_writer(B.WRITE_BUFFER_TOKEN)
    try:
        CT.seed_catalogue_types(lw, token="write_catalogue")
        if args.file:
            import json
            data = json.load(open(args.file, encoding="utf-8"))
        else:
            data = MD.fetch(cache_path=args.cache)
        res = MD.run(data, bw, lw, version=args.version,
                     batch=args.batch, limit=args.limit,
                     consume=not args.no_consume)
        print(f"records   : {res['records']}")
        print(f"ops push  : {res['ops']}")
        print(f"consumed  : {res['consumed']}")
        print(f"log       : {res['log']} "
              f"(détails: <mw>/logs/log_error_modeldev.log)")
        return 0
    finally:
        bw.close()
        lw.close()


if __name__ == "__main__":
    raise SystemExit(main())