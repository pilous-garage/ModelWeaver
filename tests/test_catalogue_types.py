"""test_catalogue_types — seed des 7 data_types du catalogue LLM (block 1).

Lancement :
    python3 -m pytest tests/test_catalogue_types.py -q

Couvre :
  - les 7 types créés (4 tables chacun, descriptions, ordre catalogue)
  - colonne extra model_official_id sur model_provider_endpoint (et ALTER
    sur une table existante sans la colonne)
  - registres de tags x_tag_type seedés (attachable ensuite)
  - source models.dev déclarée (distant)
  - idempotence (re-seed sans perte) + garde token (WriteDenied sans token)
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

MW_HOME = Path(tempfile.mkdtemp()) / "mw"
os.environ["MODELWEAVER_HOME"] = str(MW_HOME)

from modules.sqlite.base import WriteDenied  # noqa: E402
from modules.sqlite import local as L  # noqa: E402
from modules.sqlite.local import catalogue_types as CT  # noqa: E402
from modules.sqlite.local import data_table, read as lread  # noqa: E402


@pytest.fixture(scope="module")
def lw():
    shutil.rmtree(MW_HOME, ignore_errors=True)
    w = L.get_writer(L.WRITE_CATALOGUE_TOKEN)
    yield w
    w.close()


def _tables(w, type_):
    return {f"{type_}_data", f"{type_}_tag", f"{type_}_tag_type",
            f"{type_}_source_and_sharing"}


# ── seed de base ──────────────────────────────────────────────

def test_seed_all(lw):
    r = CT.seed_catalogue_types(lw, token="write_catalogue")
    assert r["ok"] is True
    assert list(r["types"]) == CT.CATALOGUE_ORDER
    for t in CT.CATALOGUE_ORDER:
        assert r["types"][t] in ("created", "exists"), r["types"][t]


def test_type_tables_and_cols(lw):
    for type_ in CT.CATALOGUE_ORDER:
        for table in _tables(lw, type_):
            assert lw._exists(table), f"table manquante: {table}"
    cols = [c["name"] for c in lw.sql(f"PRAGMA table_info(model_provider_endpoint_data)")]
    assert "model_official_id" in cols, cols
    for t in CT.CATALOGUE_ORDER:
        if t != "model_provider_endpoint":
            cols = [c["name"] for c in lw.sql(f"PRAGMA table_info({t}_data)")]
            assert "model_official_id" not in cols


def test_descriptions_seeded(lw):
    dts = data_table.list_data_types(lw)
    by_code = {d["code"]: d for d in dts}
    for type_, spec in CT.CATALOGUE_TYPES.items():
        assert by_code[type_]["description"] == spec["description"]


def test_tag_registries(lw):
    for type_, regs in CT.TAG_REGISTRIES.items():
        dt = data_table.get_table(lw, type_)
        declared = {t["tag_type"]: t["tag_value_type"]
                    for t in dt.list_tag_types()}
        for tg, (vt, _desc) in regs.items():
            assert tg in declared, f"{type_}: tag {tg} absent du registre"
            assert declared[tg] == vt, f"{type_}: {tg} = {declared[tg]}"


def test_models_dev_source(lw):
    srcs = {s["ref"]: s for s in lread.list_sources(lw)}
    assert srcs["models.dev"]["type_source"] == "distant"


def test_idempotent(lw):
    n_before = len(data_table.list_data_types(lw))
    r = CT.seed_catalogue_types(lw, token="write_catalogue")
    assert r["types"]["model_official"] == "exists"
    assert len(data_table.list_data_types(lw)) == n_before


# ── colonne extra sur table PRÉEXISTANTE (ALTER) ─────────────

def test_extra_cols_alter(lw):
    data_table.create_new_data_type(lw, "zzz_check", description="x",
                                    token="write_catalogue")
    data_table.create_new_data_type(
        lw, "zzz_check", extra_cols={"zzz_extra": "TEXT"},
        token="write_catalogue")
    cols = [c["name"] for c in lw.sql("PRAGMA table_info(zzz_check_data)")]
    assert "zzz_extra" in cols, cols


# ── garde token (writer dédié) ───────────────────────────────

def test_token_guard(lw):
    with pytest.raises(WriteDenied):
        CT.seed_catalogue_types(lw)