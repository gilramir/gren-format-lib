#!/usr/bin/env python3
"""Property-based random Gren generator (qe.md avenue #2).

Builds random-but-legal Gren modules with bounded depth and checks the standing
invariants on each: parses, formats without crashing, AST-equivalent, idempotent,
and comment-preserving. Targets the *feature co-occurrence* axis the 2026-07-18
scan proved productive — the axis every single-axis synthetic gate misses.

See GENERATOR.md for the full design (oracles, legal-layout emission, shrinking,
artifact management). Rebuild the app first: `cd ../../gren-format && ./build.sh`.

    ./gen-random.py -n 2000 -j 12          # sweep
    ./gen-random.py --seed 12345           # replay one master seed, verbose
    ./gen-random.py --promote 12345 --name Foo   # promote a fixed find to a fixture
"""

import argparse
import concurrent.futures
import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "..", "gren-format", "app")
OUT_DEFAULT = os.path.join(HERE, "gen-out")
TESTFILES = os.path.join(HERE, "testfiles", "Formatter")

BINOPS = ["||", "&&", "==", "/=", "<", ">", "<=", ">=", "++", "+", "-", "*",
          "/", "//", "^", "<<", ">>"]
PIPES = ["|>", "<|"]
WORDS = ["alpha", "bravo", "delta", "echo", "foxtrot", "sierra", "tango"]
INDENT = 4


# ───────────────────────────── AST nodes ──────────────────────────────────
# Every node bakes its layout decisions (`broken`, comments) at generation time,
# so emission is a pure function of the tree. That is what makes --seed replay
# exact and shrinking sound (tree surgery + deterministic re-emit reproduces the
# same failure minus the removed part).

class E:
    """Base expression node. `pre` is an optional inline block-comment text
    (atoms only — a comment with continuation lines would misalign children)."""
    pre = None


class Int(E):
    def __init__(self, v): self.v = v

class Str(E):
    def __init__(self, v): self.v = v

class Var(E):
    def __init__(self, name): self.name = name

class Qual(E):
    def __init__(self, mod, name): self.mod, self.name = mod, name

class Ctor(E):
    def __init__(self, name): self.name = name

class Field(E):
    def __init__(self, base, field): self.base, self.field = base, field

class Paren(E):
    def __init__(self, inner, broken=False): self.inner, self.broken = inner, broken

class Call(E):
    def __init__(self, fn, args, broken=False):
        self.fn, self.args, self.broken = fn, args, broken

class Binop(E):
    def __init__(self, operands, ops, broken=False):
        self.operands, self.ops, self.broken = operands, ops, broken

class If(E):
    def __init__(self, cond, then, els, broken=False):
        self.cond, self.then, self.els, self.broken = cond, then, els, broken

class When(E):
    def __init__(self, scrut, branches):  # branches: [(pat, body, lead_comment)]
        self.scrut, self.branches = scrut, branches

class Let(E):
    def __init__(self, binds, body):  # binds: [LetBind]
        self.binds, self.body = binds, body

class LetBind:
    def __init__(self, lhs, val, lead=None, trailing=None):
        self.lhs, self.val, self.lead, self.trailing = lhs, val, lead, trailing

class Lambda(E):
    def __init__(self, params, body): self.params, self.body = params, body

class Record(E):
    def __init__(self, fields, broken=False):  # fields: [(name, val)]
        self.fields, self.broken = fields, broken

class Update(E):
    def __init__(self, base, fields, broken=False):
        self.base, self.fields, self.broken = base, fields, broken

class Array(E):
    def __init__(self, items, broken=False):
        self.items, self.broken = items, broken


# Patterns (single line by construction)
class PVar:
    def __init__(self, name): self.name = name
class PWild: pass
class PInt:
    def __init__(self, v): self.v = v
class PCtor:
    def __init__(self, name, args): self.name, self.args = name, args
class PRecord:
    def __init__(self, fields): self.fields = fields


class Decl:
    def __init__(self, name, params, body, sig=None, sig_broken=False,
                 lead=None, trailing=None):
        self.name, self.params, self.body = name, params, body
        self.sig, self.sig_broken = sig, sig_broken
        self.lead, self.trailing = lead, trailing


# Type-alias / custom-type / port declarations. Unlike Decl (function), these
# carry no expression body, so the shrinker's expr/list machinery (which walks
# `.body`) must skip them — only the "drop this whole decl" step applies.

class TypeAliasDecl:
    def __init__(self, name, params, rhs, broken=False, lead=None, trailing=None):
        self.name, self.params, self.rhs, self.broken = name, params, rhs, broken
        self.lead, self.trailing = lead, trailing


class Variant:
    def __init__(self, name, payload=None, lead=None, trailing=None):
        # payload: None | ("record", [(field, type), ...]) | ("args", [type, ...])
        self.name, self.payload = name, payload
        self.lead, self.trailing = lead, trailing


class UnionDecl:
    def __init__(self, name, params, variants, broken=False, lead=None, trailing=None):
        self.name, self.params, self.variants, self.broken = name, params, variants, broken
        self.lead, self.trailing = lead, trailing


class PortDecl:
    def __init__(self, name, type_, broken=False, lead=None, trailing=None):
        self.name, self.type_, self.broken = name, type_, broken
        self.lead, self.trailing = lead, trailing


class Module:
    def __init__(self, name, imports, decls):
        self.name, self.imports, self.decls = name, imports, decls


# ───────────────────────────── structural queries ─────────────────────────

BLOCK = (If, When, Let, Lambda)

def is_block(n):
    return isinstance(n, BLOCK)

def multiline(n):
    """Does this node render across >1 line? Structural (col-independent)."""
    if isinstance(n, (Int, Str, Var, Qual, Ctor)):
        return False
    if isinstance(n, Field):
        return multiline(n.base)
    if isinstance(n, Paren):
        return n.broken or multiline(n.inner)
    if isinstance(n, Call):
        return n.broken or any(multiline(a) for a in n.args)
    if isinstance(n, Binop):
        return n.broken or any(multiline(o) for o in n.operands)
    if isinstance(n, If):
        return n.broken or multiline(n.cond) or multiline(n.then) or multiline(n.els)
    if isinstance(n, (When, Let)):
        return True
    if isinstance(n, Lambda):
        return multiline(n.body)
    if isinstance(n, Record):
        return n.broken or any(multiline(v) for _, v in n.fields)
    if isinstance(n, Update):
        return n.broken or any(multiline(v) for _, v in n.fields)
    if isinstance(n, Array):
        return n.broken or any(multiline(v) for v in n.items)
    return False


# ───────────────────────────── emission ───────────────────────────────────
# emit(node, col) -> list[str]. Line 0 is HEADLESS (no leading indent — placed by
# the caller right after whatever precedes it). Lines 1.. are ABSOLUTE (carry
# their own indentation, computed from `col`, the column the head starts at).

def pad(n): return " " * n

def emit(n, col):
    if isinstance(n, Int):   return [_inline(n, str(n.v))]
    if isinstance(n, Str):   return [_inline(n, '"' + n.v + '"')]
    if isinstance(n, Var):   return [_inline(n, n.name)]
    if isinstance(n, Qual):  return [_inline(n, n.mod + "." + n.name)]
    if isinstance(n, Ctor):  return [_inline(n, n.name)]
    if isinstance(n, Field):
        base = one_line(n.base)
        return [_inline(n, base + "." + n.field)]
    if isinstance(n, Paren):  return emit_paren(n, col)
    if isinstance(n, Call):   return emit_call(n, col)
    if isinstance(n, Binop):  return emit_binop(n, col)
    if isinstance(n, If):     return emit_if(n, col)
    if isinstance(n, When):   return emit_when(n, col)
    if isinstance(n, Let):    return emit_let(n, col)
    if isinstance(n, Lambda): return emit_lambda(n, col)
    if isinstance(n, Record): return emit_record(n.fields, None, n.broken, col)
    if isinstance(n, Update): return emit_record(n.fields, n.base, n.broken, col)
    if isinstance(n, Array):  return emit_array(n, col)
    raise ValueError("emit: unknown node " + type(n).__name__)


def _inline(n, s):
    """Prepend an inline block comment to a single-line atom, if present."""
    if getattr(n, "pre", None):
        return "{- " + n.pre + " -} " + s
    return s


def one_line(n):
    """Emit a node that MUST be single line (atoms / inline positions)."""
    ls = emit(n, 0)
    assert len(ls) == 1, "one_line on multiline node " + type(n).__name__
    return ls[0]


def emit_paren(n, col):
    if not n.broken and not multiline(n.inner):
        return ["( " + one_line(n.inner) + " )"]
    inner = emit(n.inner, col + 2)
    out = ["( " + inner[0]] + inner[1:]
    out.append(pad(col) + ")")
    return out


def emit_call(n, col):
    fn = one_line(n.fn)
    if not n.broken and not any(multiline(a) for a in n.args):
        return [fn + "".join(" " + one_line(a) for a in n.args)]
    # broken: glue fn + first arg on the head line, rest own-line at col+4
    if not n.args:
        return [fn]
    a0col = col + len(fn) + 1
    a0 = emit(n.args[0], a0col)
    out = [fn + " " + a0[0]] + a0[1:]
    for a in n.args[1:]:
        al = emit(a, col + INDENT)
        out.append(pad(col + INDENT) + al[0])
        out += al[1:]
    return out


def emit_binop(n, col):
    ops = n.ops
    if not n.broken and not any(multiline(o) for o in n.operands):
        parts = [one_line(n.operands[0])]
        for i, op in enumerate(ops):
            parts.append(op + " " + one_line(n.operands[i + 1]))
        return [" ".join(parts)]
    head = emit(n.operands[0], col)
    out = list(head)
    for i, op in enumerate(ops):
        prefix = op + " "
        ocol = col + INDENT + len(prefix)
        ol = emit(n.operands[i + 1], ocol)
        out.append(pad(col + INDENT) + prefix + ol[0])
        out += ol[1:]
    return out


def emit_if(n, col):
    inline = (not n.broken and not multiline(n.cond)
              and not multiline(n.then) and not multiline(n.els))
    if inline:
        return ["if " + one_line(n.cond) + " then "
                + one_line(n.then) + " else " + one_line(n.els)]
    cond = one_line(n.cond)
    out = ["if " + cond + " then"]
    out += own_line(n.then, col + INDENT)
    out.append("")
    out.append(pad(col) + "else")
    out += own_line(n.els, col + INDENT)
    return out


def emit_when(n, col):
    out = ["when " + one_line(n.scrut) + " is"]
    for i, (pat, body, lead) in enumerate(n.branches):
        if lead is not None:
            out += comment_lines(lead, col + INDENT)
        out.append(pad(col + INDENT) + emit_pat(pat) + " ->")
        out += own_line(body, col + 2 * INDENT)
        if i != len(n.branches) - 1:
            out.append("")
    return out


def emit_let(n, col):
    out = ["let"]
    bcol = col + INDENT
    for b in n.binds:
        if b.lead is not None:
            out += comment_lines(b.lead, bcol)
        head = emit_binding(b.lhs, b.val, bcol)
        line0 = head[0]
        if b.trailing is not None:
            # trailing comment rides the last line of the value
            head = head[:-1] + [head[-1] + " " + comment_text(b.trailing)] \
                if len(head) > 1 else [line0 + " " + comment_text(b.trailing)]
        out.append(pad(bcol) + head[0])
        out += head[1:]
    out.append(pad(col) + "in")
    out += own_line(n.body, col)
    return out


def emit_binding(lhs, val, col):
    """`lhs = val` at column `col` (headless line0). Block/multiline value drops
    to its own line indented +4; otherwise it hangs after `= `."""
    prefix = emit_pat(lhs) + " = "
    if multiline(val):
        out = [emit_pat(lhs) + " ="]
        out += own_line(val, col + INDENT)
        return out
    vl = emit(val, col + len(prefix))
    return [prefix + vl[0]] + vl[1:]


def emit_lambda(n, col):
    prefix = "\\" + " ".join(emit_pat(p) for p in n.params) + " -> "
    if multiline(n.body):
        out = ["\\" + " ".join(emit_pat(p) for p in n.params) + " ->"]
        out += own_line(n.body, col + INDENT)
        return out
    bl = emit(n.body, col + len(prefix))
    return [prefix + bl[0]] + bl[1:]


def emit_record(fields, base, broken, col):
    open_tok = "{ " + (base + " | " if base else "")
    if not fields and not base:
        return ["{}"]
    inline_ok = not broken and not any(multiline(v) for _, v in fields)
    if inline_ok:
        body = ", ".join(f + " = " + one_line(v) for f, v in fields)
        return [open_tok + body + " }"]
    out = []
    for i, (f, v) in enumerate(fields):
        prefix = (open_tok if i == 0 else ", ") + f + " = "
        vl = emit(v, col + len(prefix))
        line0 = prefix + vl[0]
        out.append(line0 if i == 0 else pad(col) + line0)
        out += vl[1:]
    out.append(pad(col) + "}")
    return out


def emit_array(n, col):
    if not n.items:
        return ["[]"]
    if not n.broken and not any(multiline(v) for v in n.items):
        return ["[ " + ", ".join(one_line(v) for v in n.items) + " ]"]
    out = []
    for i, v in enumerate(n.items):
        prefix = "[ " if i == 0 else ", "
        vl = emit(v, col + len(prefix))
        line0 = prefix + vl[0]
        out.append(line0 if i == 0 else pad(col) + line0)
        out += vl[1:]
    out.append(pad(col) + "]")
    return out


def own_line(n, ind):
    """Emit node on its own line(s) starting at absolute column `ind`."""
    ls = emit(n, ind)
    return [pad(ind) + ls[0]] + ls[1:]


def comment_text(c):
    kind, text = c
    return ("-- " + text) if kind == "line" else ("{- " + text + " -}")


def comment_lines(c, ind):
    return [pad(ind) + comment_text(c)]


def emit_pat(p):
    if isinstance(p, PVar):  return p.name
    if isinstance(p, PWild): return "_"
    if isinstance(p, PInt):  return str(p.v)
    if isinstance(p, PRecord): return "{ " + ", ".join(p.fields) + " }"
    if isinstance(p, PCtor):
        if not p.args:
            return p.name
        parts = []
        for a in p.args:
            s = emit_pat(a)
            if isinstance(a, PCtor) and a.args:
                s = "(" + s + ")"
            parts.append(s)
        return p.name + " " + " ".join(parts)
    raise ValueError("emit_pat: " + type(p).__name__)


# ───────────────────────────── type signatures ─────────────────────────────
# emit_type renders a type as ONE line — used for nested/inner types (record
# fields, app args, a paren'd atom) which are never author-broken on their
# own. emit_type_multiline is the top-level entry point for a signature / type
# alias RHS / port type, where the author's flat-vs-broken choice is baked in.

def emit_type(t):
    # t is a small tuple-based type IR built by gen_type
    kind = t[0]
    if kind == "con":  return t[1]
    if kind == "var":  return t[1]
    if kind == "app":  return t[1] + " " + " ".join(_type_atom(a) for a in t[2])
    if kind == "arrow": return " -> ".join(emit_type(x) for x in t[1])
    if kind == "record":
        return "{ " + ", ".join(f + " : " + emit_type(ft) for f, ft in t[1]) + " }"
    if kind == "paren":
        return "(" + emit_type(t[1]) + ")"
    raise ValueError("emit_type")

def _type_atom(t):
    s = emit_type(t)
    if t[0] in ("app", "arrow"):
        return "(" + s + ")"
    return s


def _flatten_arrow(t):
    """gen_type's "arrow" branch can recursively nest an arrow inside one of
    its own elements (e.g. `("arrow", [("arrow", [A, B]), C])`) — harmless for
    single-line emit_type (string-joining with the same " -> " separator is
    associative, so it renders identically to a flat chain), but the per-line
    segment breaker below needs the TRUE flat segment list, matching how the
    type is actually written: `A -> B -> C` is always one flat chain, and the
    only way to make a sub-arrow its own segment is to wrap it in `paren`
    (which this does NOT recurse into — a paren'd arrow is genuinely one
    opaque segment, e.g. `(String -> Bool)` in README's own example)."""
    if t[0] != "arrow":
        return [t]
    out = []
    for x in t[1]:
        out += _flatten_arrow(x)
    return out


def emit_type_multiline(t, broken):
    """Emit a top-level signature/alias/port type. Per README "Type
    signatures": written across rows, the canonical shape puts each `->`
    segment on its own line, `->` leading each continuation — so this only
    ever applies when `t` is an arrow chain; a non-arrow RHS (record, con,
    var, app) has no `->` boundary to break at and always stays inline."""
    if not broken or t[0] != "arrow":
        return [emit_type(t)]
    segs = _flatten_arrow(t)
    return [emit_type(segs[0])] + ["-> " + emit_type(s) for s in segs[1:]]


# ───────────────────────── type alias / union / port emission ─────────────
# Per README: a `type alias` RHS and a custom type's variant list ALWAYS drop
# to their own line(s) below the header, indented 4 — never glued to `=`, even
# when they'd fit on one line.

def emit_type_alias(d):
    header = "type alias " + d.name + "".join(" " + p for p in d.params) + " ="
    return [header] + [pad(INDENT) + l for l in emit_type_multiline(d.rhs, d.broken)]


def emit_variant_payload(payload):
    kind, val = payload
    if kind == "record":
        return emit_type(("record", val))
    return " ".join(_type_atom(t) for t in val)


def emit_union(d):
    header = "type " + d.name + "".join(" " + p for p in d.params)
    parts = []
    for i, v in enumerate(d.variants):
        s = ("= " if i == 0 else "| ") + v.name
        if v.payload is not None:
            s += " " + emit_variant_payload(v.payload)
        if v.trailing is not None:
            s += " " + comment_text(v.trailing)
        parts.append(s)
    lines = [header]
    if not d.broken:
        lines.append(pad(INDENT) + " ".join(parts))
    else:
        for s, v in zip(parts, d.variants):
            if v.lead is not None:
                lines.append(pad(INDENT) + comment_text(v.lead))
            lines.append(pad(INDENT) + s)
    return lines


def emit_port(d):
    if d.broken and d.type_[0] == "arrow":
        return ["port " + d.name + " :"] + \
               [pad(INDENT) + l for l in emit_type_multiline(d.type_, True)]
    return ["port " + d.name + " : " + emit_type(d.type_)]


# ───────────────────────────── module emission ────────────────────────────

def emit_module(m):
    kw = "port module " if any(isinstance(d, PortDecl) for d in m.decls) else "module "
    lines = [kw + m.name + " exposing (..)", ""]
    for imp in m.imports:
        lines.append(imp)
    if m.imports:
        lines.append("")
    lines.append("")
    body = []
    for i, d in enumerate(m.decls):
        body += emit_decl(d)
        if i != len(m.decls) - 1:
            body.append("")
            body.append("")
    return "\n".join(lines + body) + "\n"


def emit_decl(d):
    if isinstance(d, TypeAliasDecl):
        core = emit_type_alias(d)
    elif isinstance(d, UnionDecl):
        core = emit_union(d)
    elif isinstance(d, PortDecl):
        core = emit_port(d)
    else:
        return emit_function_decl(d)
    out = []
    if d.lead:
        for c in d.lead:
            out.append(comment_text(c))
    out += core
    if d.trailing is not None:
        out[-1] = out[-1] + " " + comment_text(d.trailing)
    return out


def emit_function_decl(d):
    out = []
    if d.lead:
        for c in d.lead:
            out.append(comment_text(c))
    if d.sig is not None:
        if d.sig_broken and d.sig[0] == "arrow":
            out.append(d.name + " :")
            out += [pad(INDENT) + l for l in emit_type_multiline(d.sig, True)]
        else:
            out.append(d.name + " : " + emit_type(d.sig))
    prefix = d.name + "".join(" " + emit_pat(p) for p in d.params) + " = "
    if multiline(d.body):
        out.append(d.name + "".join(" " + emit_pat(p) for p in d.params) + " =")
        out += own_line(d.body, INDENT)
    else:
        bl = emit(d.body, len(prefix))
        out.append(prefix + bl[0])
        out += bl[1:]
    if d.trailing is not None:
        out[-1] = out[-1] + " " + comment_text(d.trailing)
    return out


# ───────────────────────────── generator ──────────────────────────────────

class Gen:
    def __init__(self, rng, max_depth, comment_rate):
        self.rng = rng
        self.max_depth = max_depth
        self.crate = comment_rate
        self.cid = 0
        self.vars = ["x", "y", "z", "acc", "item", "node"]
        self.fields = ["name", "count", "value", "next", "kind"]
        self.ctors = ["Just", "Nothing", "Ok", "Err", "Leaf", "Node"]
        self.mods = ["String", "Array", "Dict", "Maybe"]

    def chance(self, p): return self.rng.random() < p
    def pick(self, xs):  return self.rng.choice(xs)

    def comment(self, kinds=("line", "block")):
        """Maybe return a fresh unique comment (kind, text), else None."""
        if not self.chance(self.crate):
            return None
        kind = self.pick(list(kinds))
        text = "k%d" % self.cid
        self.cid += 1
        return (kind, text)

    # -- expressions -------------------------------------------------------

    def atom(self, depth):
        """Single-line, argument-safe: leaf / field / qualified / parenthesized."""
        r = self.rng.random()
        if depth <= 0 or r < 0.5:
            n = self.leaf()
        elif r < 0.65:
            n = Field(self.field_base(depth), self.pick(self.fields))
        elif r < 0.8:
            n = Paren(self.inline(depth - 1))
        else:
            n = Paren(self.value(depth - 1))  # parenthesize anything (may break)
        self.maybe_inline_comment(n)
        return n

    def field_base(self, depth):
        """A base that can legally take `.field` — a var or a parenthesized expr
        (never a numeric/string literal: `3.f` mis-lexes as a float)."""
        if depth <= 0 or self.chance(0.8):
            return Var(self.pick(self.vars))
        return Paren(self.inline(depth - 1))  # base of `.field` must be single line

    def leaf(self):
        r = self.rng.random()
        if r < 0.4:   return Var(self.pick(self.vars))
        if r < 0.6:   return Int(self.rng.randint(0, 99))
        if r < 0.75:  return Str(self.pick(WORDS))
        if r < 0.9:   return Ctor(self.pick(self.ctors))
        return Qual(self.pick(self.mods), self.pick(self.vars))

    def maybe_inline_comment(self, n):
        # inline comments ride single-line atoms only
        if isinstance(n, (Int, Str, Var, Qual, Ctor)) and n.pre is None:
            c = self.comment(kinds=("block",))
            if c:
                n.pre = c[1]

    def inline(self, depth):
        """A guaranteed single-line expression (for cond / scrutinee)."""
        r = self.rng.random()
        if depth <= 0 or r < 0.55:
            return self.leaf()
        if r < 0.75:
            return Field(Var(self.pick(self.vars)), self.pick(self.fields))
        if r < 0.9:
            # inline call: fn + atom args, all single line
            k = self.rng.randint(1, 2)
            return Call(Var(self.pick(self.vars)),
                        [self.leaf() for _ in range(k)], broken=False)
        return Paren(self.inline(depth - 1))

    def arg(self, depth):
        """Argument / operand position: atom or parenthesized anything."""
        if self.chance(0.7):
            return self.atom(depth)
        return Paren(self.value(depth - 1),
                     broken=self.chance(0.4))

    def value(self, depth):
        """Value position (def body, field value, item, branch body): block
        expressions are allowed BARE here."""
        if depth <= 0:
            return self.leaf()
        r = self.rng.random()
        d = depth - 1
        if r < 0.14:  return self.mk_call(d)
        if r < 0.30:  return self.mk_binop(d)
        if r < 0.42:  return self.mk_record(d)
        if r < 0.50:  return self.mk_update(d)
        if r < 0.60:  return self.mk_array(d)
        if r < 0.70:  return self.mk_if(d)
        if r < 0.80:  return self.mk_when(d)
        if r < 0.88:  return self.mk_let(d)
        if r < 0.94:  return self.mk_lambda(d)
        return self.atom(d)

    def mk_call(self, d):
        k = self.rng.randint(1, 3)
        return Call(Var(self.pick(self.vars)),
                    [self.arg(d) for _ in range(k)],
                    broken=self.chance(0.5))

    def mk_binop(self, d):
        k = self.rng.randint(2, 4)
        operands = [self.arg(d) for _ in range(k)]
        pool = BINOPS + PIPES
        ops = [self.pick(pool) for _ in range(k - 1)]
        return Binop(operands, ops, broken=self.chance(0.5))

    def mk_record(self, d):
        k = self.rng.randint(0, 3)
        fields = [(self.pick(self.fields) + str(i), self.value(d)) for i in range(k)]
        return Record(fields, broken=self.chance(0.5))

    def mk_update(self, d):
        k = self.rng.randint(1, 3)
        fields = [(self.pick(self.fields) + str(i), self.value(d)) for i in range(k)]
        return Update(self.pick(self.vars), fields, broken=self.chance(0.5))

    def mk_array(self, d):
        k = self.rng.randint(0, 3)
        return Array([self.value(d) for _ in range(k)], broken=self.chance(0.5))

    def mk_if(self, d):
        return If(self.inline(d), self.arg(d), self.value(d),
                  broken=self.chance(0.6))

    def mk_when(self, d):
        k = self.rng.randint(1, 3)
        branches = []
        for _ in range(k):
            branches.append((self.pattern(d), self.value(d), self.comment()))
        return When(self.inline(d), branches)

    def mk_let(self, d):
        k = self.rng.randint(1, 3)
        binds = []
        for _ in range(k):
            binds.append(LetBind(PVar(self.pick(self.vars)), self.value(d),
                                 lead=self.comment(),
                                 trailing=self.comment()))
        return Let(binds, self.value(d))

    def mk_lambda(self, d):
        k = self.rng.randint(1, 2)
        return Lambda([self.pattern(d) for _ in range(k)], self.value(d))

    # -- patterns ----------------------------------------------------------

    def pattern(self, depth):
        r = self.rng.random()
        if r < 0.45:  return PVar(self.pick(self.vars))
        if r < 0.6:   return PWild()
        if r < 0.7:   return PInt(self.rng.randint(0, 9))
        if r < 0.82:  return PRecord([self.pick(self.fields) for _ in range(self.rng.randint(1, 2))])
        # Constructor pattern. Current Gren allows AT MOST ONE argument (a
        # multi-field variant carries a record); `Ctor a b` does not parse.
        if depth <= 0 or self.chance(0.4):
            return PCtor(self.pick(self.ctors), [])
        return PCtor(self.pick(self.ctors), [self.pattern(depth - 1)])

    # -- types (inline only) ----------------------------------------------

    def gen_type(self, depth, vars=None):
        r = self.rng.random()
        cons = ["Int", "Float", "String", "Bool", "Char"]
        var_pool = vars if vars else ["a", "b", "c"]
        if depth <= 0 or r < 0.4:
            return ("con", self.pick(cons))
        if r < 0.55:
            return ("var", self.pick(var_pool))
        if r < 0.7:
            return ("app", self.pick(["Array", "Maybe"]),
                    [("var", self.pick(var_pool))])
        if r < 0.85:
            k = self.rng.randint(2, 3)
            return ("arrow", [self.gen_type(depth - 1, vars) for _ in range(k)])
        k = self.rng.randint(1, 2)
        return ("record", [(self.pick(self.fields) + str(i), self.gen_type(depth - 1, vars))
                           for i in range(k)])

    # -- declarations / module --------------------------------------------

    def decl(self, i):
        name = "fn%d" % i
        nparams = self.rng.randint(0, 3)
        params = [self.pattern(self.max_depth) for _ in range(nparams)]
        body = self.value(self.max_depth)
        sig = None
        sig_broken = False
        if self.chance(0.4):
            k = nparams + 1
            sig = ("arrow", [self.gen_type(2) for _ in range(k)]) if k > 1 \
                  else self.gen_type(2)
            sig_broken = sig[0] == "arrow" and self.chance(0.5)
        lead = None
        if self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return Decl(name, params, body, sig=sig, sig_broken=sig_broken,
                    lead=lead, trailing=trailing)

    def type_params(self):
        if not self.chance(0.4):
            return []
        return self.rng.sample(["a", "b"], self.rng.randint(1, 2))

    def type_alias(self, i):
        name = "Alias%d" % i
        params = self.type_params()
        rhs = self.gen_type(2, params)
        broken = rhs[0] == "arrow" and self.chance(0.5)
        lead = None
        if self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return TypeAliasDecl(name, params, rhs, broken=broken, lead=lead, trailing=trailing)

    def variant_arg_type(self, depth, params):
        """A union variant's single positional argument. Current Gren limits a
        variant to 0 or 1 argument (a multi-field variant carries a record
        instead — see `variant_payload`'s "record" case), so this never needs
        to worry about position; it just needs to avoid `record` itself
        (which goes through the dedicated "record" payload kind, not here).
        The parser this generator targets does NOT enforce that 0-or-1 limit
        for a chain of bare constructor-name arguments (compiler-common#32:
        https://github.com/gren-lang/compiler-common/issues/32) — this
        generator still caps arity at 1 to match current valid Gren rather
        than lean on that gap."""
        r = self.rng.random()
        cons = ["Int", "Float", "String", "Bool", "Char"]
        var_pool = params if params else ["a", "b", "c"]
        if depth <= 0 or r < 0.45:
            return ("con", self.pick(cons))
        if r < 0.65:
            return ("var", self.pick(var_pool))
        if r < 0.85:
            return ("app", self.pick(["Array", "Maybe"]), [("var", self.pick(var_pool))])
        k = self.rng.randint(2, 3)
        return ("arrow", [self.variant_arg_type(depth - 1, params) for _ in range(k)])

    def variant_payload(self, params):
        r = self.rng.random()
        if r < 0.4:
            return None
        if r < 0.7:
            k = self.rng.randint(1, 2)
            fields = [(self.pick(self.fields) + str(j), self.gen_type(1, params))
                      for j in range(k)]
            return ("record", fields)
        return ("args", [self.variant_arg_type(1, params)])

    def union(self, i):
        name = "Union%d" % i
        params = self.type_params()
        k = self.rng.randint(1, 4)
        variants = []
        for _ in range(k):
            vname = self.pick(["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]) \
                    + str(self.next_cid())
            lead = self.comment() if self.chance(0.3) else None
            trailing = self.comment()
            variants.append(Variant(vname, self.variant_payload(params),
                                    lead=lead, trailing=trailing))
        # A `--` trailing comment or an own-line lead comment can't share the
        # variant-list's flat line (README "Custom types"), so either forces
        # the broken (one-variant-per-line) layout.
        forced = any(v.lead is not None for v in variants) or \
                 any(v.trailing is not None and v.trailing[0] == "line" for v in variants)
        broken = forced or self.chance(0.5)
        lead = None
        if self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return UnionDecl(name, params, variants, broken=broken, lead=lead, trailing=trailing)

    def port(self, i):
        name = "port%d" % i
        if self.chance(0.5):
            # outgoing: Type -> ... -> Cmd msg
            k = self.rng.randint(1, 2)
            segs = [self.gen_type(1) for _ in range(k)] + [("app", "Cmd", [("var", "msg")])]
            t = ("arrow", segs) if len(segs) > 1 else segs[0]
        else:
            # incoming: (Type -> msg) -> Sub msg
            inner = ("arrow", [self.gen_type(1), ("var", "msg")])
            t = ("arrow", [("paren", inner), ("app", "Sub", [("var", "msg")])])
        broken = t[0] == "arrow" and self.chance(0.5)
        lead = None
        if self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return PortDecl(name, t, broken=broken, lead=lead, trailing=trailing)

    def next_cid(self):
        c = self.cid
        self.cid += 1
        return c

    def module(self):
        name = "Gen%d" % self.rng.randint(0, 999)
        nimp = self.rng.randint(0, 3)
        imports = []
        for i in range(nimp):
            r = self.rng.random()
            mod = self.pick(["Foo", "Bar", "Baz", "Qux"]) + str(i)
            if r < 0.5:
                imports.append("import " + mod)
            elif r < 0.75:
                imports.append("import " + mod + " as M" + str(i))
            else:
                names = ", ".join(self.pick(self.vars) for _ in range(self.rng.randint(1, 2)))
                imports.append("import " + mod + " exposing (" + names + ")")
        ndecls = self.rng.randint(1, 4)
        decls = []
        for i in range(ndecls):
            r = self.rng.random()
            if r < 0.65:
                decls.append(self.decl(i))
            elif r < 0.8:
                decls.append(self.type_alias(i))
            elif r < 0.95:
                decls.append(self.union(i))
            else:
                decls.append(self.port(i))
        return Module(name, imports, decls)


def generate(seed, max_depth, comment_rate):
    rng = random.Random(seed)
    return Gen(rng, max_depth, comment_rate).module()


# ───────────────────────────── oracles ────────────────────────────────────

def run_app(args, timeout=60):
    return subprocess.run(["node", APP] + args, capture_output=True,
                          text=True, timeout=timeout)


def first_real_line(out):
    for line in out.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return s
    ls = out.splitlines()
    return ls[0] if ls else ""


def comment_multiset(path):
    """(type, normalizedText) multiset from the real lexer via --pre-context."""
    r = run_app(["--pre-context", path])
    if r.returncode != 0:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    ms = {}
    for c in data.get("comments", []):
        t = c.get("type")
        v = c.get("value", "")
        key = (t, v.rstrip() if t == "line" else v)
        ms[key] = ms.get(key, 0) + 1
    return ms


def check(src, tmpdir):
    """Run all oracles on `src`. Returns (bucket, detail_dict).
    bucket == 'ok' on full pass; 'quarantine' for a parse failure (generator bug).
    """
    inp = os.path.join(tmpdir, "input.gren")
    with open(inp, "w") as f:
        f.write(src)

    # Oracle 1: parses at all? A failure is a GENERATOR bug, not a formatter find.
    try:
        pre = run_app(["--pre-ast", inp])
    except subprocess.TimeoutExpired:
        return "quarantine", {"msg": "parse timed out"}
    if pre.returncode != 0 or "FAILED TO PARSE" in (pre.stdout + pre.stderr):
        return "quarantine", {"msg": first_real_line(pre.stdout + pre.stderr)}

    # Oracle 2: --show buys no-crash + AST-equiv + idempotent + reparses.
    try:
        show = run_app(["--show", inp])
    except subprocess.TimeoutExpired:
        return "timeout", {"msg": "format timed out (>60s)"}
    out = show.stdout + show.stderr
    if show.returncode != 0:
        if "NOT IDEMPOTENT" in out:
            bucket = "non-idempotent"
        elif "Please report this" in out or "box:" in out or "unreachable" in out:
            bucket = "crash"
        else:
            bucket = "ast-mismatch"
        return bucket, {"msg": first_real_line(out), "stderr": out}

    # Oracle 3: comment preservation (the 4th oracle the generator enables).
    formatted = show.stdout
    fmt = os.path.join(tmpdir, "formatted.gren")
    with open(fmt, "w") as f:
        f.write(formatted)
    before = comment_multiset(inp)
    after = comment_multiset(fmt)
    if before is not None and after is not None and before != after:
        missing = _ms_diff(before, after)
        extra = _ms_diff(after, before)
        return "comment-loss", {"msg": "comments changed",
                                "missing": missing, "extra": extra,
                                "formatted": formatted}
    return "ok", {"formatted": formatted}


def _ms_diff(a, b):
    out = []
    for k, n in a.items():
        d = n - b.get(k, 0)
        if d > 0:
            out.append((k[0], k[1], d))
    return out


# ───────────────────────────── shrinking ──────────────────────────────────

TRIVIAL = (Int, Var, Ctor, Qual)

def expr_slots(m):
    """Yield (node, setter) for every EXPR position, pre-order."""
    def walk(node, setter):
        yield node, setter
        for child, cset in child_slots(node):
            yield from walk(child, cset)
    for i, d in enumerate(m.decls):
        if isinstance(d, Decl):
            yield from walk(d.body, _attr_setter(d, "body"))


def child_slots(n):
    out = []
    if isinstance(n, Field):
        out.append((n.base, _attr_setter(n, "base")))
    elif isinstance(n, Paren):
        out.append((n.inner, _attr_setter(n, "inner")))
    elif isinstance(n, Call):
        out.append((n.fn, _attr_setter(n, "fn")))
        for i in range(len(n.args)):
            out.append((n.args[i], _list_setter(n.args, i)))
    elif isinstance(n, Binop):
        for i in range(len(n.operands)):
            out.append((n.operands[i], _list_setter(n.operands, i)))
    elif isinstance(n, If):
        out.append((n.cond, _attr_setter(n, "cond")))
        out.append((n.then, _attr_setter(n, "then")))
        out.append((n.els, _attr_setter(n, "els")))
    elif isinstance(n, When):
        out.append((n.scrut, _attr_setter(n, "scrut")))
        for i in range(len(n.branches)):
            out.append((n.branches[i][1], _branch_body_setter(n.branches, i)))
    elif isinstance(n, Let):
        for b in n.binds:
            out.append((b.val, _attr_setter(b, "val")))
        out.append((n.body, _attr_setter(n, "body")))
    elif isinstance(n, Lambda):
        out.append((n.body, _attr_setter(n, "body")))
    elif isinstance(n, (Record, Update)):
        for i in range(len(n.fields)):
            out.append((n.fields[i][1], _field_setter(n.fields, i)))
    elif isinstance(n, Array):
        for i in range(len(n.items)):
            out.append((n.items[i], _list_setter(n.items, i)))
    return out


def _attr_setter(obj, attr):
    def s(v): setattr(obj, attr, v)
    return s

def _list_setter(lst, i):
    def s(v): lst[i] = v
    return s

def _field_setter(fields, i):
    def s(v): fields[i] = (fields[i][0], v)
    return s

def _branch_body_setter(branches, i):
    def s(v): branches[i] = (branches[i][0], v, branches[i][2])
    return s


def list_containers(m):
    """Yield (owner, attr, min_len) for every reducible list in the module."""
    yield m, "decls", 1
    for d in m.decls:
        if isinstance(d, UnionDecl):
            yield d, "variants", 1
        if not isinstance(d, Decl):
            continue
        for node in _all_nodes(d.body):
            if isinstance(node, Call):
                yield node, "args", 0
            elif isinstance(node, Array):
                yield node, "items", 0
            elif isinstance(node, (Record, Update)):
                yield node, "fields", 0
            elif isinstance(node, When):
                yield node, "branches", 1
            elif isinstance(node, Let):
                yield node, "binds", 1


def _all_nodes(n):
    yield n
    for c, _ in child_slots(n):
        yield from _all_nodes(c)


def comment_clearers(m):
    """Yield closures that remove one comment (for the shrinker)."""
    def clear_attr(obj, attr):
        return lambda: setattr(obj, attr, None)
    for d in m.decls:
        if d.lead:
            yield clear_attr(d, "lead")
        if d.trailing is not None:
            yield clear_attr(d, "trailing")
        if isinstance(d, UnionDecl):
            for v in d.variants:
                if v.lead is not None:
                    yield clear_attr(v, "lead")
                if v.trailing is not None:
                    yield clear_attr(v, "trailing")
        if not isinstance(d, Decl):
            continue
        for node in _all_nodes(d.body):
            if getattr(node, "pre", None):
                yield clear_attr(node, "pre")
            if isinstance(node, Let):
                for b in node.binds:
                    if b.lead is not None:
                        yield clear_attr(b, "lead")
                    if b.trailing is not None:
                        yield clear_attr(b, "trailing")
            if isinstance(node, When):
                for i in range(len(node.branches)):
                    if node.branches[i][2] is not None:
                        def clr(br=node.branches, idx=i):
                            br[idx] = (br[idx][0], br[idx][1], None)
                        yield clr


def variants(m):
    """All one-step reductions of module m (each a fresh deepcopy)."""
    # 1. drop a top-level decl
    if len(m.decls) > 1:
        for i in range(len(m.decls)):
            c = copy.deepcopy(m)
            del c.decls[i]
            yield c
    # 2. drop a list item
    base = copy.deepcopy(m)
    conts = list(list_containers(base))
    for idx in range(len(conts)):
        owner, attr, minlen = conts[idx]
        lst = getattr(owner, attr)
        if len(lst) > minlen:
            for j in range(len(lst)):
                c = copy.deepcopy(base)
                oc, ac, _ = list(list_containers(c))[idx]
                getattr(oc, ac).pop(j)
                yield c
    # 3. replace an expr subtree with a trivial atom
    slots = list(expr_slots(m))
    for k in range(len(slots)):
        node, _ = slots[k]
        if isinstance(node, TRIVIAL):
            continue
        c = copy.deepcopy(m)
        cslots = list(expr_slots(c))
        cslots[k][1](Int(0))
        yield c
    # 4. drop a comment
    clearers = list(comment_clearers(m))
    for k in range(len(clearers)):
        c = copy.deepcopy(m)
        list(comment_clearers(c))[k]()
        yield c


def shrink(m, target_bucket, tmpdir, budget=4000):
    cur = m
    steps = 0
    improved = True
    while improved and steps < budget:
        improved = False
        for v in variants(cur):
            steps += 1
            if steps >= budget:
                break
            try:
                src = emit_module(v)
            except Exception:
                continue
            bucket, _ = check(src, tmpdir)
            if bucket == target_bucket:
                cur = v
                improved = True
                break
    return cur


# ───────────────────────────── artifacts ──────────────────────────────────

def app_build_id():
    try:
        with open(APP, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return "unknown"


def next_run_dir(out_root):
    os.makedirs(out_root, exist_ok=True)
    n = 0
    while os.path.exists(os.path.join(out_root, "run-%06d" % n)):
        n += 1
    d = os.path.join(out_root, "run-%06d" % n)
    os.makedirs(d)
    return d


def write_report(fdir, seed, bucket, detail, src_full, src_min, tmpdir):
    os.makedirs(fdir, exist_ok=True)
    with open(os.path.join(fdir, "input.gren"), "w") as f:
        f.write(src_full)
    with open(os.path.join(fdir, "input.min.gren"), "w") as f:
        f.write(src_min)
    lines = [
        "class:   %s" % bucket,
        "seed:    %d" % seed,
        "reproduce: ./gen-random.py --seed %d" % seed,
        "message: %s" % detail.get("msg", ""),
        "",
    ]
    # relevant diff per class
    if bucket == "comment-loss":
        lines.append("MISSING (in input, absent from output):")
        for t, v, n in detail.get("missing", []):
            lines.append("  %dx %s: %r" % (n, t, v))
        lines.append("EXTRA (in output, absent from input):")
        for t, v, n in detail.get("extra", []):
            lines.append("  %dx %s: %r" % (n, t, v))
        fmt = check_and_format(src_min, tmpdir)
        if fmt is not None:
            with open(os.path.join(fdir, "formatted.gren"), "w") as f:
                f.write(fmt)
    elif bucket == "non-idempotent":
        f1 = format_once(src_min, tmpdir)
        if f1 is not None:
            with open(os.path.join(fdir, "formatted.gren"), "w") as f:
                f.write(f1)
            f2 = format_once(f1, tmpdir)
            if f2 is not None:
                with open(os.path.join(fdir, "formatted2.gren"), "w") as f:
                    f.write(f2)
                import difflib
                diff = difflib.unified_diff(f1.splitlines(), f2.splitlines(),
                                            "format1", "format2", lineterm="")
                lines.append("format1 vs format2:")
                lines += list(diff)
    else:
        lines.append("stderr:")
        lines.append(detail.get("stderr", detail.get("msg", "")))
    with open(os.path.join(fdir, "report.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def format_once(src, tmpdir):
    p = os.path.join(tmpdir, "fmt_in.gren")
    with open(p, "w") as f:
        f.write(src)
    try:
        r = run_app(["--show-first", p])
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


def check_and_format(src, tmpdir):
    p = os.path.join(tmpdir, "cf.gren")
    with open(p, "w") as f:
        f.write(src)
    try:
        r = run_app(["--show", p])
    except subprocess.TimeoutExpired:
        return None
    return r.stdout if r.returncode == 0 else None


# ───────────────────────────── worker ─────────────────────────────────────

def process_seed(args_tuple):
    seed, max_depth, comment_rate = args_tuple
    with tempfile.TemporaryDirectory() as tmp:
        try:
            m = generate(seed, max_depth, comment_rate)
            src = emit_module(m)
        except Exception as e:
            return seed, "gen-error", {"msg": repr(e)}, None, None
        bucket, detail = check(src, tmp)
        if bucket == "ok":
            return seed, "ok", detail, None, None
        if bucket == "quarantine":
            return seed, "quarantine", detail, src, None
        # a real find — shrink it
        try:
            minm = shrink(m, bucket, tmp)
            minsrc = emit_module(minm)
        except Exception:
            minsrc = src
        return seed, bucket, detail, src, minsrc


# ───────────────────────────── promote ────────────────────────────────────

def promote(out_root, seed, name):
    # find the failure dir for this seed in the latest run(s)
    for run in sorted(os.listdir(out_root), reverse=True):
        fdir = os.path.join(out_root, run, "failures", str(seed))
        minf = os.path.join(fdir, "input.min.gren")
        if os.path.exists(minf):
            dirty = os.path.join(TESTFILES, name + ".dirty.gren")
            shutil.copy(minf, dirty)
            r = subprocess.run(["node", APP, "--show", dirty],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print("WARNING: --show still fails on the promoted case:")
                print(r.stdout + r.stderr)
            formatted = os.path.join(TESTFILES, name + ".formatted.gren")
            with open(formatted, "w") as f:
                f.write(r.stdout)
            print("Wrote:")
            print("  " + dirty)
            print("  " + formatted + ("  (from a STILL-FAILING case — review!)"
                                       if r.returncode != 0 else ""))
            print("\nAdd to tests/src/Test/Formatter/Format.gren:")
            print('    |> assertPretty fsPerm "%s" "%s"'
                  % (name.lower(), name))
            return 0
    print("no failure dir found for seed %d under %s" % (seed, out_root))
    return 1


# ───────────────────────────── main ───────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=1000)
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--seed", type=int, default=None,
                    help="replay a single master seed (verbose, no artifacts)")
    ap.add_argument("--base-seed", type=int, default=1,
                    help="first seed of the sweep (seeds are base..base+n)")
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--comment-rate", type=float, default=0.25)
    ap.add_argument("--no-comments", action="store_true")
    ap.add_argument("--keep-all", action="store_true")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--promote", type=int, metavar="SEED")
    ap.add_argument("--name", help="fixture name for --promote")
    args = ap.parse_args()

    crate = 0.0 if args.no_comments else args.comment_rate

    if args.promote is not None:
        if not args.name:
            print("--promote requires --name", file=sys.stderr)
            return 2
        return promote(args.out, args.promote, args.name)

    if not os.path.exists(APP):
        print("app not found: %s\n(cd ../../gren-format && ./build.sh)" % APP,
              file=sys.stderr)
        return 2

    # Single-seed replay: print the source and the verdict, write nothing.
    if args.seed is not None:
        with tempfile.TemporaryDirectory() as tmp:
            m = generate(args.seed, args.max_depth, crate)
            src = emit_module(m)
            print(src)
            print("=" * 60)
            bucket, detail = check(src, tmp)
            print("seed %d -> %s: %s" % (args.seed, bucket, detail.get("msg", "")))
            if bucket not in ("ok", "quarantine"):
                minm = shrink(m, bucket, tmp)
                print("=" * 60)
                print("SHRUNK:")
                print(emit_module(minm))
        return 0

    seeds = list(range(args.base_seed, args.base_seed + args.count))
    jobs = [(s, args.max_depth, crate) for s in seeds]

    print("generating %d modules (seeds %d..%d, -j %d, max-depth %d, "
          "comment-rate %.2f) ..."
          % (len(seeds), seeds[0], seeds[-1], args.jobs, args.max_depth, crate))

    run_dir = next_run_dir(args.out)
    buckets = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for seed, bucket, detail, src, minsrc in ex.map(process_seed, jobs):
            buckets.setdefault(bucket, []).append(seed)
            if bucket == "ok" and not args.keep_all:
                continue
            if bucket == "quarantine":
                qd = os.path.join(run_dir, "quarantine")
                os.makedirs(qd, exist_ok=True)
                with open(os.path.join(qd, "%d.gren" % seed), "w") as f:
                    f.write(src or "")
                with open(os.path.join(qd, "%d.stderr" % seed), "w") as f:
                    f.write(detail.get("msg", ""))
                continue
            if bucket in ("ok", "gen-error"):
                continue
            fdir = os.path.join(run_dir, "failures", str(seed))
            with tempfile.TemporaryDirectory() as tmp:
                write_report(fdir, seed, bucket, detail, src,
                             minsrc or src, tmp)

    # summary
    order = ["crash", "ast-mismatch", "non-idempotent", "comment-loss",
             "timeout", "gen-error", "quarantine"]
    counts = {b: len(v) for b, v in buckets.items()}
    ok = counts.get("ok", 0)
    finds = sum(counts.get(b, 0) for b in
                ("crash", "ast-mismatch", "non-idempotent", "comment-loss", "timeout"))
    summary_lines = ["%d/%d clean" % (ok, len(seeds)),
                     "app build: %s" % app_build_id(), ""]
    for b in order:
        if counts.get(b):
            summary_lines.append("%-15s %d   seeds: %s" %
                                 (b, counts[b],
                                  " ".join(map(str, sorted(buckets[b])[:40]))))
    summary = "\n".join(summary_lines) + "\n"
    with open(os.path.join(run_dir, "SUMMARY.txt"), "w") as f:
        f.write(summary)
    with open(os.path.join(run_dir, "run.json"), "w") as f:
        json.dump({"base_seed": args.base_seed, "count": args.count,
                   "max_depth": args.max_depth, "comment_rate": crate,
                   "app_build": app_build_id(), "counts": counts}, f, indent=2)
    latest = os.path.join(args.out, "latest")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(run_dir), latest)
    except OSError:
        pass

    print("\n" + summary)
    print("artifacts: %s" % run_dir)
    if counts.get("quarantine"):
        print("NOTE: %d quarantined (parse failures = generator bugs, not finds)"
              % counts["quarantine"])
    return 1 if finds else 0


if __name__ == "__main__":
    sys.exit(main())
