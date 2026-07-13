#!/usr/bin/env bash
# =============================================================================
# install-dependencies-ubuntu24.sh
# ARTEFACT COMPILÉ (généré depuis modules/system/deps_manifest.json pour la
# cible ubuntu24). Ne pas éditer à la main : régénérer depuis le manifeste.
#
# Installe les dépendances REQUISES (safe + light) du projet.
#   --include-optional  -> ajoute aussi les deps heavy/unsafe (litellm, docker)
# Échoue explicitement si une dépendance ne s'installe pas.
# =============================================================================
set -euo pipefail

INCLUDE_OPTIONAL=false
for a in "$@"; do
  case "$a" in
    --include-optional) INCLUDE_OPTIONAL=true ;;
    *) echo "argument inconnu: $a" >&2; exit 2 ;;
  esac
done

# Privilège : sudo si non-root
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

echo "[deps] cible: ubuntu24"
echo "[deps] apt-get update..."
$SUDO apt-get update -qq

# ── system (apt) : safe + light requises ──
APT_REQUIRED=(python3 python3-pip sqlite3)
echo "[deps] apt install (requis): ${APT_REQUIRED[*]}"
$SUDO apt-get install -y "${APT_REQUIRED[@]}"

# Empêche pip de mettre à jour les paquets possédés par Debian (ex:
# importlib-metadata -> tire zipp>=3.20, mais zipp 1.0.0 est fourni par Debian
# et sans RECORD -> "Cannot uninstall zipp ... RECORD file not found"). On
# épingle ces paquets à leur version installée : le résolveur choisit alors des
# versions compatibles (comportement identique à une install propre).
build_pip_constraints() {
  local f="$1"
  : > "$f"
  for pkg in importlib-metadata zipp; do
    local v
    v="$(python3 -m pip show "$pkg" 2>/dev/null | awk -F': ' '/^Version:/ {print $2}')"
    if [ -n "$v" ]; then echo "${pkg}==${v}" >> "$f"; fi
  done
}

CONS_FILE="$(mktemp)"
build_pip_constraints "$CONS_FILE"
PIP_CONSTRAINT_ARGS=(--constraint "$CONS_FILE")

# ── python (pip) : safe + light requises ──
PIP_REQUIRED=(keyring psutil)
echo "[deps] pip install (requis): ${PIP_REQUIRED[*]}"
python3 -m pip install "${PIP_REQUIRED[@]}" --break-system-packages "${PIP_CONSTRAINT_ARGS[@]}"

# ── optionnelles ──
# docker et litellm NE sont PAS des dépendances : ce sont des runtimes/outils
# gérés par le CATALOGUE (docker = conteneurisation, litellm = abstraction LLM).
# On ne les installe donc jamais depuis l'installeur de dépendances (notamment
# docker.io ne doit pas être installé dans un conteneur de test). Le flag
# --include-optional reste accepté (no-op) pour ne pas casser les appelants.
if $INCLUDE_OPTIONAL; then
  echo "[deps] aucune dépendance optionnelle à installer (docker/litellm sont gérés par le catalogue)"
fi

echo "[deps] installation terminée (ubuntu24)"
