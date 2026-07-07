#!/usr/bin/env bash
set -euo pipefail

export OPENAI_API_KEY="${OPENAI_API_KEY:-${OPENROUTER_API_KEY:-${GROQ_API_KEY:-${OPENCODE_ZEN_API_KEY:-dummy}}}}"
export OPENAI_API_BASE="${OPENAI_API_BASE:-http://127.0.0.1:8000/v1}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-${OPENAI_API_BASE}}"
export OPENAI_MODEL="${OPENAI_MODEL:-llama-3.3-70b-versatile}"

TRACE_FILE="/home/pierreloup2/PilousGarage/ModelWeaver/.modelweaver/route_trace.log"
FALLBACK_MODELS=("llama-3.3-70b-versatile" "llama-3.1-8b-instant" "nvidia/nemotron-3-ultra-550b-a55b:free" "nvidia/nemotron-3-super-120b-a12b:free" "openai/gpt-4o" "anthropic/claude-3.5-sonnet" "tinyllama" "deepseek-v4-flash-free" "gpt-5-nano" "nemotron-3-ultra-free")
REAL_BIN="opencode"

log_attempt() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$TRACE_FILE"
}

run_with_model() {
  model="$1"
  shift
  log_attempt "opencode attempt model=$model"
  echo "▶️  Tentative avec le modèle $model" >&2
  export OPENAI_MODEL="$model"
  output_file=$(mktemp)
  "$REAL_BIN" "$@" >"$output_file" 2>&1
  status=$?
  cat "$output_file"
  if [ $status -eq 0 ]; then
    rm -f "$output_file"
    return 0
  fi
  if grep -Eqi 'request too large|tpm|rate limit|429|too many requests|timeout|overloaded|503|502|504|connection|unexpected server error|incorrect api key|invalid api key|401|403' "$output_file"; then
    log_attempt "fallback-triggered model=$model status=$status"
    echo "⚠️  Erreur détectée, passage au modèle de secours pour $model" >&2
    rm -f "$output_file"
    return 99
  fi
  log_attempt "error-no-fallback model=$model status=$status"
  rm -f "$output_file"
  return 99
}

attempts=()
if [ -n "${OPENAI_MODEL:-}" ]; then
  attempts+=("${OPENAI_MODEL}")
fi
for candidate in "${FALLBACK_MODELS[@]}"; do
  if [ -n "$candidate" ]; then
    attempts+=("$candidate")
  fi
done

seen=()
for model in "${attempts[@]}"; do
  case " ${seen[*]} " in
    *" $model "*) continue ;;
  esac
  seen+=("$model")
  set +e
  run_with_model "$model" "$@"
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$status" -ne 99 ]; then
    exit "$status"
  fi
done

exit 1
