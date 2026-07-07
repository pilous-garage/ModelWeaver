#!/usr/bin/env python3
"""Ordonne les modèles dans litellm_config.yaml selon les préférences.

Lit .modelweaver/fallback_preferences.yaml qui définit l'ordre des groupes,
les patterns de classification et les cooldowns.

Usage :
    python ordonner-fallback.py                          # utilise les préférences par défaut
    python ordonner-fallback.py --prefs mon_fichier.yaml # fichier perso
    python ordonner-fallback.py --dry-run                # simulation sans écrire
"""

import json
import re
import sys
import yaml
from pathlib import Path
from collections import Counter

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PREFS = APP_DIR / ".modelweaver" / "fallback_preferences.yaml"
YAML_PATH = APP_DIR / ".modelweaver" / "litellm_config.yaml"
SCORES_PATH = APP_DIR / ".modelweaver" / "model_scores.json"


def load_prefs(path: Path) -> tuple[list[dict], dict]:
    """Charge les préférences depuis le YAML.

    Retourne (groupes_ordonnés, dict_patterns) où dict_patterns[group_name] = (pattern_compilé, cooldown)
    """
    if not path.exists():
        print(f"❌ {path} introuvable")
        sys.exit(1)

    with open(path) as f:
        prefs = yaml.safe_load(f)

    raw_groups = prefs.get("groups", [])
    if not raw_groups:
        print(f"❌ Aucun groupe défini dans {path}")
        sys.exit(1)

    groups = []
    patterns = {}
    for g in raw_groups:
        name = g.get("name", "?")
        try:
            compiled = re.compile(g["pattern"])
        except re.error as e:
            print(f"❌ Erreur regex pour le groupe '{name}': {e}")
            sys.exit(1)
        cd = g.get("cooldown", 300)
        groups.append(name)
        patterns[name] = (compiled, cd)

    return groups, patterns


def classify(key: str, groups: list, patterns: dict) -> str:
    """Trouve le groupe correspondant au modèle."""
    key_lower = key.lower()
    for name in groups:
        compiled, _ = patterns[name]
        if compiled.search(key_lower):
            return name
    return groups[-1] if groups else "other"


def load_scores() -> dict:
    if SCORES_PATH.exists():
        return json.loads(SCORES_PATH.read_text()).get("models", {})
    return {}


def run(prefs_path: Path | None = None, dry_run: bool = False):
    prefs_file = prefs_path or DEFAULT_PREFS
    groups, patterns = load_prefs(prefs_file)

    if not YAML_PATH.exists():
        print(f"❌ {YAML_PATH} introuvable")
        sys.exit(1)

    with open(YAML_PATH) as f:
        config = yaml.safe_load(f)

    model_list = config.get("model_list", [])
    if not model_list:
        print("❌ Aucun modèle dans model_list")
        sys.exit(1)

    scores = load_scores()

    # Classer chaque modèle
    classified = []
    for entry in model_list:
        model_key = entry["litellm_params"]["model"]
        group = classify(model_key, groups, patterns)

        score_entry = scores.get(model_key, {})
        avg_ms = score_entry.get("avg_response_ms")

        group_base = groups.index(group) * 1000
        in_group_order = avg_ms if avg_ms is not None else 999
        priority = group_base + in_group_order

        classified.append((priority, group, model_key, entry))

    classified.sort(key=lambda x: x[0])

    # Appliquer les priorités séquentielles
    for i, (_, group, model_key, entry) in enumerate(classified, 1):
        entry.setdefault("model_info", {})["priority"] = i

    if not dry_run:
        with open(YAML_PATH, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Rapport
    groups_used = Counter()
    for _, group, _, _ in classified:
        groups_used[group] += 1

    action = "⚠️  SIMULATION (dry-run)" if dry_run else "✅"
    print(f"{action} {YAML_PATH.name} — {len(classified)} modèles")
    for name in groups:
        count = groups_used.get(name, 0)
        if count > 0:
            _, cd = patterns[name]
            print(f"   {name}: {count} modèles (cooldown={cd}s)")
    print(f"\n   Premier : {classified[0][2]}")
    print(f"   Dernier : {classified[-1][2]}")

    # Vérifier si tout est classé "other"
    last_group = groups[-1]
    if groups_used.get(last_group, 0) == len(classified):
        print(f"⚠️  Tous les modèles sont classés '{last_group}' — les patterns capturent peut-être trop large")


if __name__ == "__main__":
    prefs_path = None
    dry_run = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--prefs" and i + 1 < len(args):
            prefs_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            print(f"⚠️  Argument ignoré : {args[i]}")
            i += 1

    run(prefs_path, dry_run)
