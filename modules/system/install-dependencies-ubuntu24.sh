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

# ── python (pip) : safe + light requises ──
PIP_REQUIRED=(keyring psutil)
echo "[deps] pip install (requis): ${PIP_REQUIRED[*]}"
python3 -m pip install "${PIP_REQUIRED[@]}" --break-system-packages

# ── optionnelles (heavy / unsafe) ──
if $INCLUDE_OPTIONAL; then
  echo "[deps] apt install (optionnel): docker.io"
  $SUDO apt-get install -y docker.io
  echo "[deps] pip install (optionnel): litellm"
  python3 -m pip install litellm --break-system-packages
fi

echo "[deps] installation terminée (ubuntu24)"
