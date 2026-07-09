"""Test d'intégration Agent Codeur.

Crée un agent codeur, lui demande d'écrire un tic-tac-toe,
valide le code produit.

Détecte automatiquement le provider disponible (Groq > Mistral > autre).
"""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sql.db import ModelWeaverDB
from agents.factory import AgentFactory
from agents.role_manager import RoleManager


# Providers testables, en ordre de priorité
# Chaque entrée : (provider_ref, model_name, endpoint_url)
PREFERRED_PROVIDERS = [
    ("groq", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"),
    ("mistral", "mistral-small-2501", "https://api.mistral.ai/v1"),
    ("nvidia", "nvidia/llama-3.1-nemotron-70b-instruct", "https://integrate.api.nvidia.com/v1"),
]


def load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("\"'")
    return env


def find_usable_provider(db: ModelWeaverDB, env: dict) -> dict:
    """Cherche le premier provider avec une clé utilisable.

    Crée le provider et la clé dans la BDD si nécessaire (self-contained).
    """
    for prov_ref, model, endpoint in PREFERRED_PROVIDERS:
        prov = db.providers.get(prov_ref)

        # Créer le provider s'il n'existe pas
        if not prov:
            env_key = f"{prov_ref.upper()}_API_KEY"
            if env_key not in env:
                continue
            prov_id = db.providers.save({
                "ref": prov_ref,
                "name": prov_ref.title(),
                "provider_type": "cloud",
                "api_type": "openai",
            })
            prov = db.providers.get_by_id(prov_id)
            db.commit()

        # Chercher ou créer la clé API
        key = db.keys.get_for_provider(prov_ref)
        if not key:
            env_key = f"{prov_ref.upper()}_API_KEY"
            api_key = env.get(env_key)
            if not api_key:
                print(f"   ⚠️  Clé '{prov_ref}' introuvable dans .env ({env_key})")
                continue
            key_ref = db.keys.save({
                "provider_id": prov["id"],
                "key_value": api_key,
                "identity": "default",
                "tag": "paid",
                "health_status": "unknown",
            })
            db.commit()
            key = db.keys.get(key_ref)

        print(f"   ✅ Provider : {prov['name']} → {model}")
        return {
            "provider_ref": prov_ref,
            "model_name": model,
            "endpoint_url": endpoint,
            "key_ref": key["ref"],
            "key_value": key["key_value"],
            "provider_id": prov["id"],
        }
    return None


def seed_model_provider(db: ModelWeaverDB, cfg: dict) -> int:
    """Crée l'entrée model_provider si pas déjà présente."""
    existing = db.model_providers.list_all()
    for mp in existing:
        if mp["model_name"] == cfg["model_name"]:
            print(f"   model_provider déjà existant : {mp['provider_id']}")
            return mp["provider_id"]

    pid = db.model_providers.save({
        "name": f"{cfg['provider_ref'].title()} {cfg['model_name']}",
        "engine_type": "openai_api",
        "model_name": cfg["model_name"],
        "endpoint_url": cfg["endpoint_url"],
        "max_concurrent": 3,
        "api_key_ref": cfg["key_ref"],
    })
    db.commit()
    print(f"   ✅ model_provider créé : {pid}")
    return pid


def validate_script(content: str) -> bool:
    checks = [
        ("grille 3x3", re.search(r"(board|grid|grille|plateau|tableau)", content, re.I)),
        ("affiche le plateau", "print" in content),
        ("boucle de jeu", "while" in content or "for.*turn" in content),
        ("vérification victoire", re.search(r"(win|gagn|victoire|check|verif)", content, re.I)),
        ("if __name__", 'if __name__ == "__main__"' in content),
        ("choix aléatoire", "random" in content.lower() or "randint" in content.lower()),
    ]
    ok = all(ok for _, ok in checks)
    print(f"\n   Vérifications du code produit :")
    for label, ok in checks:
        print(f"     {'✅' if ok else '❌'} {label}")
    return ok


def main():
    print("\n🧪 Test Agent Codeur — Génération d'un tic-tac-toe\n")

    env = load_env(Path(__file__).resolve().parent.parent / ".env")

    db = ModelWeaverDB()

    # 1. Trouver un provider utilisable
    cfg = find_usable_provider(db, env)
    if not cfg:
        print("❌ Aucun provider avec clé disponible")
        print("   Vérifie tes clés API dans .env")
        db.close()
        return

    # 2. Seed le model_provider
    mp_id = seed_model_provider(db, cfg)

    # 3. Créer l'agent codeur
    factory = AgentFactory(db=db)
    agent = factory.create_agent(
        name="CodeurTicTacToe",
        role_type="codeur",
        provider_id=mp_id,
    )
    print(f"\n   Agent créé : {agent.name} (ID={agent.agent_id})")

    # 4. Exécuter la tâche
    print("\n   Exécution de la tâche : 'Écris un tic-tac-toe...'")
    t0 = time.time()
    result = agent.execute(
        request=(
            "Écris un script Python qui joue au tic-tac-toe tout seul à random. "
            "Le script doit afficher la grille après chaque coup, "
            "détecter la victoire ou le match nul, et afficher le résultat. "
            "Les deux joueurs jouent aléatoirement."
        ),
        skill="code_generation",
    )
    elapsed = time.time() - t0
    print(f"   Temps : {elapsed:.1f}s")
    print(f"   Statut : {result['status']}")

    # 5. Vérifier l'erreur éventuelle
    if result.get("traceback"):
        print(f"   Traceback:\n{result['traceback']}")
    if result.get("message"):
        print(f"   Message: {result['message']}")

    if result["status"] != "ok":
        print(f"\n❌ Échec: {result.get('message', 'statut inconnu')}")
        agent.exit()
        db.close()
        return

    # 6. Valider le code
    code = result.get("content", "")
    print(f"\n   Code produit ({len(code)} caractères) :")
    print("-" * 60)
    print(code[:1500] + ("..." if len(code) > 1500 else ""))
    print("-" * 60)

    valid = validate_script(code)

    # 7. Sauvegarder le script
    output = Path(__file__).resolve().parent / "tic_tac_toe_output.py"
    output.write_text(code, encoding="utf-8")
    print(f"\n   Script sauvegardé : {output}")

    agent.exit()
    db.close()

    if valid:
        print("\n✅ Test Agent Codeur PASSÉ")
    else:
        print("\n⚠️  Test Agent Codeur — code produit mais vérifications incomplètes")


if __name__ == "__main__":
    main()
