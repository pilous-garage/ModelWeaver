"""test_local_buffer_v4 — smoke du domaine local v4 (quad + sélecteurs +
gardes) et du domaine buffer (dépôt + consumer IN du writer local).

Lancement :
    python3 -m pytest tests/test_local_buffer_v4.py -q

Couvre :
  - schéma v4 : sources seedées, table v2 du buffer absente
  - quadruplé UNIQUE (namespace, name, source, version), data_id par entrée
  - sélection : tag (prioritaire) → préférence source → tri version
    (newest/oldest/littérale), syntaxe catalogue.<type>.<ns>.<name>
  - gardes : modify d'une data étrangère = NOUVELLE entrée, delete interdit
    sur une source étrangère
  - consumer IN (local/write.import_local) : applied/error + purge
  - gardes token (WriteDenied) et sanitaires (catalogue → permissions)
"""

import os
import json
import shutil
import tempfile
from pathlib import Path

import pytest

MW_HOME = Path(tempfile.mkdtemp()) / "mw"
os.environ["MODELWEAVER_HOME"] = str(MW_HOME)

from modules.sqlite.base import WriteDenied  # noqa: E402
from modules.sqlite import buffer as B  # noqa: E402
from modules.sqlite import local as L  # noqa: E402
from modules.sqlite.buffer import read as bread  # noqa: E402
from modules.sqlite.buffer import write as bwrite  # noqa: E402
from modules.sqlite.local import data_table, local as LL  # noqa: E402
from modules.sqlite.local import read as lread  # noqa: E402
from modules.sqlite.local import write as lwrite  # noqa: E402


@pytest.fixture(scope="module")
def domains():
    shutil.rmtree(MW_HOME, ignore_errors=True)
    lw = L.get_writer(L.WRITE_CATALOGUE_TOKEN)
    bw = B.get_writer(B.WRITE_BUFFER_TOKEN)
    yield {"local": lw, "buffer": bw}
    lw.close()
    bw.close()


# ── schéma v4 ────────────────────────────────────────────────

def test_schema_v4(domains):
    lw = domains["local"]
    assert lw._exists("global_local_buffer_op") is False
    srcs = lread.list_sources(lw)
    assert {s["type_source"] for s in srcs} == set(LL.SOURCE_TYPES)


# ── quadruplé + data_id par entrée ───────────────────────────

def test_quad_unique(domains):
    lw = domains["local"]
    data_table.create_new_data_type(lw, "provider", "providers test",
                                    token=L.WRITE_CATALOGUE_TOKEN)
    dt = data_table.get_table(lw, "provider")
    dt.upsert({"namespace": "models/openai", "name": "gpt4", "version": "1.0",
               "data_value_type": "json", "value": {"a": 1}},
              source="user", token=L.WRITE_CATALOGUE_TOKEN)
    dt.upsert({"namespace": "models/openai", "name": "gpt4", "version": "1.0",
               "data_value_type": "json", "value": {"a": 1}},
              source="official", token=L.WRITE_CATALOGUE_TOKEN)
    dt.upsert({"namespace": "models/openai", "name": "gpt4", "version": "2.0",
               "data_value_type": "json", "value": {"a": 2}},
              source="user", token=L.WRITE_CATALOGUE_TOKEN)
    rows = lw.table("provider_data").select(order_by="version")
    assert len(rows) == 3
    ids = {r["data_id"] for r in rows}
    assert len(ids) == 3, "un data_id PAR entrée (quad)"
    quad = LL.data_id_of("models/openai", "gpt4", rows[0]["source_id"], "1.0")
    assert quad in ids, "data_id = hash stable du quadruplé"
    # même quad re-ajouté → update en place, pas de doublon
    dt.upsert({"namespace": "models/openai", "name": "gpt4", "version": "1.0",
               "data_value_type": "json", "value": {"a": 9}},
              source="user", token=L.WRITE_CATALOGUE_TOKEN)
    assert lw.table("provider_data").count() == 3


# ── sélection (tag → source → version) ───────────────────────

def test_selection(domains):
    lw = domains["local"]
    lr = L.db_ro()
    dt = data_table.get_table(lr, "provider")
    # défaut : user>enterprise>official + newest → user 2.0
    e = dt.get("catalogue.provider.models.openai.gpt4")
    assert e["source_type"] == "user" and e["version"] == "2.0", e
    # source exacte + oldest
    e = dt.get("catalogue.provider.models.openai.gpt4:official@oldest")
    assert e["source_type"] == "official" and e["version"] == "1.0", e
    # version littérale
    e = dt.get("catalogue.provider.models.openai.gpt4:user@1.0")
    assert e["version"] == "1.0"
    # héritée ns/name
    e = dt.get("models/openai/gpt4")
    assert e["source_type"] == "user"
    # tag PRIORITAIRE : tag stable posé SUR official seulement (version 1.0)
    dto = data_table.get_table(lw, "provider")
    dto.tag_type_add("stable", "bool", token=L.WRITE_CATALOGUE_TOKEN)
    rr = lw.table("provider_data").get(
        {"data_id": LL.data_id_of("models/openai", "gpt4",
                                  LL.source_id_for(lw, "official"), "1.0")})
    dto.tag_attach("catalogue.provider.models.openai.gpt4:official@1.0",
                   "stable", True, token=L.WRITE_CATALOGUE_TOKEN)
    assert rr["data_id"]  # tag posé sur la ligne official
    e = dt.get("catalogue.provider.models.openai.gpt4:all@newest:tag(stable)")
    assert e["source_type"] == "official", "tag d'abord, même si user plus récent"
    # défaut après tag : chaîne user>…>official → official uniquement taggé
    e = dt.get("catalogue.provider.models.openai.gpt4:tag(stable)")
    assert e["source_type"] == "official"
    # chaîne de préférence explicite : user absent de la chaîne → official
    e = dt.get("catalogue.provider.models.openai.gpt4:official>enterprise@newest")
    assert e["source_type"] == "official"
    # introuvable
    with pytest.raises(KeyError):
        dt.get("catalogue.provider.models.openai.gpt4:friend@newest")
    lr.close()


# ── gardes d'écriture ────────────────────────────────────────

def test_write_guards(domains):
    lw = domains["local"]
    dt = data_table.get_table(lw, "provider")
    # modify d'une data OFFICIAL par le writer user → NOUVELLE entrée user
    # (fork : version suivante de la ligne modifiée 1.0 → 1.1)
    r = dt.modify("catalogue.provider.models.openai.gpt4:official@1.0",
                  {"value": {"patch": 1}}, source="user",
                  token=L.WRITE_CATALOGUE_TOKEN)
    assert r["source"] == "user" and r["version"] == "1.1", r
    row = lw.table("provider_data").get({"data_id": r["data_id"]})
    assert row["source_id"] == LL.source_id_for(lw, "user")
    # modify d'une data USER → update EN PLACE (identité conservée)
    before = lw.table("provider_data").get({
        "data_id": LL.data_id_of("models/openai", "gpt4",
                                 LL.source_id_for(lw, "user"), "2.0")})
    dt.modify("catalogue.provider.models.openai.gpt4:user@2.0",
              {"value": {"b": 3}}, source="user",
              token=L.WRITE_CATALOGUE_TOKEN)
    rows = lw.table("provider_data").select()
    after = lw.table("provider_data").get({"data_id": before["data_id"]})
    assert after["value"] == '{"b": 3}' and len(rows) == 4
    # delete d'une source étrangère → refus
    with pytest.raises(WriteDenied):
        dt.delete("catalogue.provider.models.openai.gpt4:official@1.0",
                  source="user", token=L.WRITE_CATALOGUE_TOKEN)
    # delete de SA source → ok (fork user 1.1)
    n = dt.delete("catalogue.provider.models.openai.gpt4:user@1.1",
                  source="user", token=L.WRITE_CATALOGUE_TOKEN)
    assert n["deleted"] == 1


# ── consumer IN (buffer → local) ─────────────────────────────

def test_import_local(domains):
    local_w, buffer_w = domains["local"], domains["buffer"]
    # importeur : push UNE batch (source official) + WriteDenied si mauvais token
    bwrite.import_ops(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": {"namespace": "models/anthropic", "name": "claude",
                     "version": "3.5", "source": "official",
                     "data_value_type": "json", "value": {"ctx": 200000}}},
        {"domain": "provider", "op": "add",
         "payload": {"namespace": "models/anthropic", "name": "claude",
                     "version": "4.0", "source": "official",
                     "data_value_type": "json", "value": {"ctx": 400000}}},
        {"domain": "provider", "op": "modify",
         "payload": {"namespace": "models/openai", "name": "gpt4",
                     "version": "2.0", "source": "official",
                     "data_value_type": "json", "value": {"x": 1}}},
    ], external_tag="models.dev", token=B.WRITE_BUFFER_TOKEN)
    with pytest.raises(WriteDenied):
        bwrite.import_ops(buffer_w, [{"domain": "provider", "op": "add",
                                "payload": {}}], token="mauvais")
    # consumer du WRITER LOCAL : applique + purge
    res = lwrite.import_local(buffer_w, local_w,
                                token=L.WRITE_CATALOGUE_TOKEN)
    assert res["applied"] == 3 and res["errors"] == 0, res
    assert res["purged"] >= 3
    assert buffer_w.table("buffer_op").count({"status": "applied"}) == 0
    # data présente, source official — et la ligne user 2.0 intacte
    lr = L.db_ro()
    dt = data_table.get_table(lr, "provider")
    e = dt.get("catalogue.provider.models.anthropic.claude:official@newest")
    assert e["version"] == "4.0" and e["source_type"] == "official"
    e2 = dt.get("catalogue.provider.models.openai.gpt4:official@newest")
    assert e2["value"] == '{"x": 1}' and e2["source_type"] == "official"
    e3 = dt.get("catalogue.provider.models.openai.gpt4:user@newest")
    assert e3["value"] == '{"b": 3}', "ligne user jamais écrasée par l'import"
    lr.close()
    # op en erreur (type inconnu) → marqué error, pas bloquant, retry→pending
    bwrite.import_ops(buffer_w, [{"domain": "nope", "op": "add",
                            "payload": {"namespace": "", "name": "x",
                                        "version": "1", "source": "official",
                                        "data_value_type": "json"}}],
                external_tag="test", token=B.WRITE_BUFFER_TOKEN)
    res = lwrite.import_local(buffer_w, local_w, purge=False,
                                token=L.WRITE_CATALOGUE_TOKEN)
    assert res["applied"] == 0 and res["errors"] == 1 and res["purged"] == 0
    assert "nope" in bread.status(buffer_w, status="error")[0]["error"]
    st = bwrite.retry(buffer_w, token=B.WRITE_BUFFER_TOKEN)
    assert bread.status(buffer_w, status="pending")
    lwrite.import_local(buffer_w, local_w, purge=False,
                          token=L.WRITE_CATALOGUE_TOKEN)  # nope → error à nouveau
    assert bread.status(buffer_w, status="error")
    # delete d'une ligne user via une op qui SE DÉCLARE official (data_id
    # cible = ligne user) → op en error (garde de source)
    uid = LL.data_id_of("models/openai", "gpt4",
                        LL.source_id_for(local_w, "user"), "2.0")
    bwrite.import_ops(buffer_w, [{"domain": "provider", "op": "delete",
                            "payload": {"data_id": uid, "source": "official"}}],
                external_tag="test", token=B.WRITE_BUFFER_TOKEN)
    res = lwrite.import_local(buffer_w, local_w,
                                token=L.WRITE_CATALOGUE_TOKEN)
    assert res["errors"] == 1, "delete d'une ligne user par une op official refusé"
    assert local_w.table("provider_data").get({"data_id": uid}) is not None


# ── import_included / export / export_local ──────────────────

def test_import_included(domains):
    """Variante STRICTE : None ⟺ la donnée porte source/version (requis)."""
    buffer_w = domains["buffer"]
    base = {"namespace": "models/included", "name": "m1",
            "data_value_type": "json", "value": {"ctx": 1000}}
    # version incluse (payload) + source incluse → ok
    r = bwrite.import_included(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": dict(base, version="1.0", source="official")}],
        token=B.WRITE_BUFFER_TOKEN)
    assert r["pushed"] == 1
    # version incluse via TIMESTAMP (pas de payload['version']) → ok
    r = bwrite.import_included(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": dict(base, name="m2", source="official",
                         timestamp="2026-01-05", value={"ctx": 1})}],
        token=B.WRITE_BUFFER_TOKEN)
    assert r["pushed"] == 1
    # source non incluse + donnée ne la porte pas → ValueError
    with pytest.raises(ValueError, match="source"):
        bwrite.import_included(buffer_w, [
            {"domain": "provider", "op": "add", "payload": dict(base)}],
            token=B.WRITE_BUFFER_TOKEN)
    # version non incluse, ni version ni timestamp → ValueError
    with pytest.raises(ValueError, match="version"):
        bwrite.import_included(buffer_w, [
            {"domain": "provider", "op": "add",
             "payload": dict(base, source="official")}],
            token=B.WRITE_BUFFER_TOKEN)
    # valeurs fournies en argument → appliquées à toutes les ops
    r = bwrite.import_included(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": dict(base, name="m3", timestamp="2026-02-02")}],
        source="official", version="2026-03-03",
        token=B.WRITE_BUFFER_TOKEN)
    assert r["pushed"] == 1
    rows = bread.status(buffer_w, status="pending")
    names = {}
    for rr in rows:
        if rr["domain"] == "provider":
            p = json.loads(rr["payload_json"])
            names[p["name"]] = (p["version"], p["source"])
    assert names["m1"] == ("1.0", "official")
    assert names["m2"] == ("2026-01-05", "official")
    assert names["m3"] == ("2026-03-03", "official")


def test_export_local_export(domains):
    """export_local (local → buffer, direction out) puis export (buffer →
    ailleurs) : filtre par source/version en ARGUMENTS, payloads complets."""
    local_w, buffer_w = domains["local"], domains["buffer"]
    # importeur : depose une data (in) pour la consommer
    bwrite.import_ops(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": {"namespace": "models/export", "name": "a",
                     "version": "2026-01-01", "source": "official",
                     "data_value_type": "json", "value": {"ctx": 1}}},
    ], token=B.WRITE_BUFFER_TOKEN)
    lwrite.import_local(buffer_w, local_w, token=L.WRITE_CATALOGUE_TOKEN)
    # le writer local pousse SES modifs vers le buffer (direction out)
    r = bwrite.export_local(buffer_w, [
        {"domain": "provider", "op": "add",
         "payload": {"namespace": "models/export", "name": "a",
                     "version": "2026-01-02", "source": "user",
                     "data_value_type": "json", "value": {"ctx": 2}}},
        {"domain": "provider", "op": "add",
         "payload": {"namespace": "models/export", "name": "b",
                     "version": "2026-02-01", "source": "official",
                     "data_value_type": "json", "value": {"ctx": 3}}},
    ], external_tag="send", token=B.WRITE_BUFFER_TOKEN)
    assert r["pushed"] == 2
    # consumer OUT : sélection par ARGUMENTS — on n'envoie QUE les
    # correspondances ; les payloads gardent source/version (quadruple)
    r = bwrite.export(buffer_w, source="official",
                      token=B.WRITE_BUFFER_TOKEN)
    assert r["exported"] == 1
    assert r["ops"][0]["payload"]["name"] == "b"
    assert r["ops"][0]["payload"]["version"] == "2026-02-01"
    assert r["ops"][0]["payload"]["source"] == "official"
    r = bwrite.export_included(buffer_w, token=B.WRITE_BUFFER_TOKEN)
    assert r["exported"] == 2  # pas de contrainte → tout
    # après envoi réussi : mark_status applied (reste en audit)
    ids = [o["op_id"] for o in r["ops"]]
    n = bwrite.mark_status(buffer_w, ids, "applied",
                           token=B.WRITE_BUFFER_TOKEN)
    assert n == 2
    assert buffer_w.table("buffer_op").count({"direction": "out",
                                              "status": "applied"}) == 2
    # rien à exporter après confirmation
    r = bwrite.export_included(buffer_w, token=B.WRITE_BUFFER_TOKEN)
    assert r["exported"] == 0


# ── sanitaires ───────────────────────────────────────────────

def test_sanitaires(domains):
    lw = domains["local"]
    lwrite.create_namespace(lw, "models", token=L.WRITE_CATALOGUE_TOKEN)
    lwrite.create_path(lw, "models/openai", "/data/oa", token=L.WRITE_CATALOGUE_TOKEN)
    r = LL.resolve_path(lw, "models/openai/gpt4.json")
    assert r["resolved"] and r["address"] == "/data/oa/gpt4.json", r
    lwrite.create_privilege(lw, "/data/oa", read="r---", write="-w--",
                            token=L.WRITE_CATALOGUE_TOKEN)
    m = LL.resolve_privilege(lw, "/data/oa/gpt4.json")
    assert len(m) == 1 and m[0]["read"] == "r---"
    # refresh 304 : ligne trouvée par sélection
    dt = data_table.get_table(L.db_ro(), "provider")
    r = dt.refresh("catalogue.provider.models.anthropic.claude:official@newest",
                   last_access="9999-01-01T00:00:00")
    assert r["modified"] is False