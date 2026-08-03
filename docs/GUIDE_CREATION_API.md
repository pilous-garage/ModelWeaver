# Guide à la création des API

## Objectif

Aider les utilisateurs à créer et configurer des clés API pour les différents providers supportés par ModelWeaver, étape par étape, comme si un bot les guidait.

## Principe

L'utilisateur est accompagné par un overlay navigateur qui :
1. Identifie la page où créer la clé API
2. Guide le curseur vers le bon bouton/champ
3. Explique chaque étape en langage simple
4. Valide la clé une fois créée
5. Teste la clé automatiquement

## Providers supportés et pages de création

| Provider | Page de création | Format clé |
|----------|-----------------|------------|
| OpenAI | https://platform.openai.com/api-keys | `sk-...` |
| Anthropic | https://console.anthropic.com/settings/keys | `sk-ant-...` |
| Google | https://aistudio.google.com/app/api-keys | `AIza...` |
| Mistral | https://console.mistral.ai/api-keys | `mistral-...` |
| Ollama Cloud | https://ollama.com/settings/keys | `<hex>.<hex>` |
| Groq | https://console.groq.com/api-keys | `gsk_...` |
| OpenRouter | https://openrouter.ai/settings/keys | `sk-or-v1-...` |
| NVIDIA | https://build.nvidia.com | `nvapi-...` |
| HuggingFace | https://huggingface.co/settings/tokens | `hf_...` |

## Étapes types de création

1. **Se connecter** au provider (login/mot de passe)
2. **Naviguer** vers la page des clés API (lien direct fourni)
3. **Cliquer** sur "Create new key" / "Generate"
4. **Nommer** la clé (ex. `modelweaver-cloud`)
5. **Sélectionner** les permissions/accès nécessaires
6. **Copier** la clé (ctrl+c)
7. **Coller** dans ModelWeaver (ctrl+v)
8. **Tester** la clé automatiquement

## Actions de navigation guidée

- **Click** : survoler et cliquer sur un élément
- **Type** : taper du texte dans un champ
- **Copy/Paste** : copier-coller la clé
- **Scroll** : descendre jusqu'à un bouton
- **Wait** : attendre qu'une page charge
- **Verify** : vérifier qu'un élément est présent

## Intégration ModelWeaver

Le guide est accessible via :
- L'interface web ModelWeaver (overlay navigateur)
- La commande CLI `modelweaver setup`
- Le plugin OpenCode `/connect`

## Sécurité

- Les clés API ne sont jamais stockées en clair
- Elles sont chiffrées dans la base de données locale
- L'utilisateur peut révoquer une clé à tout moment depuis le dashboard du provider