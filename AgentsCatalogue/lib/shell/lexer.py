"""Lexer — tokenisation, expansion, globbing pour le shell.

Respecte les règles shell classiques :
  - 'single quotes' : tout littéral
  - "double quotes" : $VAR et $(cmd) expansés, pas de glob
  - sans quotes : $VAR, $(cmd), et glob expansion
  - $(cmd) et `cmd` : substitution de commande
  - * ? [ : glob expansion
"""

import glob as _glob
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


# ── Token ──────────────────────────────────────────────────────

class Token:
    __slots__ = ("type", "value", "quote", "text")

    def __init__(self, type: str, value: str, quote: str = "", text: str = ""):
        self.type = type       # word / pipe / redirect_out / redirect_append / redirect_in / redirect_err
        self.value = value     # valeur brute (avec quotes)
        self.quote = quote     # '' / "" / "" (vide = unquoted)
        self.text = text or value  # valeur après expansion

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, quote={self.quote!r})"

    @property
    def is_quoted(self) -> bool:
        return bool(self.quote)


# ── Tokenizer ──────────────────────────────────────────────────

def tokenize(cmd: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(cmd)
    current = []
    in_quote = None

    while i < n:
        ch = cmd[i]

        # ── single quote ──
        if ch == "'" and in_quote is None:
            in_quote = "'"
            current.append(ch)
            i += 1
        elif ch == "'" and in_quote == "'":
            current.append(ch)
            tokens.append(Token("word", "".join(current), quote="'"))
            current = []
            in_quote = None
            i += 1

        # ── double quote ──
        elif ch == '"' and in_quote is None:
            in_quote = '"'
            current.append(ch)
            i += 1
        elif ch == '"' and in_quote == '"':
            current.append(ch)
            tokens.append(Token("word", "".join(current), quote='"'))
            current = []
            in_quote = None
            i += 1

        # ── operators (only when not in quotes) ──
        elif in_quote is None and ch in ("|", ">", "<", "&"):
            if current:
                token = Token("word", "".join(current))
                tokens.append(token)
                current = []
            if ch == "|":
                tokens.append(Token("pipe", "|"))
                i += 1
            elif ch == ">" and i + 1 < n and cmd[i + 1] == ">":
                tokens.append(Token("redirect_append", ">>"))
                i += 2
            elif ch == ">":
                tokens.append(Token("redirect_out", ">"))
                i += 1
            elif ch == "<":
                tokens.append(Token("redirect_in", "<"))
                i += 1
            elif ch == "&":
                tokens.append(Token("background", "&"))
                i += 1

        elif in_quote is None and ch == "2" and i + 1 < n and cmd[i + 1] == ">":
            if current:
                tokens.append(Token("word", "".join(current)))
                current = []
            if i + 2 < n and cmd[i + 2] == ">":
                tokens.append(Token("redirect_err_append", "2>>"))
                i += 3
            else:
                tokens.append(Token("redirect_err", "2>"))
                i += 2

        # ── space ──
        elif in_quote is None and ch in (" ", "\t"):
            if current:
                tokens.append(Token("word", "".join(current)))
                current = []
            i += 1

        # ── backslash escape (quotes & backslash only) ──
        elif ch == "\\" and i + 1 < n and in_quote != "'":
            nxt = cmd[i + 1]
            if nxt in ('"', "'", "\\"):
                current.append(nxt)
                i += 2
            else:
                current.append(ch)
                current.append(nxt)
                i += 2

        # ── other ──
        else:
            current.append(ch)
            i += 1

    if current:
        tokens.append(Token("word", "".join(current)))

    return tokens


# ── Variable expansion ─────────────────────────────────────────

def expand_vars(tokens: List[Token], env: Dict[str, str]) -> List[Token]:
    combined = {**os.environ, **env}
    result = []

    for tok in tokens:
        if tok.type != "word":
            result.append(tok)
            continue

        if tok.quote == "'":
            tok.text = tok.value.strip("'")
            result.append(tok)
            continue

        text = tok.value.strip('"') if tok.quote == '"' else tok.value

        text = text.replace("\\$", "\x00")

        def _replace(m: re.Match) -> str:
            if m.group(1) is not None:
                return combined.get(m.group(1), "")
            if m.group(2) is not None:
                return combined.get(m.group(2), "")
            if m.group() == "$$":
                return "$"
            return combined.get("?", "")

        text = re.sub(r'\$\{(\w+)\}|\$(\w+)|\$\?|\$\$', _replace, text)
        text = text.replace("\x00", "$")
        tok.text = text
        result.append(tok)

    return result


# ── Command substitution ───────────────────────────────────────

def expand_cmd_subst(tokens: List[Token], executor_fn: Callable) -> List[Token]:
    """Remplace $(cmd) et `cmd` par la sortie de la commande."""
    result = []
    for tok in tokens:
        if tok.type != "word":
            result.append(tok)
            continue
        text = tok.text

        # $(...)
        def _run_cmd_subst(m: re.Match) -> str:
            inner = m.group(1)
            out = executor_fn(inner)
            return out.get("stdout", "").rstrip("\n") or ""

        # `...` (backticks)
        def _run_backtick(m: re.Match) -> str:
            inner = m.group(1)
            out = executor_fn(inner)
            return out.get("stdout", "").rstrip("\n") or ""

        text = re.sub(r'\$\(([^)]+)\)', _run_cmd_subst, text)
        text = re.sub(r'`([^`]+)`', _run_backtick, text)
        tok.text = text
        result.append(tok)

    return result


# ── Globbing ───────────────────────────────────────────────────

def expand_glob(tokens: List[Token], workdir: str) -> List[Token]:
    """Remplace les patterns glob par les fichiers correspondants.

    Ne s'applique qu'aux tokens non-quoted.
    Si aucun fichier ne correspond, le token reste inchangé."""
    result = []
    for tok in tokens:
        if tok.type != "word" or tok.is_quoted:
            result.append(tok)
            continue
        if not _has_glob_chars(tok.text):
            result.append(tok)
            continue

        matches = _glob.glob(str(Path(workdir) / tok.text))
        if not matches:
            result.append(tok)
        else:
            for path in sorted(matches):
                rel = Path(path).relative_to(workdir)
                result.append(Token("word", str(rel)))

    return result


def _has_glob_chars(s: str) -> bool:
    return bool(set(s) & {"*", "?", "["})


# ── Pipeline + redirect parsing ────────────────────────────────

def split_pipeline(tokens: List[Token]) -> List[List[Token]]:
    segments: List[List[Token]] = []
    current: List[Token] = []
    for tok in tokens:
        if tok.type == "pipe":
            segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments or [[]]


def parse_segment(tokens: List[Token]) -> Tuple[List[str], Dict[str, str]]:
    """Parse une liste de tokens en (args, redirects).

    redirects = {"stdout": str, "stderr": str, "stdin": str, "append": bool}"""
    args: List[str] = []
    redirects: Dict[str, str] = {}
    append = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "redirect_out":
            if i + 1 < len(tokens):
                redirects["stdout"] = tokens[i + 1].text
                append = False
                i += 2
            else:
                i += 1
        elif tok.type == "redirect_append":
            if i + 1 < len(tokens):
                redirects["stdout"] = tokens[i + 1].text
                append = True
                i += 2
            else:
                i += 1
        elif tok.type == "redirect_in":
            if i + 1 < len(tokens):
                redirects["stdin"] = tokens[i + 1].text
                i += 2
            else:
                i += 1
        elif tok.type == "redirect_err":
            if i + 1 < len(tokens):
                redirects["stderr"] = tokens[i + 1].text
                i += 2
            else:
                i += 1
        elif tok.type == "redirect_err_append":
            if i + 1 < len(tokens):
                redirects["stderr"] = tokens[i + 1].text
                i += 2
            else:
                i += 1
        elif tok.type == "word":
            args.append(tok.text)
            i += 1
        else:
            i += 1

    if append:
        redirects["append"] = "true"

    return args, redirects