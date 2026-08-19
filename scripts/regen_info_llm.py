#!/usr/bin/env python3
"""Régénère info_llm.db depuis le local catalogue v4 (le COMPACTEUR).

Sélection automatique des meilleures lignes par priorité de source
(user > entreprise > official > models.dev), version la plus récente.

Usage :
    python3 scripts/regen_info_llm.py              # regen complète (auto)
    python3 scripts/regen_info_llm.py --wipe       # recrée la db puis regen
    python3 scripts/regen_info_llm.py --source models.dev   # 1 seule source
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sqlite.local import db_ro  # noqa: E402
from modules.sqlite.info_llm import (  # noqa: E402
    get_writer, WRITE_INFO_LLM_TOKEN, compact,
    db_path,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wipe", action="store_true",
                   help="supprime info_llm.db avant la régénération")
    p.add_argument("--source", default="",
                   help="force UNE seule source (sinon priorité auto)")
    p.add_argument("--batch", type=int, default=1000,
                   help="taille des mini-batchs d'upsert (défaut: 1000)")
    args = p.parse_args()

    if args.wipe:
        path = Path(db_path("info_llm.db"))
        if path.exists():
            path.unlink()
            print(f"db supprimée : {path}")

    lo = db_ro()
    iw = get_writer(WRITE_INFO_LLM_TOKEN)
    try:
        res = compact.compact(lo, iw, source_ref=args.source,
                              batch=args.batch)
        print("regen OK (priorité de source auto)" if not args.source
              else f"regen OK (source={args.source})")
        for t in ("catalogue_providers", "catalogue_models",
                  "model_capability", "provider_models",
                  "provider_models_mapping",
                  "provider_endpoint_api_key_type",
                  "endpoint_apikeytype_model_adress", "cost_final"):
            print(f"  {t:34} {res['counts'].get(t, 0)}")
    finally:
        iw.close()
        lo.close()


if __name__ == "__main__":
    main()