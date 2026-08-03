"""check_google_api.py — teste directement chaque modèle Google Gemini.

Appelle l'API Gemini (via DirectBridge, qui résout la clé réelle depuis
modelweaver.db) avec une petite prompt, pour vérifier que chaque modèle
répond. Affiche l'état BDD (unavailable/noretryuntil) et le résultat réel.

Usage :
    python3 check_google_api.py            # tous les modèles google
    python3 check_google_api.py --only gemini-3.5 gemma-4
"""

import argparse
import sys
import time

sys.path.insert(0, "/home/pierreloup2/PilousGarage/ModelWeaver")


def _load_google_models() -> list:
    """Liste les modèles google disponibles depuis le catalogue."""
    import sqlite3
    cat = sqlite3.connect("/home/pierreloup2/.modelweaver/catalogue.db")
    cat.row_factory = sqlite3.Row
    rows = cat.execute("""
        SELECT DISTINCT kem.provider_model_name
        FROM key_endpoint_models kem
        WHERE kem.provider_id = (SELECT id FROM catalogue_providers WHERE ref='google')
          AND kem.available = 1
          AND kem.provider_model_name NOT LIKE 'google/%'
        ORDER BY kem.provider_model_name
    """).fetchall()
    return [r["provider_model_name"] for r in rows]


def _bdd_state(model: str) -> str:
    """Retourne l'état BDD du modèle (unavailable/noretry)."""
    import sqlite3
    cat = sqlite3.connect("/home/pierreloup2/.modelweaver/catalogue.db")
    cat.row_factory = sqlite3.Row
    row = cat.execute("""
        SELECT pm.unavailable, pm.noretryuntil, pm.notrytime
        FROM provider_models pm
        JOIN catalogue_providers p ON p.id = pm.provider_id
        WHERE p.ref = 'google' AND pm.provider_model_name = ?
    """, (model,)).fetchone()
    if not row:
        return "?"
    if row["noretryuntil"] and row["noretryuntil"] > time.time():
        return f"REST {(row['noretryuntil'] - time.time()) / 60:.0f}min"
    if row["unavailable"]:
        return "unav (repos expiré)"
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="Test les modèles Google Gemini")
    ap.add_argument("--only", nargs="*", help="Filtrer par sous-chaîne (ex. gemini-3.5)")
    args = ap.parse_args()

    from modules.llm_manager.direct_bridge import DirectBridge
    from modules.sql.db import CatalogueDB

    models = _load_google_models()
    if args.only:
        models = [m for m in models if any(o.lower() in m.lower() for o in args.only)]
    if not models:
        print("aucun modèle trouvé")
        sys.exit(1)

    print(f"Test de {len(models)} modèles google via l'API Gemini\n")
    print(f"{'MODÈLE':<38} {'BDD':<22} {'RÉPONSE':<12} {'LATENCE':<10} {'ERREUR'}")
    print("-" * 100)

    bridge = DirectBridge(cat=CatalogueDB())
    ok = fail = 0
    for m in models:
        bdd = _bdd_state(m)
        t0 = time.time()
        try:
            resp = bridge.chat("google", m,
                               [{"role": "user", "content": "dis ok"}],
                               max_tokens=5, temperature=0)
            lat = (time.time() - t0) * 1000
            content = (resp.content or "").strip()[:12] if hasattr(resp, "content") else "?"
            print(f"{m:<38} {bdd:<22} {content:<12} {lat:6.0f}ms   {'':<10}")
            ok += 1
        except Exception as e:
            lat = (time.time() - t0) * 1000
            err = str(e).replace("\n", " ")[:60]
            print(f"{m:<38} {bdd:<22} {'ECHEC':<12} {lat:6.0f}ms   {err}")
            fail += 1

    print("-" * 100)
    print(f"Résultat : {ok} OK, {fail} échecs sur {len(models)} modèles")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
