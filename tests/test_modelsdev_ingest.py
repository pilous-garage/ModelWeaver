"""test_modelsdev_ingest — téléverseur models.dev → buffer → local.

Lancement :
    python3 -m pytest tests/test_modelsdev_ingest.py -q

Couvre :
  - fetch depuis un snapshot JSON local (pas de réseau)
  - parse : split ids (:free, @eu, hf:org/…), official vs provider-prefixed,
    typekeys, costs — et log_error_modeldev pour toute info non transformable
    (clés modèle inconnues, limit non numérique, experimental mal formé…)
  - build des ops : quadruple + tags par type, réconciliation model_official_id
  - round-trip réel (fixture volontairement minimale) : seed → push buffer →
    consume → données + tags + gardes en BDD
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

MW_HOME = Path(tempfile.mkdtemp()) / "mw"
os.environ["MODELWEAVER_HOME"] = str(MW_HOME)

from modules.sqlite.base import WriteDenied  # noqa: E402
from modules.sqlite import buffer as B  # noqa: E402
from modules.sqlite import local as L  # noqa: E402
from modules.sqlite.local import catalogue_types as CT  # noqa: E402
from modules.sqlite.local import data_table, read as lread  # noqa: E402
from modules.ingest import modelsdev as MD  # noqa: E402

FIXTURE = {
    "openai": {
        "id": "openai", "name": "OpenAI",
        "npm": "@ai-sdk/openai", "api": "@ai-sdk/openai",
        "models": {
            "gpt-4o": {
                "id": "gpt-4o", "name": "GPT-4o", "family": "gpt",
                "attachment": True, "reasoning": False, "tool_call": True,
                "structured_output": True, "temperature": True,
                "knowledge": "2023-09", "release_date": "2024-05-13",
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "open_weights": False,
                "limit": {"context": 128000, "output": 16384},
                "cost": {"input": 2.5, "output": 10, "cache_read": 1.25},
            },
            "gpt-oss:120b": {
                "id": "gpt-oss:120b", "name": "GPT-OSS 120B",
                "reasoning": True, "reasoning_options": [{"type": "toggle"}],
                "interleaved": {"field": "reasoning_details"},
                "limit": {"context": 400000, "input": 272000,
                          "output": 128000},
                "cost": {"input": 0, "output": 0, "tiers": [
                    {"input": 1.5, "output": 5, "tier": {"type": "context",
                                                         "size": 128000}}]},
                "experimental": {"modes": {"fast": {
                    "cost": {"input": 1, "output": 2},
                    "provider": {"body": {"service_tier": "priority"},
                                 "headers": {"x-foo": "bar"}}}}},
                "totally_unknown_field": "x",   # → WARN (log)
            },
        },
    },
    "openrouter": {
        "id": "openrouter", "name": "OpenRouter", "npm": "@openrouter/ai-sdk",
        "api": "@ai-sdk/openai-compatible",
        "models": {
            "openai/gpt-4o:free": {
                "id": "openai/gpt-4o:free", "name": "GPT-4o (free)",
                "description": "free tier",
                "limit": {"context": 128000, "output": 16384,
                          "context_max": 999},   # → WARN (limit inconnue)
                "cost": {"input": 0, "output": 0},
            },
            "openai/gpt-4o@eu": {
                "id": "openai/gpt-4o@eu", "name": "GPT-4o (EU)",
                "limit": {"context": 128000, "output": 16384, "input": 1000},
                "provider": {"npm": "@ai-sdk/openai", "shape": "completions",
                             "api": "https://eu.example/v1"},
                "cost": {"input": 2.5, "output": 10, "cache_read": 1.25,
                         "cache_write": 3},
            },
            "hf:Qwen/Qwen3.6-27B": {
                "id": "hf:Qwen/Qwen3.6-27B", "name": "Qwen3.6 27B",
                "limit": {"context": 36000, "output": 8000, "input": "?"},
                "cost": {"input": 0.2, "output": 0.8},
            },
        },
    },
}

NS = MD.NS


@pytest.fixture(scope="module")
def domains():
    shutil.rmtree(MW_HOME, ignore_errors=True)
    lw = L.get_writer(L.WRITE_CATALOGUE_TOKEN)
    bw = B.get_writer(B.WRITE_BUFFER_TOKEN)
    CT.seed_catalogue_types(lw, token="write_catalogue")
    yield {"local": lw, "buffer": bw}
    lw.close()
    bw.close()


# ── split des ids ───────────────────────────────────────────

def test_split_id():
    assert MD.split_id("openai/gpt-oss-20b:free") == \
        ("openai/gpt-oss-20b", "free", "")
    assert MD.split_id("gpt-oss:120b") == ("gpt-oss", "120b", "")
    assert MD.split_id("gpt-4.1@eu") == ("gpt-4.1", "", "eu")
    assert MD.split_id("openai/gpt-5.6-luna@eu") == \
        ("openai/gpt-5.6-luna", "", "eu")
    assert MD.split_id("@cf/qwen/qwen3.8-27b") == \
        ("@cf/qwen/qwen3.8-27b", "", ""), "le @cf de workers n'est pas une région"
    assert MD.split_id("hf:Qwen/Qwen3.6-27B") == \
        ("hf:Qwen/Qwen3.6-27B", "", "")
    assert MD.split_id("gpt-4o") == ("gpt-4o", "", "")


def test_official_base():
    assert MD.official_base("openai/gpt-4o") == "gpt-4o"
    assert MD.official_base("gpt-4o") == "gpt-4o"
    assert MD.official_base("hf:Qwen/Qwen3.6-27B") == "Qwen3.6-27B"
    assert MD.official_base("z-ai/glm-5.2-free") == "glm-5.2"
    assert MD.official_base("openai/gpt-oss-20b", "free") == "gpt-oss-20b"


# ── parse + logs ────────────────────────────────────────────

def test_parse_fixture():
    log = MD.IngestLog()
    r = MD.parse(FIXTURE, log)
    assert set(r["providers"]) == {"openai", "openrouter"}
    # official : les ids SANS '/' (gpt-4o ET gpt-oss:120b chez openai)
    assert set(r["officials"]) == {"gpt-4o", "gpt-oss"}
    assert r["officials"]["gpt-4o"]["family"] == "gpt"
    assert len(r["mpes"]) == 5
    by = {m["raw_id"]: m for m in r["mpes"]}
    assert by["openai/gpt-4o:free"]["typekey"] == "free"
    assert by["openai/gpt-4o:free"]["region"] == ""
    assert by["openai/gpt-4o@eu"]["region"] == "eu"
    assert by["openai/gpt-4o@eu"]["typekey"] == ""
    # hf:… pas découpé en typekey
    assert by["hf:Qwen/Qwen3.6-27B"]["typekey"] == ""
    # typekeys par provider
    assert set(r["typekeys"]["openai"]) == {"default", "120b"}
    assert set(r["typekeys"]["openrouter"]) == {"default", "free"}
    assert r["typekeys"]["openrouter"]["free"]["free_tier"] is True
    # costs : présents pour les 5 ; tiers conservés (typekey = 120b)
    assert "tiers" in r["costs"][("openai", "gpt-oss", "120b")]
    # logs : champs non traités → ERREUR (règle champ→traitement)
    msgs = "\n".join(log.lines)
    assert "totally_unknown_field" in msgs
    assert log.errors >= 3, "champs non mappés sortent en ERROR"
    assert "ERROR" in msgs and "champ non traité" in msgs


# ── build des ops ───────────────────────────────────────────

def test_build_ops(domains):
    lw = domains["local"]
    log = MD.IngestLog()
    records = MD.parse(FIXTURE, log)
    ops = MD.build_ops(records, "2026-08-18", local_ro=lw, log=log)
    kinds = {}
    for o in ops:
        kinds[o["domain"]] = kinds.get(o["domain"], 0) + 1
    assert kinds == {"provider": 2, "endpoint": 2, "model_official": 2,
                     "model_provider_endpoint": 5,
                     "model_provider_endpoint_typekey": 5,
                     "provider_typekey": 4}, kinds
    official = next(o for o in ops if o["domain"] == "model_official"
                    and o["payload"]["name"] == "gpt-4o")
    tags = official["payload"]["tags"]
    assert tags["family"] == "gpt"
    assert tags["capability"] == ["attachment", "tool_call",
                                  "structured_output", "temperature"]
    assert tags["modalities_input"] == ["text", "image"]
    oss = next(o for o in ops if o["domain"] == "model_official"
               and o["payload"]["name"] == "gpt-oss")
    ot = oss["payload"]["tags"]
    assert ot["reasoning_options"] == [{"type": "toggle"}]
    assert ot["interleaved"] == '{"field": "reasoning_details"}'
    mpe_free = next(o for o in ops
                    if o["payload"]["name"] == "openrouter/openai/gpt-4o#free")
    t = mpe_free["payload"]["tags"]
    assert t["free_tier"] is True and t["typekey"] == "free"
    mpe_eu = next(o for o in ops
                  if o["payload"]["name"] == "openrouter/openai/gpt-4o#eu")
    t = mpe_eu["payload"]["tags"]
    assert t["region"] == "eu" and t["route_shape"] == "completions"
    assert t["route_npm"] == "@ai-sdk/openai"
    assert mpe_eu["payload"]["model_official_id"] > 0, "réconciliation"
    assert "cache_write_per_1m" not in t, "cache_write = MPET, pas MPE"
    mpet = next(o for o in ops
                if o["domain"] == "model_provider_endpoint_typekey"
                and o["payload"]["name"] == "openrouter/openai/gpt-4o#default")
    assert mpet["payload"]["tags"]["cache_write_per_1m"] == 3
    tiers = next(o for o in ops
                 if o["domain"] == "model_provider_endpoint_typekey"
                 and o["payload"]["name"] == "openai/gpt-oss#120b")
    assert tiers["payload"]["tags"]["cost_tiers"][0]["tier"]["size"] == 128000
    assert tiers["payload"]["tags"]["free_tier"] is True  # input == 0
    hf = next(o for o in ops
              if o["payload"]["name"] == "openrouter/hf#Qwen/Qwen3.6-27B")
    assert hf["payload"]["tags"].get("typekey") is None


# ── round-trip : buffer → local ─────────────────────────────

def test_round_trip(domains):
    lw, bw = domains["local"], domains["buffer"]
    log = MD.IngestLog()
    records = MD.parse(FIXTURE, log)
    ops = MD.build_ops(records, "2026-08-18", local_ro=lw, log=log)
    res = MD.push(bw, ops, batch=7, token="write_buffer")
    assert res["pushed"] == len(ops)
    from modules.sqlite.local import write as lwrite
    cons = lwrite.import_local(bw, lw, token="write_catalogue")
    assert cons["applied"] == len(ops) and cons["errors"] == 0, cons
    assert bw.table("buffer_op").count({"status": "applied"}) == 0
    # données en place (lecture par quadruple — les names avec '/' ou '#'
    # ne sont pas adressables via l'accessor ; lecture BDD directe)
    ro = L.db_ro()
    mo = data_table.get_table(ro, "model_official")
    e = mo.get("catalogue.model_official.catalogue.llm.model.gpt-4o"
               ":models.dev@newest")
    assert e["value"] and e["source_type"] == "distant"
    mpes = ro.sql("SELECT * FROM model_provider_endpoint_data "
                  "WHERE name='openrouter/openai/gpt-4o#free'")
    assert mpes and mpes[0]["model_official_id"] > 0, "réconciliation ronde"
    oid = mpes[0]["model_official_id"]
    official = ro.sql("SELECT * FROM model_official_data WHERE data_id=?",
                      (oid,))
    assert official[0]["name"] == "gpt-4o", "model_official_id pointe l'officiel"
    ctxs = ro.sql("SELECT * FROM model_provider_endpoint_data "
                  "WHERE name LIKE '%/gpt-4o%'")
    assert {c["name"] for c in ctxs} == {
        "openrouter/openai/gpt-4o#free", "openai/gpt-4o",
        "openrouter/openai/gpt-4o#eu"}
    tags = ro.sql("SELECT tag_type, tag_value FROM model_provider_endpoint_tag "
                  "WHERE data_id=(SELECT data_id FROM "
                  "model_provider_endpoint_data WHERE name='openrouter/openai/gpt-4o#eu'"
                  ")")
    tv = {t["tag_type"]: t["tag_value"] for t in tags}
    assert tv["region"] == "eu" and tv["route_shape"] == "completions"
    mpets = ro.sql("SELECT * FROM model_provider_endpoint_typekey_data "
                   "WHERE name='openai/gpt-oss#120b'")
    assert mpets and json.loads(mpets[0]["value"])["input"] == 0
    tk_tags = ro.sql("SELECT tag_type, tag_value FROM "
                     "model_provider_endpoint_typekey_tag WHERE data_id=?",
                     (mpets[0]["data_id"],))
    ttv = {t["tag_type"]: t["tag_value"] for t in tk_tags}
    assert "cost_tiers" in ttv and ttv["free_tier"] == "True"
    ro.close()
    # garde : le push doit venir du writer buffer (pas d'écriture du importeur
    # avec un token quelconque)
    with pytest.raises(WriteDenied):
        MD.push(bw, ops[:1], token="mauvais")


# ── logs écrits sur disque ──────────────────────────────────

def test_log_file(domains):
    log = MD.IngestLog()
    log.error("essai erreur")
    log.warn("essai warning")
    log.done()
    assert "log_error_modeldev.log" in log.path.name
    content = log.path.read_text(encoding="utf-8")
    assert "ERROR | essai erreur" in content
    assert "WARN | essai warning" in content