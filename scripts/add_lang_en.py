#!/usr/bin/env python3
"""add_lang_en.py — Ajoute langEmbeddedEn (anglais) aux panels V2.

Lit chaque .panel.tsx contenant `langEmbedded: LANG_FR`, extrait le YAML FR,
traduit les valeurs (dictionnaire FR→EN), et insère `const LANG_EN` +
`langEmbeddedEn: LANG_EN` juste après la déclaration de LANG_FR.

Usage : python3 scripts/add_lang_en.py
"""
import re
import sys
from pathlib import Path

PANELS_DIR = Path(__file__).resolve().parent.parent / "interfaces" / "main" / "GUI" / "v2" / "src" / "panels"

# Dictionnaire de traduction FR → EN (valeurs récurrentes des panels)
TRAD = {
    "Processus": "Processes", "PID": "PID", "Mémoire": "Memory", "Erreur": "Error",
    "Projets": "Projects", "Statut": "Status", "Responsables": "Leaders",
    "Workspace": "Workspace", "Nom du projet": "Project name",
    "Initialiser le workspace": "Initialize workspace", "Outils Registry": "Tools Registry",
    "Outil": "Tool", "Bundles": "Bundles", "Bundle": "Bundle", "panels": "panels",
    "Monitoring agents": "Agents monitoring", "Agents": "Agents", "Services": "Services",
    "Moniteur LLM": "LLM monitor", "Fenêtre": "Window", "Requêtes": "Requests",
    "Tokens in": "Tokens in", "Tokens out": "Tokens out", "Coût": "Cost",
    "Ressources": "Resources", "GPU": "GPU", "Réseau": "Network",
    "État système": "System state", "CPU": "CPU", "Carte mère": "Motherboard",
    "Disques": "Disks", "File d'installation": "Install queue", "Réf": "Ref",
    "Type": "Type", "Vider la file": "Clear queue", "Annuler": "Cancel",
    "Outils installés": "Installed tools", "Version": "Version", "Désinstaller": "Uninstall",
    "Dashboard": "Dashboard", "Rafraîchir": "Refresh", "Configuration": "Configuration",
    "Clés API": "API keys", "Provider": "Provider", "État": "State", "Verrou": "Lock",
    "Supprimer": "Delete", "LLM locaux": "Local LLMs", "Moteur": "Engine", "Port": "Port",
    "Mode": "Mode", "Redém.": "Restart", "Nom": "Name", "Vue": "View",
    "Statut": "Status", "Installé": "Installed", "Variant": "Variant",
    "Ressources (variant)": "Resources (variant)", "État (simple)": "State (simple)",
    "En ligne": "Online", "Services actifs": "Active services", "Slip": "Slip",
}

def translate_value(v: str) -> str:
    """Traduit une valeur simple (mot ou courte phrase)."""
    # valeur composée "X Y Z" → traduire chaque mot connu
    words = v.split(" ")
    out = []
    for w in words:
        out.append(TRAD.get(w, TRAD.get(v, v if not w else w)))
    # si la phrase entière est connue, la prendre
    if v in TRAD:
        return TRAD[v]
    joined = " ".join(out)
    if joined == v:
        return v
    return joined

def translate_yaml_fr(fr_yaml: str) -> str:
    """Traduit le YAML FR (clés conservées, valeurs traduites)."""
    lines = fr_yaml.strip().splitlines()
    out = []
    for line in lines:
        m = re.match(r'^(\s*)([a-zA-Z0-9_]+):\s*"?(.*?)"?\s*$', line)
        if m and '"' in line:
            indent, key, val = m.group(1), m.group(2), m.group(3)
            out.append(f'{indent}{key}: "{translate_value(val)}"')
        else:
            out.append(line)
    return "\n".join(out)

def process_file(path: Path) -> bool:
    src = path.read_text(encoding="utf-8")
    if "langEmbedded: LANG_FR" not in src or "langEmbeddedEn" in src:
        return False
    # extraire le bloc LANG_FR
    m = re.search(r'const LANG_FR = `\n(.*?)`;', src, re.S)
    if not m:
        return False
    fr_yaml = m.group(1)
    en_yaml = translate_yaml_fr(fr_yaml)
    # construire le bloc EN
    en_block = f'const LANG_EN = `\n{en_yaml}\n`;\n'
    # insérer LANG_EN après la fin de LANG_FR
    src = src.replace(m.group(0), m.group(0) + "\n\n" + en_block, 1)
    # insérer langEmbeddedEn après langEmbedded
    src = src.replace("langEmbedded: LANG_FR,", "langEmbedded: LANG_FR,\n  langEmbeddedEn: LANG_EN,", 1)
    path.write_text(src, encoding="utf-8")
    return True

def main():
    n = 0
    for f in sorted(PANELS_DIR.glob("*.panel.tsx")):
        if process_file(f):
            print(f"  + {f.name}")
            n += 1
    print(f"{n} panels enrichis en anglais")

if __name__ == "__main__":
    main()
