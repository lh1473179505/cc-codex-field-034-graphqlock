"""Parse a small GraphQL query document. Execution and auth are not implemented here."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class QueryError(Exception):
    pass


@dataclass
class Field:
    name: str
    alias: str | None
    args: dict[str, Any]
    sel: list[Field | FragSpread | Inline]


@dataclass
class FragSpread:
    name: str


@dataclass
class Inline:
    type_cond: str | None
    sel: list[Field | FragSpread | Inline]


@dataclass
class Fragment:
    name: str
    type_cond: str
    sel: list[Field | FragSpread | Inline]


@dataclass
class Document:
    operation: str
    name: str | None
    sel: list[Field | FragSpread | Inline]
    fragments: dict[str, Fragment]
    variables: dict[str, str]


class _Tok:
    def __init__(self, src: str) -> None:
        self.s = src
        self.i = 0

    def peek(self) -> str:
        self._ws()
        return self.s[self.i] if self.i < len(self.s) else ""

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n,":
            # GraphQL treats comma as whitespace
            self.i += 1
        if self.i + 1 < len(self.s) and self.s[self.i:self.i + 1] == "#":
            while self.i < len(self.s) and self.s[self.i] not in "\n":
                self.i += 1
            self._ws()

    def ident(self) -> str:
        self._ws()
        i = self.i
        if i >= len(self.s) or not (self.s[i].isalpha() or self.s[i] == "_"):
            raise QueryError("expected_ident")
        i += 1
        while i < len(self.s) and (self.s[i].isalnum() or self.s[i] == "_"):
            i += 1
        name = self.s[self.i:i]
        self.i = i
        return name

    def eat(self, ch: str) -> None:
        self._ws()
        if self.s[self.i:self.i + len(ch)] != ch:
            raise QueryError(f"expected {ch!r}")
        self.i += len(ch)

    def maybe(self, ch: str) -> bool:
        self._ws()
        if self.s[self.i:self.i + len(ch)] == ch:
            self.i += len(ch)
            return True
        return False

    def value(self) -> Any:
        self._ws()
        if self.maybe("$"):
            return {"var": self.ident()}
        if self.peek() == '"':
            return self._string()
        if self.peek() == "[":
            self.eat("[")
            xs = []
            while self.peek() and self.peek() != "]":
                xs.append(self.value())
            self.eat("]")
            return xs
        if self.peek() in "tfn" and self.s[self.i:self.i + 4] in ("true", "null"):
            # true or null
            if self.s[self.i:self.i + 4] == "true":
                self.i += 4
                return True
            self.i += 4
            return None
        if self.s[self.i:self.i + 5] == "false":
            self.i += 5
            return False
        i = self.i
        if self.s[i:i + 1] in "+-":
            i += 1
        if i < len(self.s) and self.s[i].isdigit():
            j = i
            while j < len(self.s) and self.s[j].isdigit():
                j += 1
            num = self.s[self.i:j]
            self.i = j
            return int(num)
        # enum
        return self.ident()

    def _string(self) -> str:
        self.eat('"')
        out = []
        while self.i < len(self.s) and self.s[self.i] != '"':
            if self.s[self.i] == "\\":
                self.i += 1
                if self.i >= len(self.s):
                    raise QueryError("bad_string")
                esc = self.s[self.i]
                out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(esc, esc))
                self.i += 1
                continue
            out.append(self.s[self.i])
            self.i += 1
        self.eat('"')
        return "".join(out)


def _sels(tok: _Tok) -> list[Field | FragSpread | Inline]:
    tok.eat("{")
    out: list[Field | FragSpread | Inline] = []
    while tok.peek() and tok.peek() != "}":
        if tok.maybe("..."):
            if tok.peek() == "o" and False:
                pass
            # inline ... on Type { } or spread ...Name
            # peek ident
            start = tok.i
            try:
                word = tok.ident() if tok.peek().isalpha() or tok.peek() == "_" else ""
            except QueryError:
                word = ""
            if word == "on":
                tname = tok.ident()
                out.append(Inline(tname, _sels(tok)))
            elif word:
                out.append(FragSpread(word))
            else:
                raise QueryError("bad_spread")
            continue
        alias_or_name = tok.ident()
        alias = None
        name = alias_or_name
        if tok.maybe(":"):
            alias = alias_or_name
            name = tok.ident()
        args: dict[str, Any] = {}
        if tok.maybe("("):
            while tok.peek() and tok.peek() != ")":
                an = tok.ident()
                tok.eat(":")
                args[an] = tok.value()
            tok.eat(")")
        sel: list = []
        if tok.peek() == "{":
            sel = _sels(tok)
        out.append(Field(name, alias, args, sel))
    tok.eat("}")
    return out


def parse_query(src: str) -> Document:
    tok = _Tok(src)
    operation = "query"
    name = None
    variables: dict[str, str] = {}
    if tok.peek() and (tok.s[tok.i:].lstrip(" \t\r\n,").startswith("query") or tok.s[tok.i:].lstrip(" \t\r\n,").startswith("mutation")):
        operation = tok.ident()
        if tok.peek() and (tok.peek().isalpha() or tok.peek() == "_"):
            # could be name or {
            # if next is ident not followed by ... we take name unless it's only {
            nxt = tok.peek()
            if nxt != "{":
                name = tok.ident()
        if tok.maybe("("):
            while tok.peek() and tok.peek() != ")":
                tok.eat("$")
                vn = tok.ident()
                tok.eat(":")
                typ = tok.ident()
                if tok.maybe("!"):
                    typ += "!"
                variables[vn] = typ
            tok.eat(")")
    sel = _sels(tok)
    fragments: dict[str, Fragment] = {}
    while tok.peek():
        word = tok.ident()
        if word != "fragment":
            raise QueryError("trailing")
        fname = tok.ident()
        if tok.ident() != "on":
            raise QueryError("fragment_on")
        tcond = tok.ident()
        fragments[fname] = Fragment(fname, tcond, _sels(tok))
    tok._ws()
    if tok.i < len(tok.s):
        raise QueryError("trailing")
    return Document(operation, name, sel, fragments, variables)


def field_to_dict(n: Field | FragSpread | Inline) -> dict[str, Any]:
    if isinstance(n, Field):
        return {
            "kind": "field",
            "name": n.name,
            "alias": n.alias,
            "args": n.args,
            "sel": [field_to_dict(x) for x in n.sel],
        }
    if isinstance(n, FragSpread):
        return {"kind": "spread", "name": n.name}
    return {"kind": "inline", "on": n.type_cond, "sel": [field_to_dict(x) for x in n.sel]}


def document_to_dict(d: Document) -> dict[str, Any]:
    return {
        "operation": d.operation,
        "name": d.name,
        "variables": d.variables,
        "sel": [field_to_dict(x) for x in d.sel],
        "fragments": {
            k: {"on": f.type_cond, "sel": [field_to_dict(x) for x in f.sel]}
            for k, f in d.fragments.items()
        },
    }


def load_query(path: str | Path) -> Document:
    return parse_query(Path(path).read_text(encoding="utf-8"))
