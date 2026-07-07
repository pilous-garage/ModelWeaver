#!/bin/bash
# Test la connectivité de tous les modèles via LiteLLM
# Usage: ./tests/test_model_connectivity.sh [--analyze]
#   --analyze : en cas d'erreur ambiguë, utilise Ollama pour analyser la réponse
# NOTE: Considérez plutôt tests/test_model_connectivity.py (parallélisé, JSON, backoff)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
LITELLM_CONFIG="$SCRIPT_DIR/.modelweaver/litellm_config.json"
ROUTE_TRACE="$SCRIPT_DIR/.modelweaver/route_trace.log"
LITELLM_LOG="$SCRIPT_DIR/.modelweaver/litellm.log"
ANALYZE="${1:-}"

LITELLM_URL="http://127.0.0.1:8000"
OLLAMA_URL="http://127.0.0.1:11434"

# Couleurs
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Source les clés API
if [ -f "$ENV_FILE" ]; then
    export $(grep -v "^#" "$ENV_FILE" | grep -v "^\s*$" | xargs)
fi

# Fonction pour logger
log_event() {
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "[$ts] $1" >> "$ROUTE_TRACE"
}

# Fonction probe
_probe() {
    curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 3 -m 5 "$1" 2>/dev/null || echo "000"
}

# Démarre les services si besoin
ensure_services() {
    local started=false

    if [ "$(_probe "$OLLAMA_URL/api/tags")" = "000" ]; then
        echo "   🚀  Démarrage d'Ollama..."
        ollama serve > /dev/null 2>&1 &
        sleep 3
        if [ "$(_probe "$OLLAMA_URL/api/tags")" = "000" ]; then
            echo -e "   ${RED}❌ Ollama impossible à démarrer${NC}"
        else
            echo -e "   ${GREEN}✅ Ollama démarré${NC}"
            started=true
        fi
    fi

    if [ "$(_probe "$LITELLM_URL/health/readiness")" = "000" ]; then
        echo "   🚀  Démarrage de LiteLLM..."
        if [ -f "$LITELLM_CONFIG" ]; then
            litellm --host 127.0.0.1 --port 8000 --config "$SCRIPT_DIR/.modelweaver/litellm_config.yaml" > "$LITELLM_LOG" 2>&1 &
            sleep 6
        fi
        if [ "$(_probe "$LITELLM_URL/health/readiness")" = "000" ]; then
            echo -e "   ${RED}❌ LiteLLM impossible à démarrer${NC}"
        else
            echo -e "   ${GREEN}✅ LiteLLM démarré${NC}"
            started=true
        fi
    fi

    if [ "$started" = false ]; then
        echo -e "   ${GREEN}✅ Services déjà actifs${NC}"
    fi
}

# Récupère la liste des modèles depuis la config LiteLLM
get_models() {
    if [ ! -f "$LITELLM_CONFIG" ]; then
        echo "[]"
        return
    fi
    python3 -c "
import json
with open('$LITELLM_CONFIG') as f:
    data = json.load(f)
models = [m['model_name'] for m in data.get('model_list', [])]
print(json.dumps(models))
"
}

# Test un modèle
test_model() {
    local model="$1"
    local prompt="Réponds en un mot : 2+2 ="

    local response
    response=$(curl -s -m 30 -X POST "$LITELLM_URL/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"$model\", \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}], \"max_tokens\": 10}" 2>&1)

    local status="$?"
    if [ "$status" -ne 0 ]; then
        echo "❌ TIMEOUT|timeout après 30s"
        log_event "TIMEOUT model=$model"
        return 1
    fi

    local content
    local error_msg
    content=$(echo "$response" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    if 'choices' in d and d['choices']:
        print('OK:' + d['choices'][0]['message']['content'][:80].replace(chr(10),' '))
    elif 'error' in d:
        print('ERR:' + str(d['error']['message'])[:120].replace(chr(10),' '))
    else:
        print('UNK:' + str(d)[:120])
except:
    print('PARSE_ERROR')
" 2>&1)

    case "$content" in
        OK:*)
            local msg="${content#OK:}"
            echo -e "✅ SUCCESS|$msg"
            log_event "SUCCESS model=$model"
            return 0
            ;;
        ERR:*)
            local msg="${content#ERR:}"
            echo -e "❌ $msg"
            log_event "FAIL model=$model error=$msg"
            return 1
            ;;
        UNK:*)
            local msg="${content#UNK:}"
            echo -e "⚠️  $msg"
            log_event "UNKNOWN model=$model response=$msg"
            return 2
            ;;
        *)
            echo -e "❌ Erreur de parsing"
            log_event "PARSE_ERROR model=$model"
            return 2
            ;;
    esac
}

# Analyse une erreur avec Ollama (option --analyze)
analyze_error() {
    local error_text="$1"
    if [ ! -f "$LITELLM_CONFIG" ]; then
        echo "   ℹ️  Pas de config LiteLLM, ignore l'analyse"
        return
    fi

    local has_ollama
    has_ollama=$(echo "$(get_models)" | python3 -c "import json,sys; ms=json.load(sys.stdin); print(any('tinyllama' in m for m in ms))" 2>/dev/null || echo "False")

    if [ "$has_ollama" = "True" ] && [ "$(_probe "$OLLAMA_URL/api/tags")" != "000" ]; then
        echo "   🔍  Analyse avec Ollama..."
        local analysis
        analysis=$(curl -s -m 30 -X POST "$LITELLM_URL/v1/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{\"model\": \"tinyllama\", \"messages\": [{\"role\": \"user\", \"content\": \"Analyze this API error and suggest a fix: $error_text. Reply in one sentence.\"}], \"max_tokens\": 50}" 2>&1 | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
    if 'choices' in d and d['choices']:
        print(d['choices'][0]['message']['content'][:150])
    else:
        print('(analyse non disponible)')
except:
    print('(erreur analyse)')
" 2>/dev/null)
        echo "   💡  $analysis"
    fi
}

# ─── Main ─────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🔌  Test de connectivité des modèles"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

ensure_services

echo ""
echo "📋  Modèles configurés :"
models_json=$(get_models)
models=()
while IFS= read -r m; do
    models+=("$m")
done < <(echo "$models_json" | python3 -c "import json,sys; [print(m) for m in json.load(sys.stdin)]" 2>/dev/null)

if [ ${#models[@]} -eq 0 ]; then
    echo -e "   ${RED}Aucun modèle trouvé dans $LITELLM_CONFIG${NC}"
    exit 1
fi

for m in "${models[@]}"; do
    echo "   • $m"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🧪  Test de chaque modèle..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

success=0
failed=0
unknown=0
first_success=""

for model in "${models[@]}"; do
    printf "  %-50s " "$model"
    result=$(test_model "$model" 2>&1)
    status=$?

    if echo "$result" | grep -q "^✅"; then
        echo -e "${GREEN}$result${NC}"
        success=$((success + 1))
        if [ -z "$first_success" ]; then
            first_success="$model"
        fi
    elif echo "$result" | grep -q "^⚠️"; then
        echo -e "${YELLOW}$result${NC}"
        unknown=$((unknown + 1))
        err_text=$(echo "$result" | sed 's/^⚠️  //')
        if [ "$ANALYZE" = "--analyze" ]; then
            analyze_error "$err_text"
        fi
    else
        echo -e "${RED}$result${NC}"
        failed=$((failed + 1))
        err_text=$(echo "$result" | cut -d'|' -f2-)
        if [ "$ANALYZE" = "--analyze" ]; then
            analyze_error "$err_text"
        fi
    fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📊  Résumé"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}✅ Succès  : $success${NC}"
echo -e "  ${RED}❌ Échecs  : $failed${NC}"
echo -e "  ${YELLOW}⚠️  Inconnus : $unknown${NC}"
echo "  Total    : $((success + failed + unknown))"

if [ -n "$first_success" ]; then
    echo ""
    echo -e "  🏆  Premier modèle fonctionnel : ${GREEN}$first_success${NC}"
fi

if [ "$failed" -gt 0 ]; then
    echo ""
    echo "  💡  Astuce : relancez avec --analyze pour une analyse Ollama des erreurs"
fi

echo ""
echo "  📝  Trace : $ROUTE_TRACE"
echo ""
exit $failed
