#!/usr/bin/env python3
"""resolution — Moteur de résolution typée des chemins d'objets runtime.

Le langage YAML peut exprimer des appels CHAÎNÉS sur un graphe d'objets :
    team.chatroom.read()
    team.chatroom.sent()
    team.list_member.reduce_pattern(name=*codeur*).random.get_home()

Chaque objet/nœud a un `rtype` (type de résolution) et chaque méthode
déclare entrée/sortie. Le PathEvaluator valide la chaîne et choisit la
méthode la PLUS RESTREINTE applicable.

TREILLIS D'INCLUSION (sous-type = compatible en entrée) :
                    list (0..n)
                    ├── list_not_empty (1..n)
                    │    └── singleton (exactement 1)
                    └── singleton_or_none (0..1)
                         └── singleton (exactement 1)

Règles :
  - singleton ⊂ singleton_or_none, singleton ⊂ list_not_empty,
    singleton_or_none ⊂ list, list_not_empty ⊂ list.
  - singleton est l'INTERSECTION de singleton_or_none et list_not_empty.
  - Compatibilité entrée : une méthode input_type=T accepte un récepteur R
    si R ⊂ T.
  - Choix : la méthode la PLUS RESTREINTE (le plus petit sous-type acceptant).
  - AMBIGUÏTÉ : si un récepteur singleton ne peut être satisfait que par
    singleton_or_none ET list_not_empty (sans singleton déclaré) → ERREUR
    (résolution interdite).

VALEURS SPÉCIALES :
  - None.return : signal qu'un récepteur singleton_or_none était VIDE (no-op,
    la chaîne continue). Distinct de None (valeur) et empty (liste vide).

REGISTRE DÉCLARATIF :
  METHOD_REGISTRY[objet] = {méthode: [ {input, output, impl}, ... ]}
  Plusieurs entrées = méthode POLYMORPHE (choisie par rtype réel).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# Types de résolution.
LIST = "list"
LIST_NOT_EMPTY = "list_not_empty"
SINGLETON = "singleton"
SINGLETON_OR_NONE = "singleton_or_none"
ANY = "any"   # accepte n'importe quel récepteur

RTYPES = (LIST, LIST_NOT_EMPTY, SINGLETON, SINGLETON_OR_NONE)

# Treillis : {rtype → ses SOUS-TYPES directs} (plus restreint = plus bas).
_SUBTYPES: Dict[str, set] = {
    LIST: {LIST_NOT_EMPTY, SINGLETON_OR_NONE},
    LIST_NOT_EMPTY: {SINGLETON},
    SINGLETON_OR_NONE: {SINGLETON},
    SINGLETON: set(),
}


def _ancestors(rtype: str) -> set:
    """Tous les types que `rtype` satisfait (rtype ∪ ses sur-types)."""
    if rtype not in _SUBTYPES:
        return {rtype}
    out = {rtype}
    for parent in _SUBTYPES.get(rtype, set()):
        out |= _ancestors(parent)
    return out


def accepts(input_type: str, rtype: str) -> bool:
    """Vrai si un récepteur de `rtype` est compatible avec une méthode qui
    attend `input_type` (R ⊂ T).

    CHAÎNAGE : une méthode `singleton` s'applique aussi sur un récepteur
    `singleton_or_none` (si vide → None.return, no-op). C'est la règle
    « la plupart des fonctions sont singleton_or_none » — les appels d'un
    membre doivent pouvoir chaîner sans erreur même quand reduce donne 0."""
    if input_type == ANY:
        return True
    if input_type == SINGLETON and rtype == SINGLETON_OR_NONE:
        return True
    return rtype in _ancestors(input_type)


def subtype_of(a: str, b: str) -> bool:
    """Vrai si a est un sous-type (strict ou égal) de b (a ⊂ b)."""
    return a in _ancestors(b)


class NoneReturn:
    """Signal : un récepteur singleton_or_none était vide (no-op)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "None.return"

    def __bool__(self) -> bool:
        return False


NONE_RETURN = NoneReturn()


class ResolutionError(TypeError):
    pass


class NoSignatureError(ResolutionError):
    """Aucune signature pour ce nom/type — la méthode n'existe pas (le
    RuntimeObject délègue alors aux méthodes natives du value)."""


class MethodRegistry:
    """Registre déclaratif : {objet: {méthode: [signatures]}}."""

    def __init__(self) -> None:
        self._methods: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._navs: Dict[str, Dict[str, Callable]] = {}

    def register(self, obj: str, name: str, input_type: str, output_type: str,
                 impl: Callable) -> None:
        self._methods.setdefault(obj, {}).setdefault(name, []).append({
            "input": input_type, "output": output_type, "impl": impl,
        })

    def register_nav(self, obj: str, attr: str, builder: Callable) -> None:
        """Déclare un attribut NAVIGATEUR (sous-objet).

        ``builder`` : callable(récepteur RuntimeObject) → RuntimeObject."""
        self._navs.setdefault(obj, {})[attr] = builder

    def nav(self, obj: str, attr: str) -> Optional[Callable]:
        return self._navs.get(obj, {}).get(attr)

    def signatures(self, obj: str, name: str) -> List[Dict[str, Any]]:
        return self._methods.get(obj, {}).get(name, [])

    def resolve(self, obj: str, name: str, rtype: str) -> Dict[str, Any]:
        """Choisit la méthode applicable la PLUS RESTREINTE pour `rtype`.

        Lève ResolutionError si non applicable OU si ambiguïté (plusieurs
        minima incomparables — ex. singleton non déclaré)."""
        sigs = self.signatures(obj, name)
        cands = [s for s in sigs if accepts(s["input"], rtype)]
        if not cands:
            raise NoSignatureError(
                f"'{obj}.{name}' : aucune signature pour récepteur {rtype} "
                f"(déclarées: {[s['input'] for s in sigs]})")
        # minima : signatures dont l'input est le plus restreint
        minima = [s for s in cands if not any(
            subtype_of(s2["input"], s["input"]) and s2 is not s
            for s2 in cands)]
        if len(minima) > 1:
            raise ResolutionError(
                f"'{obj}.{name}' : ambiguïté pour récepteur {rtype} — "
                f"plusieurs signatures minimales incomparables "
                f"({[s['input'] for s in minima]}). Déclarer {SINGLETON} pour "
                f"lever l'ambiguïté.")
        return minima[0]


class RuntimeObject:
    """Objet runtime navigable : attributs (sous-objets) + méthodes typées.

    - .first : list → singleton_or_none (1er élément)
    - .all   : singleton/singleton_or_none → list
    - les méthodes viennent du MethodRegistry, choisies par rtype réel.
    """

    def __init__(self, value: Any, rtype: str = SINGLETON_OR_NONE,
                 obj_kind: str = "", registry: Optional[MethodRegistry] = None,
                 ctx: Optional[Dict[str, Any]] = None):
        self.value = value
        self.rtype = rtype
        self.obj_kind = obj_kind
        self._registry = registry or GLOBAL_REGISTRY
        self._ctx = ctx or {}

    # ── conversions ────────────────────────────────────────

    @property
    def first(self) -> "RuntimeObject":
        """list/list_not_empty → singleton_or_none (1er élément ou None)."""
        items = self._as_list()
        v = items[0] if items else None
        return RuntimeObject(v, SINGLETON_OR_NONE, self.obj_kind,
                             self._registry, self._ctx)

    @property
    def all(self) -> "RuntimeObject":
        """n'importe quel récepteur → list (enveloppe)."""
        v = self._as_list()
        return RuntimeObject(v, LIST, self.obj_kind, self._registry, self._ctx)

    def _as_list(self) -> list:
        if isinstance(self.value, (list, tuple)):
            return list(self.value)
        if self.rtype in (SINGLETON, SINGLETON_OR_NONE):
            return [] if self.value is None else [self.value]
        return []

    def _effective(self) -> list:
        """Éléments réels (None.return → [])."""
        if self.value is NONE_RETURN or self.value is None:
            return []
        return self._as_list()

    # ── navigation / appel ─────────────────────────────────

    def _get_method(self, name: str):
        sig = self._registry.resolve(self.obj_kind, name, self.rtype)
        return sig

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in ("first", "all"):
            return getattr(self, name)
        # attribut NAVIGATEUR ? (sous-objet déclaré) → RuntimeObject
        nav = self._registry.nav(self.obj_kind, name)
        if nav is not None:
            return nav(self)
        # méthode typée → renvoie un appelable qui exécute l'implémentation
        try:
            sig = self._get_method(name)
        except NoSignatureError:
            sig = None
        if sig is not None:
            def _call(*args, **kwargs):
                return self._apply(sig, *args, **kwargs)
            return _call
        # DÉLÉGATION NATIVE : si le value est un dict/list et le nom est une
        # méthode native (get, keys, items, append…), on y délègue. Permet
        # `daemon.info().get("status")` en Python.
        native = getattr(self.value, name, None)
        if callable(native):
            return native
        raise AttributeError(name)

    def _apply(self, sig: Dict[str, Any],
               *args, **kwargs) -> "RuntimeObject":
        input_t = sig["input"]
        items = self._effective()
        if input_t in (SINGLETON, SINGLETON_OR_NONE):
            if not items:
                return RuntimeObject(NONE_RETURN, SINGLETON_OR_NONE,
                                     self.obj_kind, self._registry, self._ctx)
            item = items[0]
            out = sig["impl"](item, *args, ctx=self._ctx, **kwargs)
        else:
            # list / list_not_empty : l'implémentation reçoit la liste
            out = sig["impl"](items, *args, ctx=self._ctx, **kwargs)
        if isinstance(out, RuntimeObject):
            return out
        # enveloppe le résultat selon output_type
        return self._wrap(out, sig["output"])

    def _wrap(self, out: Any, output_t: str) -> "RuntimeObject":
        if output_t == LIST:
            return RuntimeObject(out if isinstance(out, list) else [out],
                                 LIST, self.obj_kind, self._registry, self._ctx)
        if output_t == LIST_NOT_EMPTY:
            return RuntimeObject(out if isinstance(out, list) else [out],
                                 LIST_NOT_EMPTY, self.obj_kind,
                                 self._registry, self._ctx)
        if output_t == SINGLETON:
            return RuntimeObject(out, SINGLETON, self.obj_kind,
                                 self._registry, self._ctx)
        # singleton_or_none
        return RuntimeObject(out, SINGLETON_OR_NONE, self.obj_kind,
                             self._registry, self._ctx)

    def __repr__(self) -> str:
        return (f"<RuntimeObject {self.obj_kind or '?'} rtype={self.rtype} "
                f"value={self.value!r}>")

    # ── délégation container (list/dict) pour un usage Python naturel ─────

    def __len__(self) -> int:
        return len(self._effective())

    def __iter__(self):
        return iter(self._effective())

    def __getitem__(self, key):
        return self._effective()[key]

    def __bool__(self) -> bool:
        return bool(self._effective())


class PathEvaluator:
    """Évalue une chaîne d'appels YAML sur un objet racine.

    Ex. "team.list_member.reduce_pattern(name=*codeur*).random.get_home()" →
    parse les segments, navigue, applique les méthodes typées."""

    def __init__(self, root: RuntimeObject):
        self._root = root

    def evaluate(self, path: str, **kwargs) -> Any:
        cur = self._root
        segs = self._parse(path)
        # ignore le préfixe racine redondant (ex. "team." quand root=team)
        if segs and segs[0]["type"] == "attr" and \
                segs[0]["name"] == self._root.obj_kind:
            segs = segs[1:]
        for seg in segs:
            if seg["type"] == "attr":
                cur = getattr(cur, seg["name"])
            else:  # call
                cur = getattr(cur, seg["name"])(*seg["args"], **seg["kwargs"])
        # retourne la VALEUR (None.return si signal)
        if isinstance(cur, RuntimeObject):
            if cur.value is NONE_RETURN:
                return NONE_RETURN
            return cur.value
        return cur

    @staticmethod
    def _parse(path: str) -> List[Dict[str, Any]]:
        """Parse 'a.b.c(x).d(y=z)' → [attr/call, args, kwargs]."""
        segs = []
        i, n = 0, len(path)
        while i < n:
            # lire le nom (lettres/chiffres/_/$/*)
            start = i
            while i < n and (path[i].isalnum() or path[i] in "_$*"):
                i += 1
            if i == start:
                i += 1
                continue
            name = path[start:i]
            if i < n and path[i] == "(":
                depth, j = 1, i + 1
                while j < n and depth:
                    if path[j] == "(":
                        depth += 1
                    elif path[j] == ")":
                        depth -= 1
                    j += 1
                args_src = path[i + 1:j - 1]
                args, kwargs = _parse_args(args_src)
                segs.append({"type": "call", "name": name,
                             "args": args, "kwargs": kwargs})
                i = j
            else:
                segs.append({"type": "attr", "name": name})
                i += 1   # saute le '.' séparateur
        return segs


def _parse_args(src: str) -> tuple:
    """Parse des arguments 'a, b, x=*codeur*' → (args, kwargs)."""
    args, kwargs = [], {}
    if not src.strip():
        return args, kwargs
    # découpe au niveau top (pas dans les parenthèses/guillemets)
    parts, depth, cur, quote = [], 0, "", None
    for ch in src:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            cur += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    for part in parts:
        if "=" in part and not part.startswith(("'", '"')):
            k, v = part.split("=", 1)
            kwargs[k.strip()] = _parse_value(v.strip())
        else:
            args.append(_parse_value(part))
    return args, kwargs


def _parse_value(v: str) -> Any:
    if not v:
        return None
    if (v.startswith("'") and v.endswith("'")) or \
       (v.startswith('"') and v.endswith('"')):
        return v[1:-1]
    if v == "True" or v == "true":
        return True
    if v == "False" or v == "false":
        return False
    if v in ("None", "none", "null"):
        return None
    if v.startswith("{") and v.endswith("}"):
        # dict simple key:value ou key=value
        out = {}
        for kv in v[1:-1].split(","):
            if not kv.strip():
                continue
            k, val = re_split_kv(kv)
            out[k] = _parse_value(val)
        return out
    if v.startswith("[") and v.endswith("]"):
        return [_parse_value(x.strip()) for x in v[1:-1].split(",") if x.strip()]
    return v


def re_split_kv(kv: str) -> tuple:
    for sep in (":", "="):
        if sep in kv:
            k, val = kv.split(sep, 1)
            return k.strip(), val.strip()
    return kv.strip(), None


# Registre global (rempli par les objets racines).
GLOBAL_REGISTRY = MethodRegistry()


def make_root(obj_kind: str, value: Any, rtype: str = SINGLETON,
              ctx: Optional[Dict[str, Any]] = None) -> RuntimeObject:
    return RuntimeObject(value, rtype, obj_kind, GLOBAL_REGISTRY, ctx)

