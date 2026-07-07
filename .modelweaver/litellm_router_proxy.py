#!/usr/bin/env python3
"""Proxy multi-providers :
- Contexte projet auto-injecté (gitingest)
- Budgets par modèle (fallback si trop long)
- Signature [Répondu par : X]
- Streaming
"""

import os, logging, yaml, asyncio, re, json, time, uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn
from litellm import acompletion
from gitingest import ingest_async as gitingest_ingest

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("proxy")

# === CHARGEMENT CONFIG ===
config_path = os.path.join(os.path.dirname(__file__), "litellm_config.yaml")
with open(config_path) as f:
    config = yaml.safe_load(f)

model_list = config.get("model_list", [])
for m in model_list:
    m["_priority"] = m.get("model_info", {}).get("priority", 999)
model_list.sort(key=lambda x: (x["_priority"], x["model_name"]))

from collections import OrderedDict
groups = OrderedDict()
for m in model_list:
    groups.setdefault(m["model_name"], []).append(m)

# Budgets par modèle (chars max de contexte)
model_budgets = config.get("model_budgets", {})

# === GESTIONNAIRE DE CONTEXTE (gitingest) ===
ctx_settings = config.get("context_settings", {})
CTX_ENABLED = ctx_settings.get("enabled", False)
CTX_ROOT = ctx_settings.get("project_root", ".")
CTX_REFRESH = ctx_settings.get("refresh_interval", 60)
CTX_MAX_CHARS = ctx_settings.get("max_context_chars", 100000)
CTX_EXCLUDE = ctx_settings.get("exclude_patterns", None)
CTX_MAX_FILE = ctx_settings.get("max_file_size", 50000)

class ProjectContext:
    def __init__(self):
        self.summary = ""
        self.tree = ""
        self.content = ""
        self._cache_time = 0

    async def refresh(self):
        if not CTX_ENABLED:
            return
        now = time.time()
        if now - self._cache_time < CTX_REFRESH:
            return
        try:
            logger.info(f"🔄 Rafraîchissement contexte projet: {CTX_ROOT}")
            summary, tree, content = await asyncio.wait_for(
                gitingest_ingest(
                    CTX_ROOT,
                    max_file_size=CTX_MAX_FILE,
                    exclude_patterns=CTX_EXCLUDE,
                ),
                timeout=30,
            )
            self.summary = summary
            self.tree = tree
            self.content = content
            self._cache_time = now
            logger.info(f"✅ Contexte: {len(tree)} chars tree, {len(content)} chars content")
        except Exception as e:
            logger.warning(f"⚠️  Échec gitingest: {e}")

    def build_context(self, budget_chars: int = CTX_MAX_CHARS) -> str:
        """Construit un contexte compact dans la limite budget_chars."""
        parts = []
        if self.tree:
            parts.append(f"Arborescence du projet:\n{self.tree}")
        if self.summary:
            parts.append(f"Résumé: {self.summary}")
        combined = "\n\n".join(parts)

        # Ajouter le contenu des fichiers si la place le permet
        remaining = budget_chars - len(combined) - 200
        if remaining > 500 and self.content:
            content_trunc = self.content[:remaining]
            parts.append(f"Contenu des fichiers:\n{content_trunc}")
            combined = "\n\n".join(parts)

        # Forcer la limite absolue
        if len(combined) > budget_chars:
            combined = combined[:budget_chars] + "\n\n[...tronqué]"

        return combined

project_ctx = ProjectContext()

@asynccontextmanager
async def lifespan(app):
    if CTX_ENABLED:
        logger.info("🔄 Pré-chargement du contexte projet...")
        await project_ctx.refresh()
    yield

app = FastAPI(title="Multi-Provider Proxy", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ChatRequest(BaseModel):
    model: str
    messages: list
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False

@app.get("/health")
@app.get("/v1/health")
async def health():
    return {"status": "healthy"}

@app.get("/models")
@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": k, "object": "model"} for k in groups]}

def add_signature(content: str | None, model_id: str) -> str:
    if not content:
        content = ""
    content = re.sub(r'\n*\[Répondu par : [^\]]+\]', '', content)
    return content.rstrip() + f"\n\n[Répondu par : {model_id}]"

def count_chars(msgs: list) -> int:
    return sum(len(m.get("content", "")) for m in msgs)

def inject_context(msgs: list, ctx_text: str) -> list:
    """Injecte le contexte projet dans les messages."""
    msgs = [dict(m) for m in msgs]  # copie
    sys_idx = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
    ctx_block = f"[Contexte du projet]\n{ctx_text}"
    if sys_idx is not None:
        msgs.insert(sys_idx + 1, {"role": "system", "content": ctx_block})
    else:
        msgs.insert(0, {"role": "system", "content": ctx_block})
    return msgs

async def try_deployments(req: ChatRequest, raw_msgs: list, ctx_text: str):
    deployments = groups.get(req.model)
    if not deployments:
        raise HTTPException(404, f"Modèle '{req.model}' inconnu")

    user_chars = count_chars(raw_msgs)
    errors = []

    for dep in deployments:
        model_id = dep.get("model_info", {}).get("id", "?")
        actual_model = dep["litellm_params"]["model"]
        api_key = dep["litellm_params"]["api_key"]
        budget = model_budgets.get(model_id, 200000)

        # Tronquer le contexte pour ce modèle
        ctx_for_model = ctx_text
        ctx_budget = budget - user_chars - 200  # réserve pour le message système
        if ctx_budget < 200:
            logger.warning(f"⚠️  {model_id}: budget trop petit ({budget} chars pour {user_chars} de user)")
            errors.append({"id": model_id, "error": f"budget insuffisant ({budget} chars)"})
            continue
        if len(ctx_for_model) > ctx_budget:
            ctx_for_model = ctx_for_model[:ctx_budget] + "\n\n[...tronqué]"

        msgs = inject_context(raw_msgs, ctx_for_model)
        total = count_chars(msgs)
        logger.info(f"Trying {model_id} ({actual_model}) [budget: {budget} chars, contexte: {len(ctx_for_model)}, total: {total}]")

        try:
            response = await asyncio.wait_for(
                acompletion(
                    model=actual_model,
                    messages=msgs,
                    api_key=api_key,
                    max_tokens=req.max_tokens or min(8192, budget // 4),
                    temperature=req.temperature,
                ),
                timeout=30,
            )
            resp = response.model_dump() if hasattr(response, "model_dump") else response
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content or not content.strip():
                logger.warning(f"⚠️  {model_id}: réponse vide, essai suivant")
                errors.append({"id": model_id, "error": "réponse vide"})
                continue
            logger.info(f"✅ {model_id} a répondu ({len(content)} chars)")
            return resp, model_id, errors, total

        except asyncio.TimeoutError:
            logger.warning(f"⏱️  {model_id}: timeout")
            errors.append({"id": model_id, "error": "timeout (30s)"})
        except Exception as e:
            msg = str(e)[:200]
            logger.warning(f"❌ {model_id}: {msg}")
            errors.append({"id": model_id, "error": msg})

    raise HTTPException(502, {
        "error": "Tous les modèles ont échoué",
        "attempted": [e["id"] for e in errors],
    })

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    await project_ctx.refresh()

    raw_msgs = [m.model_dump() if hasattr(m, "model_dump") else m for m in req.messages]

    # Construire le contexte projet (une seule fois, taille max)
    ctx_text = ""
    if CTX_ENABLED and CTX_ROOT:
        ctx_text = project_ctx.build_context(budget_chars=CTX_MAX_CHARS)

    if req.stream:
        return await stream_response(req, raw_msgs, ctx_text)

    resp, model_id, errors, total = await try_deployments(req, raw_msgs, ctx_text)
    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    resp["choices"][0]["message"]["content"] = add_signature(content, model_id)
    resp["_responded"] = model_id
    resp["_eliminated"] = [e["id"] for e in errors]
    resp["_context_chars"] = total
    return resp

async def stream_response(req: ChatRequest, raw_msgs: list, ctx_text: str):
    resp, model_id, errors, total = await try_deployments(req, raw_msgs, ctx_text)
    content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    signed = add_signature(content, model_id)
    resp_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async def generate():
        sig_index = signed.rfind("[Répondu par :")
        main_content = signed[:sig_index].rstrip() if sig_index > 0 else signed
        sig_part = signed[sig_index:] if sig_index > 0 else ""

        if main_content:
            yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {'content': main_content}, 'finish_reason': None}]})}\n\n"
        if sig_part:
            yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {'content': sig_part}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': resp_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
