"""Transformations de texte et données.

Migrées depuis services/skill_manager.py (_exec_*).
"""

import base64
import hashlib
import difflib
import uuid
import random
import time


def template(inputs: dict, ws: str) -> dict:
    template = inputs.get("template", "")
    vars_ = inputs.get("vars", {}) or {}
    out = template
    for k, v in vars_.items():
        out = out.replace("{{" + str(k) + "}}", str(v))
    return {"result": out}


def string_ops(inputs: dict, ws: str) -> dict:
    op = inputs.get("op", "trim")
    text = inputs.get("text", "")
    if op == "trim":
        return {"result": text.strip()}
    if op == "upper":
        return {"result": text.upper()}
    if op == "lower":
        return {"result": text.lower()}
    if op == "len":
        return {"result": len(text)}
    if op == "split":
        sep = inputs.get("sep", " ")
        mx = inputs.get("maxsplit", -1)
        parts = text.split(sep, mx) if mx and mx > 0 else text.split(sep)
        return {"result": parts}
    if op == "replace":
        return {"result": text.replace(inputs.get("old", ""),
                                      inputs.get("new", ""))}
    if op == "slice":
        a = int(inputs.get("start", 0))
        b = inputs.get("end")
        b = None if b in (None, "") else int(b)
        return {"result": text[a:b]}
    return {"result": text, "error": f"op inconnue: {op}"}


def diff(inputs: dict, ws: str) -> dict:
    a = inputs.get("a", "").splitlines()
    b = inputs.get("b", "").splitlines()
    added, removed = [], []
    for line in difflib.unified_diff(a, b, lineterm=""):
        if line.startswith("+") and not line.startswith("+++ "):
            added.append(line[1:])
        elif line.startswith("-") and not line.startswith("--- "):
            removed.append(line[1:])
    return {"added": added, "removed": removed,
            "added_count": len(added), "removed_count": len(removed)}


def base64(inputs: dict, ws: str) -> dict:
    mode = inputs.get("mode", "encode")
    data = inputs.get("data", "")
    if mode == "decode":
        try:
            return {"result": base64.b64decode(data).decode("utf-8",
                                                           errors="replace")}
        except Exception as e:
            return {"result": "", "error": str(e)}
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return {"result": base64.b64encode(raw).decode("ascii")}


def hash(inputs: dict, ws: str) -> dict:
    algo = inputs.get("algo", "sha256")
    data = inputs.get("data", "")
    h = hashlib.new(algo)
    h.update(data.encode("utf-8") if isinstance(data, str) else data)
    return {"algo": algo, "hex": h.hexdigest()}


def uuid(inputs: dict, ws: str) -> dict:
    return {"value": str(uuid.uuid4())}


def json_query(inputs: dict, ws: str) -> dict:
    data = inputs.get("data")
    path = inputs.get("path", "")
    cur = data
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return {"found": False, "error": f"index invalide: {part}"}
        elif isinstance(cur, dict):
            if part not in cur:
                return {"found": False, "error": f"clé absente: {part}"}
            cur = cur[part]
        else:
            return {"found": False, "error": f"non navigable en '{part}'"}
    return {"found": True, "value": cur}


def random(inputs: dict, ws: str) -> dict:
    lo = int(inputs.get("min", 0))
    hi = int(inputs.get("max", 100))
    if hi < lo:
        hi = lo
    return {"value": random.randint(lo, hi)}


def timestamp(inputs: dict, ws: str) -> dict:
    fmt = inputs.get("format", "iso")
    now = time.time()
    if fmt == "epoch":
        return {"value": now, "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime(now))}
    if fmt == "date":
        return {"value": time.strftime("%Y-%m-%d", time.localtime(now))}
    return {"value": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                   time.localtime(now)),
            "epoch": now}


__skills__ = [
    "template", "string_ops", "diff", "base64", "hash", "json_query",
    "uuid", "random", "timestamp",
]
