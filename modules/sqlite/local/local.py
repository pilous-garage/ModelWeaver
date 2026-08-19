from __future__ import annotations

import re
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

import xxhash

from modules.sqlite.base import Db

# ── Constantes du domaine (V4 figées) ─────────────────────────
MAX_PRIV_LEVEL = (1 << 32) - 1
ASK_LEVELS = ("none", "security_supervisor", "human", "human_root")
SHARING_LEVELS = ("non", "everyone", "enterprise", "friends", "official")
SOURCE_TYPES = ("user", "official", "enterprise", "distant", "friend", "git_depot")
DEFAULT_SOURCE_CHAIN = ("user", "enterprise", "official")
TAG_VALUE_TYPES = ("bool", "text", "number", "date", "list", "range")
SCALAR_VALUE_TYPES = ("string", "int", "uint", "float", "bool", "date",
                      "timestamp", "json")
EXTERNAL_VALUE_TYPES = ("file",)
DATA_STATUSES = ("active", "missing", "archived")
VERSION_SELECTORS = ("newest", "oldest")
RESERVED_TEST_PREFIX = "auto-test-check-official"
RESERVED_SYSTEM_PREFIXES = ("auto/", "system/", "catalogue/")

_SEED = 0x5EEDC0DE
_TEXT_N = re.compile(r"^text\[\d+ch\]$")

BASE_COLUMNS = {
    "data_id": "INTEGER PRIMARY KEY",
    "name": "TEXT NOT NULL",
    "namespace": "TEXT NOT NULL DEFAULT ''",
    "source_id": "INTEGER NOT NULL",
    "version": "TEXT NOT NULL DEFAULT 'latest'",
    "data_value_type": "TEXT NOT NULL",
    "value": "TEXT DEFAULT '{}'",
    "ref_file": "TEXT DEFAULT ''",
    "path": "TEXT DEFAULT ''",
    "description": "TEXT DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "last_modify": "TEXT DEFAULT NULL",
    "created_at": "TEXT DEFAULT (datetime('now'))",
    "updated_at": "TEXT DEFAULT (datetime('now'))",
}
BASE_UNIQUE = "UNIQUE(namespace, name, source_id, version)"

# Colonnes extra autorisées à l'écriture par upsert/modify (déclarées par
# create_new_data_type(extra_cols=...) et présentes dans les tables). Toute
# autre clé de payload est ignorée pour l'écriture des colonnes.
EXTRA_COLS_ALLOWED = ("model_official_id",)

_TYPE_TO_SQL = {
    "text": "TEXT", "string": "TEXT", "date": "TEXT", "json": "TEXT",
    "file": "TEXT", "row": "TEXT",
    "int": "INTEGER", "uint": "INTEGER", "bool": "INTEGER",
    "timestamp": "INTEGER", "float": "REAL",
}
_TAG_TYPE_TO_SQL = {
    "bool": "INTEGER", "text": "TEXT", "number": "REAL",
    "date": "TEXT", "list": "TEXT", "range": "TEXT",
}


# ── Helpers identitaires / typage ─────────────────────────────
def data_id_of(namespace: str, name: str, source_id: int, version: str) -> int:
    """Hash stable (int64 positif) du QUADRUPLE (namespace, name, source_id,
    version) — un data_id par entrée (version/source), identique load/reload."""
    return xxhash.xxh64(f"{namespace}\0{name}\0{source_id}\0{version}",
                        seed=_SEED).intdigest() & 0x7FFFFFFFFFFFFFFF


def source_id_for(db: Db, source) -> int:
    """Résout une source (int sources_id ou type_source/ref) en sources_id."""
    if isinstance(source, int):
        return source
    s = str(source or "user").strip()
    row = db.table("global_local_source").get({"type_source": s})
    if row:
        return row["sources_id"]
    row = db.table("global_local_source").get({"ref": s})
    if row:
        return row["sources_id"]
    raise ValueError(f"source inconnue: {s!r} (types: {', '.join(SOURCE_TYPES)})")


def source_type_of(db: Db, source_id: int) -> str:
    row = db.table("global_local_source").get({"sources_id": source_id},
                                              cols=["type_source"])
    return row["type_source"] if row else ""


def version_key(v: str) -> tuple:
    """Clé de comparaison de version (numérique-aware : 1.10 > 1.9)."""
    parts = re.split(r"[._\-+]", str(v or "").strip().lower())
    out = []
    for p in parts:
        if p.isdigit():
            out.append((0, int(p)))
        elif p:
            out.append((1, p))
    return tuple(out)


def parse_accessor(type_: str, accessor: str) -> Dict[str, Any]:
    """Parse une adresse d'accès catalogue en (namespace, name) + sélecteurs.

    Syntaxes acceptées (V1) :
      catalogue.<type>.<ns1>.<ns2>.<name>  |  <ns1>/<ns2>/<name>
    sélecteurs optionnels APRÈS le nom, séparés par ':' (le premier ':' coupe
    le nom du reste — les versions peuvent contenir des points) :
      <source>@<version>    source ∈ all|user|official|enterprise|distant|
                            friend|git_depot| chaîne de préférence
                            'user>enterprise>official' ; version ∈ newest|
                            oldest|<version littérale> — hérité : @<version>
                            seul = version littérale, source = chaîne défaut.
      tag(<tag_type>)       filtre PRIORITAIRE (avant source puis version).
    Défauts : source = DEFAULT_SOURCE_CHAIN, version = newest.
    """
    s = (accessor or "").strip()
    if ":" in s:
        base, sel_str = s.split(":", 1)
    else:
        base, sel_str = s, ""
    selectors: Dict[str, Any] = {"source": "", "version": "newest", "tag": ""}
    has_ver_selector = False
    for seg in sel_str.split(":") if sel_str else []:
        if seg.startswith("tag(") and seg.endswith(")"):
            selectors["tag"] = seg[4:-1].strip()
        elif "@" in seg:
            src, ver = seg.split("@", 1)
            selectors["source"] = src.strip()
            selectors["version"] = ver.strip() or "newest"
            has_ver_selector = True
        else:
            raise ValueError(f"sélecteur invalide: {seg!r} dans {accessor!r}")
    if base.startswith("catalogue."):
        base = base[len("catalogue."):]
        if base.startswith(type_ + "."):
            base = base[len(type_) + 1:]
    parts = base.split(".") if "." in base else base.split("/")
    tail = parts[-1] if parts else ""
    if "@" in tail:
        tail, ver = tail.split("@", 1)
        if not has_ver_selector:
            selectors["version"] = ver.strip() or "newest"
    if not tail:
        raise ValueError(f"adresse invalide: {accessor!r}")
    name = tail
    namespace = "/".join(parts[:-1]) if len(parts) > 1 else ""
    return {"namespace": namespace, "name": name, **selectors}


def source_chain(db: Db, spec: str = "") -> List[int]:
    """Séquence de sources (sources_id) pour un sélecteur de source.

    'all' → toutes les sources actives ; 'a>b>c' → ordre de préférence ;
    une source seule sinon. Défaut : DEFAULT_SOURCE_CHAIN. Lève si une
    source du spec est inconnue."""
    spec = (spec or "").strip() or ">".join(DEFAULT_SOURCE_CHAIN)
    if spec == "all":
        rows = db.table("global_local_source").select(
            where={"active": 1}, order_by="type_source")
        return [r["sources_id"] for r in rows]
    out: List[int] = []
    for part in spec.split(">"):
        part = part.strip()
        if not part:
            continue
        sid = source_id_for(db, part)
        if sid not in out:
            out.append(sid)
    return out


def select_row(db: Db, type_: str, namespace: str, name: str,
               selectors: Dict[str, Any],
               cols: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Sélection V1 sur la FAMILLE (namespace, name) : 1) filtre TAG
    (prioritaire), 2) PRÉFÉRENCE de source (première source de la chaîne
    qui a des lignes), 3) TRI de version (newest = max, oldest = min,
    littérale = égalité). Retourne la ligne (ou None)."""
    tbl = db.table(f"{type_}_data")
    need = ["source_id", "version"]
    if cols:
        cols = list(dict.fromkeys(list(cols) + [c for c in need if c not in cols]))
    rows = tbl.select(where={"namespace": namespace, "name": name},
                      cols=cols, order_by="data_id")
    if not rows:
        return None
    tag = str(selectors.get("tag") or "").strip()
    if tag:
        tagged = {r["data_id"] for r in db.table(f"{type_}_tag").select(
            where={"tag_type": tag}, cols=["data_id"])}
        rows = [r for r in rows if r["data_id"] in tagged]
        if not rows:
            return None
    chain = source_chain(db, str(selectors.get("source") or ""))
    by_src: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        by_src.setdefault(int(r["source_id"]), []).append(r)
    for sid in chain:
        if by_src.get(sid):
            rows = by_src[sid]
            break
    else:
        return None
    ver = str(selectors.get("version") or "newest").strip()
    if ver == "newest":
        return max(rows, key=lambda r: version_key(r["version"]))
    if ver == "oldest":
        return min(rows, key=lambda r: version_key(r["version"]))
    for r in sorted(rows, key=lambda r: version_key(r["version"]), reverse=True):
        if str(r["version"]) == ver:
            return r
    return None


def bump_version(v: str) -> str:
    """Version suivante : incrémente le dernier nombre (1.9 → 1.10)."""
    m = re.search(r"(\d+)([^0-9]*)$", str(v or ""))
    if not m:
        return f"{v}.1" if v else "1"
    return v[:m.start(1)] + str(int(m.group(1)) + 1) + m.group(2)


def parse_row_type(dvt: str) -> Optional[List[tuple]]:
    s = dvt.strip()
    if not (s.startswith("row(") and s.endswith(")")):
        return None
    inner = s[4:-1].strip()
    if not inner:
        return None
    out = []
    for part in inner.split(","):
        part = part.strip()
        if "=" not in part:
            raise ValueError(f"row invalide: {part!r} dans {dvt!r}")
        header, typ = part.split("=", 1)
        header, typ = header.strip(), typ.strip()
        if not header or not typ:
            raise ValueError(f"row invalide: {dvt!r}")
        out.append((header, typ))
    return out


def col_type_for(value_type: str) -> str:
    p = value_type.strip()
    if p in _TYPE_TO_SQL:
        return _TYPE_TO_SQL[p]
    if _TEXT_N.match(p):
        return "TEXT"
    raise ValueError(f"type de valeur inconnu: {value_type!r}")


def coerce(dvt: str, value: Any):
    d = dvt.strip()
    if d in ("json", "row", "file"):
        return value
    if d == "int":
        return int(value)
    if d == "uint":
        v = int(value)
        return v if v >= 0 else 0
    if d == "float":
        return float(value)
    if d == "bool":
        if isinstance(value, str):
            return 1 if value.lower() in ("1", "true", "yes", "on") else 0
        return 1 if value else 0
    if d == "timestamp":
        return int(value)
    if d == "date":
        return str(value)
    return str(value)


def is_reserved(ref: str) -> bool:
    return (ref.startswith(RESERVED_TEST_PREFIX)
            or any(ref.startswith(p) for p in RESERVED_SYSTEM_PREFIXES)
            or ref in ("",))


def now_iso() -> str:
    t = time.time()
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def now_shift(seconds: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
    return dt.isoformat()


# ── Algorithmes / logique (pas de CRUD fin → read.py / write.py) ─

def resolve_path(db: Db, target: str) -> Dict[str, Any]:
    """Résolution par précision gauche→droite (/lib/a/$1 prime).

    Un segment `$N` du path_name CAPTURE le segment cible correspondant
    (joker) ; les segments littéraux doivent matcher exactement. L'adresse
    résolue = préfixe address + segments capturés ($) + segments en plus
    (tail). Contrairement à V2, le segment capturé est réellement réinjecté."""
    parts = target.split("/")
    rows = db.table("global_local_path").select(order_by="LENGTH(path_name) DESC")
    best, best_len, best_caps = None, -1, []
    for row in rows:
        pn = row["path_name"].split("/")
        if len(pn) > len(parts):
            continue
        score, ok, caps = 0, True, []
        for i, seg in enumerate(pn):
            if not seg or seg[0] == "$":
                if i < len(parts):
                    caps.append(parts[i])
                continue
            if seg != parts[i]:
                ok = False
                break
            score += 1
        if ok and score > best_len:
            best, best_len, best_caps = row, score, caps
    if not best:
        return {"resolved": False, "target": target, "address": target,
                "scheme": "file"}
    addr = best["address"].replace("/$", "")
    for seg in best_caps + parts[len(best["path_name"].split("/")):]:
        addr += "/" + seg
    return {"resolved": True, "target": target, "address": addr,
            "scheme": best["scheme"]}


def _priv_matches(row: Dict[str, Any], chemin: str) -> bool:
    if row["kind"] == "cmd":
        target = (chemin or "").strip()
        declared = (row["chemin_ref"] or "").strip()
        return target == declared or (declared and target.startswith(declared + " "))
    if row["kind"] == "path":
        target = (chemin or "").strip("/")
        declared = (row["chemin_ref"] or "").strip("/")
        return target == declared or target.startswith(declared + "/")
    return chemin == row["chemin_ref"]


def resolve_privilege(db: Db, chemin: str, kind: str = "path",
                      agent_id: int = -1, team: int = -1) -> List[Dict[str, Any]]:
    """Match par précision (plus long chemin_ref en premier), filtrage
    d'identité (agent_id/team ciblés OU -1 = générique). SANS consommation."""
    rows = db.table("global_local_privilege").sql(
        "SELECT * FROM global_local_privilege WHERE kind = ? "
        "AND (agent_id = ? OR agent_id = -1) "
        "AND (team = ? OR team = -1) "
        "ORDER BY LENGTH(chemin_ref) DESC",
        (kind, int(agent_id), int(team)))
    return [r for r in rows if _priv_matches(r, chemin)]


def resolve_can_be_shared(db: Db, type_: str, ref: str = "",
                          data_id: Optional[int] = None,
                          tag_type: str = "", tag_value: str = "") -> str:
    """Cascade : (type,tag_type,tag_value) → (type,'*','*') → 'non'."""
    dtid = db.table("global_local_data_type").get({"code": type_})
    if not dtid:
        return "non"
    dt = dtid["data_type_id"]
    for tt, tv in ((tag_type, tag_value), ("*", "*")):
        r = db.table("global_local_shared_default").get(
            {"data_type_id": dt, "tag_type": tt or "*", "tag_value": tv or "*"})
        if r:
            return r["can_be_shared"]
    did = data_id if data_id is not None else data_id_of(ref)
    sh = db.table(f"{type_}_source_and_sharing").get({"data_id": did})
    return sh["can_be_shared"] if sh and sh["can_be_shared"] != "non" else "non"
