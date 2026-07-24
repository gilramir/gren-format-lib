#!/usr/bin/env python3
"""Property-based random Gren generator (qe.md avenue #2).

Builds random-but-legal Gren modules with bounded depth and checks the standing
invariants on each: parses, formats without crashing, AST-equivalent, idempotent,
comment-preserving, and independent of the order the author wrote sortable things
in. Targets the *feature co-occurrence* axis the 2026-07-18 scan proved
productive — the axis every single-axis synthetic gate misses.

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
import re
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
# Escape sequences spliced into Str/PStr/PChar content — verified directly
# against the app: all round-trip stably, and \u{...} is NORMALIZED on
# format (expands to its literal character in a string; survives with its
# hex lowercased in a char literal) rather than merely echoed back.
STR_ESCAPES = ["\\n", "\\t", "\\\\", "\\\"", "\\u{0041}", "\\u{00e9}", "\\u{1F600}"]
CHAR_ESCAPES = ["\\n", "\\t", "\\\\", "\\'", "\\u{0041}", "\\u{1F600}"]
# Concrete (arity-0) type-constructor names, used as leaf types and type-app args.
TYPE_CONS = ["Int", "Float", "String", "Bool", "Char"]
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
    def __init__(self, v, hex=False): self.v, self.hex = v, hex

class FloatLit(E):
    def __init__(self, v): self.v = v  # v: already-formatted source text, e.g. "3.14"

class Neg(E):
    """Unary minus (`-x`) — a distinct AST node from binary `-`, verified
    directly against the app: `foo -5` parses as a CALL whose argument is a
    negate node, not subtraction, and glues with no space before its operand
    in every position tried (bare, binop operand leading/trailing, call
    argument, when-scrutinee)."""
    def __init__(self, inner): self.inner = inner

class Str(E):
    def __init__(self, v): self.v = v

class MultilineStr(E):
    def __init__(self, lines, trailing=None):
        # lines: list of str (a content row's already-escaped source text) or
        # None (a wholly empty row — legal; a row with SOME but too-little
        # indentation is not, see emit_multiline_str).
        self.lines = lines
        # An optional line/block comment riding the closing `"""` — legal in
        # every surrounding-expression position this atom reaches (record
        # field value, array item, call/binop/pipeline operand, decl body),
        # not just at a declaration's own end (the shape v1.3 originally
        # fixed) — verified directly against the app for each position before
        # generating; see GENERATOR.md.
        self.trailing = trailing

class Var(E):
    def __init__(self, name): self.name = name

class Qual(E):
    def __init__(self, mod, name): self.mod, self.name = mod, name

class Ctor(E):
    def __init__(self, name): self.name = name

class Chr(E):
    def __init__(self, v): self.v = v  # v: already-escaped char content (see char_content)

class Field(E):
    def __init__(self, base, field): self.base, self.field = base, field

class Accessor(E):
    # Bare `.field` accessor FUNCTION (e.g. `Array.map .name xs`) — distinct
    # from `Field` (`x.name`), which has a base. No expr children.
    def __init__(self, field): self.field = field

class OpRef(E):
    # Operator reference `(+)` / `(|>)` — an operator used as a plain value /
    # function argument. Verified against the app: every operator in
    # BINOPS + PIPES parses as `(op)` in value position. No expr children.
    def __init__(self, op): self.op = op

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
    def __init__(self, lhs, val, params=None, sig=None, lead=None, trailing=None):
        # `params` (list of patterns) makes this a local FUNCTION binding
        # (`f a b = ...`); when non-empty, `lhs` is a PVar (the function name).
        # `sig`, if set, is a gen_type IR emitted single-line as `name : Type`
        # on the line directly above the binding — only used when `lhs` is a
        # PVar (a signature needs a name, not a destructure).
        self.lhs, self.val = lhs, val
        self.params = params or []
        self.sig = sig
        self.lead, self.trailing = lead, trailing

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
    def __init__(self, v, hex=False): self.v, self.hex = v, hex
class PStr:
    def __init__(self, v): self.v = v
class PChar:
    def __init__(self, v): self.v = v
class PCtor:
    # `mod`, if set, qualifies the constructor (`Mod.Ctor`) — verified
    # directly against the app in every position `pctor_ref`/`let_pattern`
    # reach (when-branch, lambda/decl param, array item, let LHS bare and
    # paren'd, `as`-aliased): a qualified 0-arg pattern parses bare same as
    # unqualified, and an applied one glues the same way (`Maybe.Just y`).
    def __init__(self, name, args, mod=None): self.name, self.args, self.mod = name, args, mod
class PRecord:
    def __init__(self, fields): self.fields = fields
class PArray:
    def __init__(self, items): self.items = items
class PAs:
    # `inner as name` — the parenthesization rule is verified empirically
    # (see Gen.pattern_base/pattern): bare `as` parses after PVar/PWild/PStr/
    # PChar/PArray/PRecord, but NEVER after PInt or PCtor (0-arg included) —
    # broader than the documented compiler-common#31 gap ("constructor
    # application"), which doesn't mention 0-arg constructors or int literals.
    def __init__(self, inner, name): self.inner, self.name = inner, name


class Decl:
    def __init__(self, name, params, body, sig=None, sig_broken=False,
                 doc=None, lead=None, trailing=None, arrow_comment=None):
        self.name, self.params, self.body = name, params, body
        self.sig, self.sig_broken = sig, sig_broken
        self.doc, self.lead, self.trailing = doc, lead, trailing
        self.arrow_comment = arrow_comment  # (seg_idx, (kind, text)) | None


# Type-alias / custom-type / port declarations. Unlike Decl (function), these
# carry no expression body, so the shrinker's expr/list machinery (which walks
# `.body`) must skip them — only the "drop this whole decl" step applies.

class TypeAliasDecl:
    def __init__(self, name, params, rhs, broken=False, doc=None, lead=None,
                 trailing=None, arrow_comment=None):
        self.name, self.params, self.rhs, self.broken = name, params, rhs, broken
        self.doc, self.lead, self.trailing = doc, lead, trailing
        self.arrow_comment = arrow_comment


class Variant:
    def __init__(self, name, payload=None, lead=None, trailing=None):
        # payload: None | ("record", [(field, type, None), ...]) | ("args", [type, ...])
        # (the payload record's fields are always comment-free — see
        # emit_variant_payload — the trailing None keeps the triple shape
        # gen_type's own record/exrecord fields use)
        self.name, self.payload = name, payload
        self.lead, self.trailing = lead, trailing


class UnionDecl:
    def __init__(self, name, params, variants, broken=False, doc=None, lead=None, trailing=None):
        self.name, self.params, self.variants, self.broken = name, params, variants, broken
        self.doc, self.lead, self.trailing = doc, lead, trailing


class PortDecl:
    def __init__(self, name, type_, broken=False, doc=None, lead=None,
                 trailing=None, arrow_comment=None):
        self.name, self.type_, self.broken = name, type_, broken
        self.doc, self.lead, self.trailing = doc, lead, trailing
        self.arrow_comment = arrow_comment


# `infix left 6 (+++) = add0` — a fixity declaration. Parsed by a dedicated
# loop that runs strictly after imports and before every other top-level
# declaration (`Compiler.Parse.Module.operatorLoopParser`), so these live on
# their own `Module.infixes` list, not mixed into `decls`. No `doc` field —
# `Compiler.Ast.Source.Infix` carries no doc-comment slot, unlike every other
# declaration kind — only a regular own-line `lead` / same-row `trailing`
# comment.

class InfixDecl:
    def __init__(self, assoc, prec, symbol, fn, lead=None, trailing=None):
        self.assoc, self.prec, self.symbol, self.fn = assoc, prec, symbol, fn
        self.lead, self.trailing = lead, trailing


# A single `import` statement. `SortSymbols` reorders a contiguous **run** of
# imports, and since 2026-07-23 a blank line is the ONLY thing that splits one
# (docs/sorting.md, "Runs and boundaries") — an own-line comment no longer
# breaks a run, it travels with the import below it. `blank` is therefore the
# run boundary, and every comment field is a placement case from that document:
#
#   anchor   own-line comment emitted BEFORE this import's blank line, so it has
#            a blank under it and leads nothing — the "section header keeps its
#            place" case. Only meaningful together with `blank`; pinned to the
#            position, it does NOT travel with the import when the run sorts.
#   lead     own-line comment directly above the import, no blank between —
#            belongs to it and MOVES with it. Legal on the first import of a run
#            too: cd1afeb made the head of a run uniform with the rest, so there
#            is deliberately no `i > 0` restriction here.
#   trailing rides the import's own last line (module name, `as` alias, `(..)`,
#            or the exposing list's close paren) and moves with it.
#
# `item_lead`/`item_trailing`, each `(index, (kind, text)) | None`, put an
# own-line-before / same-row-after comment on one item of a list-form
# `exposing` — verified directly against the app that this is legal and forces
# the list to break across lines; NEVER on a "(..)" or bare (no-exposing)
# import, which have no item to attach to.
class Import:
    def __init__(self, mod, as_name=None, exposing=None, lead=None, blank=False,
                 trailing=None, item_lead=None, item_trailing=None, anchor=None):
        self.mod, self.as_name, self.exposing = mod, as_name, exposing
        self.lead, self.blank, self.trailing = lead, blank, trailing
        self.item_lead, self.item_trailing = item_lead, item_trailing
        self.anchor = anchor


class Module:
    def __init__(self, name, imports, decls, infixes=None, doc=None, exposing="(..)",
                 effect=None, imports_tail=None):
        self.name, self.imports, self.decls, self.doc = name, imports, decls, doc
        self.infixes = infixes if infixes is not None else []
        # The header export list, already rendered: "(..)" or an explicit
        # "(foo, Bar(..))" built from the real declared names (see module_exposing).
        self.exposing = exposing
        # None, or an effect module's `where { ... }` clause: a list of
        # (field, name, comment|None) in emission order — see `Gen.effect_header`.
        self.effect = effect
        # Own-line comments emitted after the LAST import, before the blank
        # lines that separate the import block from the declarations. Such a
        # comment leads no import, so it stays at the end of the block while
        # the run above it sorts (docs/sorting.md, "Below the run's last
        # import"). Position-anchored: it never travels.
        self.imports_tail = imports_tail if imports_tail is not None else []


# ───────────────────────────── structural queries ─────────────────────────

BLOCK = (If, When, Let, Lambda)

def is_block(n):
    return isinstance(n, BLOCK)

def multiline(n):
    """Does this node render across >1 line? Structural (col-independent)."""
    if isinstance(n, (Int, Str, Var, Qual, Ctor, Chr, Accessor, OpRef)):
        return False
    if isinstance(n, MultilineStr):
        return True
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
    if isinstance(n, Int):   return [_inline(n, _int_text(n.v, n.hex))]
    if isinstance(n, FloatLit): return [_inline(n, n.v)]
    if isinstance(n, Neg):   return [_inline(n, "-" + one_line(n.inner))]
    if isinstance(n, Str):   return [_inline(n, '"' + n.v + '"')]
    if isinstance(n, MultilineStr): return emit_multiline_str(n, col)
    if isinstance(n, Var):   return [_inline(n, n.name)]
    if isinstance(n, Qual):  return [_inline(n, n.mod + "." + n.name)]
    if isinstance(n, Ctor):  return [_inline(n, n.name)]
    if isinstance(n, Chr):   return [_inline(n, "'" + n.v + "'")]
    if isinstance(n, Accessor): return [_inline(n, "." + n.field)]
    if isinstance(n, OpRef): return [_inline(n, "(" + n.op + ")")]
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


def _int_text(v, is_hex):
    """Source text for an int literal. Hex is emitted with LOWERCASE digits so
    the formatter's uppercasing (intToHex) is exercised; the value round-trips
    up to 2^53 - 1 (the exact-integer limit) since the intToHex 32-bit-`//` bug
    was fixed."""
    return ("0x" + format(v, "x")) if is_hex else str(v)


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
        if b.sig is not None:
            # `name : Type` on its own line directly above the binding (no
            # blank between), exactly like a top-level signature.
            out.append(pad(bcol) + emit_pat(b.lhs) + " : " + emit_type(b.sig))
        head = emit_binding(b, bcol)
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


def emit_binding(b, col):
    """`lhs <params> = val` at column `col` (headless line0). A local function
    binding (`b.params` non-empty) puts its parameters after the name, exactly
    like a top-level declaration. Block/multiline value drops to its own line
    indented +4; otherwise it hangs after `= `."""
    head = emit_pat(b.lhs) + "".join(" " + emit_param(p) for p in b.params)
    prefix = head + " = "
    if multiline(b.val):
        out = [head + " ="]
        out += own_line(b.val, col + INDENT)
        return out
    vl = emit(b.val, col + len(prefix))
    return [prefix + vl[0]] + vl[1:]


def emit_lambda(n, col):
    prefix = "\\" + " ".join(emit_param(p) for p in n.params) + " -> "
    if multiline(n.body):
        out = ["\\" + " ".join(emit_param(p) for p in n.params) + " ->"]
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


def emit_multiline_str(n, col):
    """`\"\"\"` at `col` (headless — the caller glues it after whatever
    precedes it, matching real Gren: the opener can follow other tokens on
    its row, e.g. `"prefix " ++ x ++ \"\"\"`). Content lines and the closing
    `\"\"\"` are absolute at `col` — every content row MUST be indented to AT
    LEAST that column (a real parser error otherwise: "Multi-line string
    lines are not indented equally"); a wholly empty row is the one exception
    and needs no padding at all. Rows may be indented DEEPER than `col`
    (`n.lines[i]` already includes any such extra indent as part of its own
    text) — only under-indenting is illegal."""
    out = ['"""']
    for line in n.lines:
        out.append("" if line is None else pad(col) + line)
    close = pad(col) + '"""'
    if n.trailing is not None:
        close += " " + comment_text(n.trailing)
    out.append(close)
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


def emit_doc_comment(doc):
    """`{-| ... -}` doc comment, always at column 0 (module-level or a
    top-level declaration's own doc — Gren has no nested/local doc comments).
    `doc` is either a str (one-line shorthand, `{-| text -}`) or a list of
    raw content lines (opener/closer alone on their own lines, content
    verbatim at column 0 — per README divergence #11, gren-format NEVER
    reindents or reflows a doc comment's body, unlike a plain `{- -}` block
    comment). A multi-line doc missing a blank line before `-}` gets one
    inserted by the formatter (a stable, idempotent one-time normalization,
    verified directly against the app) — not modeled here, since including
    or omitting a trailing blank in the input is equally safe either way."""
    if isinstance(doc, str):
        return ["{-| " + doc + " -}"]
    return ["{-|"] + list(doc) + ["-}"]


def emit_leading(d):
    """A declaration's leading lines: its doc comment if it has one,
    otherwise its regular own-line comments (mutually exclusive — a decl
    never gets both, to avoid the untested "comment stacked above a doc
    comment" combination)."""
    if getattr(d, "doc", None) is not None:
        return emit_doc_comment(d.doc)
    if d.lead:
        return [comment_text(c) for c in d.lead]
    return []


def emit_pat(p):
    if isinstance(p, PVar):  return p.name
    if isinstance(p, PWild): return "_"
    if isinstance(p, PInt):  return _int_text(p.v, p.hex)
    if isinstance(p, PStr):  return '"' + p.v + '"'
    if isinstance(p, PChar): return "'" + p.v + "'"
    if isinstance(p, PRecord): return "{ " + ", ".join(p.fields) + " }"
    if isinstance(p, PArray):
        if not p.items:
            return "[]"
        return "[ " + ", ".join(emit_pat(i) for i in p.items) + " ]"
    if isinstance(p, PCtor):
        qname = (p.mod + "." + p.name) if p.mod else p.name
        if not p.args:
            return qname
        parts = []
        for a in p.args:
            s = emit_pat(a)
            if isinstance(a, PCtor) and a.args:
                s = "(" + s + ")"
            # A ctor's own argument slot is parsed without alias-awareness
            # (`Compiler.Parse.Pattern`'s `parserNoAlias`), so a bare `n as
            # whole` there doesn't parse at all — verified directly against
            # the app (`Just (n as whole) -> ...` needs exactly this wrap;
            # `Just (Nothing as whole)` still fails even wrapped once, since
            # the alias base itself ALSO needs its own inner paren, already
            # supplied by the `PAs` case below).
            elif isinstance(a, PAs):
                s = "(" + s + ")"
            parts.append(s)
        return qname + " " + " ".join(parts)
    if isinstance(p, PAs):
        inner = emit_pat(p.inner)
        if isinstance(p.inner, (PCtor, PInt)):
            inner = "(" + inner + ")"
        return inner + " as " + p.name
    raise ValueError("emit_pat: " + type(p).__name__)


def emit_param(p):
    """A pattern in a function/lambda/let-binding PARAMETER slot. A bare
    `PAs` there DOES parse on its own (`f n as whole = whole` is legal,
    confirmed directly against the app) — but if the alias base is itself a
    multi-token constructor-with-argument, the ctor's own argument slot
    doesn't reach across the `as` at all: `f Just n as whole = whole`
    silently reparses as TWO separate params (`Just` a bare 0-arg pattern,
    `n as whole` the next param) rather than failing — a different AST, not
    a parse error, so no oracle would catch the drift. One extra pair of
    parens around the WHOLE alias sidesteps this uniformly for every inner
    kind (var/0-arg-ctor/ctor-with-arg/array/record), matching the exact
    canonical form gren-format itself always normalizes a param-position
    alias to — verified directly against the app for each kind before
    wiring in."""
    s = emit_pat(p)
    if isinstance(p, PAs):
        s = "(" + s + ")"
    return s


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
        # Fields are (name, type, lead) triples; `lead` is only ever non-None
        # on a `top` record generated `broken` (see Gen.gen_type), which
        # always renders through emit_type_multiline's own-line branch
        # instead of reaching here — so it's structurally guaranteed None
        # whenever this flat, single-line join runs.
        return "{ " + ", ".join(f + " : " + emit_type(ft) for f, ft, _ in t[1]) + " }"
    if kind == "exrecord":
        # Extensible record `{ base | field : T, … }`. Emitted inline like any
        # record type; the formatter breaks it (base on the `{` line, `|`/`,`
        # fields +4 beneath) only when the author wrote it broken — handled
        # by emit_type_multiline for a `top`, `broken` record (see there);
        # this flat join is for every other (always comment-free) occurrence.
        base, fields = t[1], t[2]
        return "{ " + base + " | " + ", ".join(f + " : " + emit_type(ft) for f, ft, _ in fields) + " }"
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


def emit_record_type(kind, base, fields):
    """Multi-line record TYPE — a `top`, `broken` record/exrecord from
    `Gen.gen_type` (fields are (name, type, lead) triples). Returned lines are
    in LOCAL coordinates (0 = the record's own `{` column); the caller pads
    every line by the enclosing INDENT, same as emit_type_multiline's arrow
    branch. Two shapes, both verified directly against the app:

    plain, field 0 glues onto the `{` line, every other field gets its own
    `, f : T` line at column 0 (RecordTypeLayoutByAuthor/Vertical,
    SignatureRecordTypeComment):

        { f0 : T0
        , f1 : T1
        }

    extensible, `base` is alone on the `{` line, and EVERY field (including
    0) gets its own `| `/`, ` line at column INDENT — so field 0 can carry a
    lead comment too, unlike the plain shape (RecordTypeLayoutByAuthor/
    ExtVertical, ExtensibleRecordTypeTrailingComment):

        { base
            | f0 : T0
            , f1 : T1
            }

    A field's `lead` (own-line comment right before its own line) rides at
    the SAME column as the field lines — confirmed against the app: it is
    NOT glued to the record's own `{`/base line."""
    if kind == "exrecord":
        lines = ["{ " + base]
        fcol = INDENT
    else:
        lines = []
        fcol = 0
    for i, (f, ft, lead) in enumerate(fields):
        if kind == "record" and i == 0:
            lines.append("{ " + f + " : " + emit_type(ft))
            continue
        if lead is not None:
            lines.append(pad(fcol) + comment_text(lead))
        prefix = "| " if (kind == "exrecord" and i == 0) else ", "
        lines.append(pad(fcol) + prefix + f + " : " + emit_type(ft))
    lines.append("}")
    return lines


def emit_type_multiline(t, broken, arrow_comment=None):
    """Emit a top-level signature/alias/port type. Per README "Type
    signatures": written across rows, the canonical shape puts each `->`
    segment on its own line, `->` leading each continuation — so this only
    ever applies when `t` is an arrow chain; a non-arrow RHS (record, con,
    var, app) has no `->` boundary to break at and always stays inline.

    A `top`, `broken` record/exrecord (see `Gen.gen_type`) is the other
    multi-line shape a type can take, independent of the arrow-breaking
    `broken` parameter below — its OWN broken flag lives in the tuple
    (`t[-1]`), checked first since a record/exrecord is never itself an arrow.

    `arrow_comment`, if given, is `(seg_idx, (kind, text))` — a comment riding
    the `->` that leads `segs[seg_idx]` (README divergence #5). A single-line
    block comment glues onto the SAME line as its segment (`-> {- k -} Type`);
    a line comment can't share a line with anything after it, so the segment
    drops to its own next line with no `->` prefix (it's a continuation of
    the same arrow step, not a new one)."""
    if t[0] in ("record", "exrecord") and t[-1]:
        if t[0] == "record":
            fields = t[1]
            return emit_record_type("record", None, fields)
        base, fields = t[1], t[2]
        return emit_record_type("exrecord", base, fields)
    if not broken or t[0] != "arrow":
        return [emit_type(t)]
    segs = _flatten_arrow(t)
    lines = [emit_type(segs[0])] + ["-> " + emit_type(s) for s in segs[1:]]
    if arrow_comment is not None:
        idx, c = arrow_comment
        kind, _ = c
        if 0 < idx < len(lines):
            if kind == "line":
                lines[idx:idx + 1] = ["-> " + comment_text(c), emit_type(segs[idx])]
            else:
                lines[idx] = "-> " + comment_text(c) + " " + emit_type(segs[idx])
    return lines


# ───────────────────────── type alias / union / port emission ─────────────
# Per README: a `type alias` RHS and a custom type's variant list ALWAYS drop
# to their own line(s) below the header, indented 4 — never glued to `=`, even
# when they'd fit on one line.

def emit_type_alias(d):
    header = "type alias " + d.name + "".join(" " + p for p in d.params) + " ="
    return [header] + [pad(INDENT) + l
                        for l in emit_type_multiline(d.rhs, d.broken, d.arrow_comment)]


def emit_variant_payload(payload):
    kind, val = payload
    if kind == "record":
        # A variant payload record is always flat/comment-free (never `top`,
        # so `gen_type` never applies to it — it's built by hand in
        # Gen.variant_payload) — pass broken=False to match emit_type's
        # 3-element "record" tuple shape.
        return emit_type(("record", val, False))
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
               [pad(INDENT) + l
                for l in emit_type_multiline(d.type_, True, d.arrow_comment)]
    return ["port " + d.name + " : " + emit_type(d.type_)]


def emit_where(effect):
    """The `where { command = MyCmd, subscription = MySub }` clause. Always
    inline (like `emit_infix`, it collapses to one line regardless of input
    layout, so there's no broken variant to model — see `effect_header`)."""
    parts = []
    for field, name, cmt in effect:
        s = field + " = " + name
        if cmt is not None:
            s += " " + comment_text(cmt)
        parts.append(s)
    return "where { " + ", ".join(parts) + " }"


def emit_infix(d):
    """Always single-line — README: 'An infix declaration is always written
    on one line' — the generator never author-breaks it (the formatter
    collapses a broken one anyway; see the checked-in InfixWrapped fixture,
    which covers that collapse directly)."""
    out = emit_leading(d)
    line = "infix %s %d (%s) = %s" % (d.assoc, d.prec, d.symbol, d.fn)
    if d.trailing is not None:
        line += " " + comment_text(d.trailing)
    out.append(line)
    return out


def emit_import(imp):
    """Emit one `import` statement, preceded by its own boundary lines and
    possibly broken across lines to carry a comment on one `exposing` item.

    Emission order is `anchor`, blank, `lead`, import — and the order is the
    whole point: an own-line comment ABOVE the blank line leads nothing and
    stays put, while one BELOW it (directly above the import) belongs to that
    import and travels with it. The two shapes are a comment on either side of
    the same blank line, so nothing but this ordering distinguishes them."""
    out = []
    if imp.anchor is not None:
        out.append(comment_text(imp.anchor))
    if imp.blank:
        out.append("")
    if imp.lead is not None:
        out.append(comment_text(imp.lead))
    head = "import " + imp.mod
    if imp.as_name is not None:
        head += " as " + imp.as_name
    if imp.exposing is None or imp.exposing == "(..)":
        line = head if imp.exposing is None else head + " exposing (..)"
        if imp.trailing is not None:
            line += " " + comment_text(imp.trailing)
        out.append(line)
        return out
    items = imp.exposing
    if imp.item_lead is None and imp.item_trailing is None:
        line = head + " exposing (" + ", ".join(items) + ")"
        if imp.trailing is not None:
            line += " " + comment_text(imp.trailing)
        out.append(line)
        return out
    lead_idx, lead_c = imp.item_lead if imp.item_lead is not None else (None, None)
    trail_idx, trail_c = imp.item_trailing if imp.item_trailing is not None else (None, None)
    out.append(head + " exposing")
    for i, it in enumerate(items):
        if i == lead_idx:
            out.append(pad(INDENT) + comment_text(lead_c))
        prefix = "( " if i == 0 else ", "
        line = pad(INDENT) + prefix + it
        if i == trail_idx:
            line += " " + comment_text(trail_c)
        out.append(line)
    close = pad(INDENT) + ")"
    if imp.trailing is not None:
        close += " " + comment_text(imp.trailing)
    out.append(close)
    return out


# ───────────────────────────── module emission ────────────────────────────

def emit_module(m):
    if m.effect is not None:
        kw = "effect module "
    elif any(isinstance(d, PortDecl) for d in m.decls):
        kw = "port module "
    else:
        kw = "module "
    head = kw + m.name
    if m.effect is not None:
        head += " " + emit_where(m.effect)
    head += " exposing " + m.exposing
    header = [head]
    if m.doc is not None:
        # Module doc: exactly one blank line after the header, verified
        # directly against the app — then the SAME import/decl spacing logic
        # applies as if the doc were part of the header block (one blank
        # before imports if any follow, else the standard two blanks before
        # the first top-level declaration).
        header.append("")
        header += emit_doc_comment(m.doc)
    lines = header + [""]
    for imp in m.imports:
        lines += emit_import(imp)
    if m.imports:
        # Only meaningful below an actual import — with no imports at all this
        # would just be a leading comment on the first declaration, a shape
        # emit_decl already covers. The shrinker can delete every import, so
        # this guard is load-bearing, not just a generation-time nicety.
        for c in m.imports_tail:
            lines.append(comment_text(c))
        lines.append("")
    lines.append("")
    for d in m.infixes:
        lines += emit_infix(d)
    if m.infixes:
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
    out = emit_leading(d)
    out += core
    if d.trailing is not None:
        out[-1] = out[-1] + " " + comment_text(d.trailing)
    return out


def emit_function_decl(d):
    out = emit_leading(d)
    if d.sig is not None:
        # A `top`, `broken` record/exrecord sig (see Gen.gen_type) also needs
        # the multiline path, same as a broken arrow — checked via the type's
        # own embedded broken flag (t[-1]) since d.sig_broken only ever tracks
        # arrow-breaking (it's False for a record sig by construction).
        sig_is_broken_record = d.sig[0] in ("record", "exrecord") and d.sig[-1]
        if (d.sig_broken and d.sig[0] == "arrow") or sig_is_broken_record:
            out.append(d.name + " :")
            out += [pad(INDENT) + l
                    for l in emit_type_multiline(d.sig, d.sig_broken, d.arrow_comment)]
        else:
            out.append(d.name + " : " + emit_type(d.sig))
    prefix = d.name + "".join(" " + emit_param(p) for p in d.params) + " = "
    if multiline(d.body):
        out.append(d.name + "".join(" " + emit_param(p) for p in d.params) + " =")
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
        # Dynamically-scoped claim flag: set by `mk_multiline_str` when a
        # generated `MultilineStr` grabs its own trailing comment, read (and
        # save/restored) by `gen_body_with_trailing` — see that method.
        self._multiline_trailing_claimed = False
        self.vars = ["x", "y", "z", "acc", "item", "node"]
        self.fields = ["name", "count", "value", "next", "kind"]
        self.ctors = ["Just", "Nothing", "Ok", "Err", "Leaf", "Node"]
        self.mods = ["String", "Array", "Dict", "Maybe"]
        # Type-application heads paired with a realistic arg count, for richer
        # type application (`Dict String Int`, `Array (Array a)`) — see
        # `gen_type_app`. The parser this targets does not enforce arity, so the
        # count is cosmetic; these mirror Gren core types (`Array a`, `Maybe a`,
        # `Result e a`, `Dict k v`) so the generated types read as real code.
        self.type_apps = [("Array", 1), ("Maybe", 1), ("Result", 2), ("Dict", 2)]
        # Fake imported type names and operator symbols for import `exposing`
        # lists (`import Foo exposing (Alpha(..), (|=), value)`) — these name
        # things in OTHER modules, which a generated single module never
        # defines, so any parseable spelling is fine (verified they sort as
        # operators → types → values). Not reused for the module-header export
        # list, which draws only real declared names.
        self.exp_types = ["Alpha", "Bravo", "Gamma", "Delta"]
        self.exp_ops = ["(|=)", "(|.)", "(</>)", "(>>>)", "(<?>)"]
        # Bare (unparenthesized — `emit_infix` adds the parens) operator
        # symbols for `infix` fixity declarations. Each is built only from
        # `Compiler.Parse.Operator`'s accepted charset (+-/*=.<>:&|^?%!) and
        # confirmed distinct from its five reserved exact-match tokens
        # (".", "|", "->", "=", ":") — verified directly against the app
        # before use, alongside every other shape below.
        self.custom_ops = ["+++", "<+>", "^^", "%%", "&&&", "???", "<->", "==>", "**", "!!"]
        # Constructors declared by this module's own `union()` calls so far
        # (populated as decls are generated, in source order — see `union`).
        # [(name, kind)], kind in "none" / "record" / "value" (mirrors
        # `variant_payload`'s None / ("record", ...) / ("args", ...) shapes).
        # Referenced back from `leaf`/`pattern_base` (via `ctor_ref`/
        # `pctor_ref`) so a generated module actually constructs and matches
        # the unions it declares, instead of only ever declaring them — see
        # GENERATOR.md's "Remaining expansion targets".
        self.declared_ctors = []

    def chance(self, p): return self.rng.random() < p
    def pick(self, xs):  return self.rng.choice(xs)

    def gen_body_with_trailing(self, gen_fn):
        """Run `gen_fn()` (a thunk building a decl/let-binding's value) and
        return `(value, trailing_comment_for_the_container)`. A `MultilineStr`
        ANYWHERE in that value — not just at the top: nested as a call's last
        argument, a binop's last operand, a record's last field, … — may end
        up rendering as the value's own last line, and `mk_multiline_str` may
        independently give IT a trailing comment (see GENERATOR.md's
        surrounding-expression addition). Two trailing comments would then
        collide on that one rendered line, merging into a single comment's
        text instead of staying distinct tokens (breaking the
        comment-preservation oracle's multiset check) — regardless of how
        deeply nested the multiline string is, or through how many
        containers, since none of them (`Call`/`Binop`/`Record`/`Array`/…)
        have a competing per-item comment mechanism of their own to
        disambiguate against.

        Rather than statically re-deriving "is this multiline string the
        rightmost rendered token" (a property of the renderer, not the tree —
        parens, container brackets, and layout choices all affect it), this
        tracks the claim DYNAMICALLY via `self._multiline_trailing_claimed`,
        save/restored around `gen_fn()` so a NESTED trailing-owning construct
        (a `let`'s own bindings, each independently calling this same method)
        doesn't leak its own claim into an outer caller's decision — only a
        claim that survives all the way to the top of `gen_fn()`, unconsumed
        by any inner scope, reaches this method's own read-back."""
        saved = self._multiline_trailing_claimed
        self._multiline_trailing_claimed = False
        val = gen_fn()
        claimed = self._multiline_trailing_claimed
        self._multiline_trailing_claimed = saved
        return val, (None if claimed else self.comment())

    def comment(self, kinds=("line", "block")):
        """Maybe return a fresh unique comment (kind, text), else None."""
        if not self.chance(self.crate):
            return None
        kind = self.pick(list(kinds))
        text = "k%d" % self.cid
        self.cid += 1
        return (kind, text)

    def forced_comment(self, kinds=("line", "block")):
        """A comment for a site that has ALREADY decided it wants one.

        `comment()` rolls the comment-rate dice itself, so a caller whose whole
        purpose is to place a comment (a run boundary, a section header) has to
        override that roll or it mostly gets None. The override stops at
        `--no-comments`, which promises structure only: rate 0 means no
        comments anywhere, not "no comments except the insistent ones"."""
        if self.crate <= 0:
            return None
        return self.comment(kinds) or (self.pick(list(kinds)),
                                       "k%d" % self.next_cid())

    def forced_comments(self, n=1, kinds=("line", "block")):
        """`forced_comment` as a list — empty under `--no-comments`."""
        out = []
        for _ in range(n):
            c = self.forced_comment(kinds)
            if c is not None:
                out.append(c)
        return out

    # -- expressions -------------------------------------------------------

    def atom(self, depth):
        """Single-line, argument-safe: leaf / field / qualified / parenthesized.
        MultilineStr is included bare (no parens) — like any string, it never
        needs them; this is also how it lands as a binop operand / call
        argument, matching how a real triple-quoted string glues onto a
        preceding binop chain in Gren source. `Neg` (unary minus) is included
        bare too, for the same reason — verified directly against the app
        that `foo -5` parses as a call whose argument is a distinct negate
        node (not subtraction), gluing with no space in every position
        tried (bare, binop operand, call argument, when-scrutinee)."""
        r = self.rng.random()
        if depth <= 0 or r < 0.42:
            n = self.leaf()
        elif r < 0.5:
            n = Neg(self._flat_leaf())
        elif r < 0.62:
            n = Field(self.field_base(depth), self.pick(self.fields))
        elif r < 0.72:
            n = self.mk_multiline_str()
        elif r < 0.85:
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

    def gen_int(self):
        """An int literal — usually a small decimal, ~25% a hex literal. Hex
        magnitude is log-uniform up to 2^44: this spans the everyday small
        values AND the >= 2^35 range that used to corrupt intToHex, while
        staying well under the 2^53 exact-integer limit. Emitted lowercase so
        the formatter's uppercasing is exercised."""
        if self.chance(0.25):
            return Int(self.rng.randint(0, (1 << self.rng.randint(1, 44)) - 1), hex=True)
        return Int(self.rng.randint(0, 99))

    def gen_pint(self):
        """A pattern int literal — same hex spread as `gen_int`."""
        if self.chance(0.25):
            return PInt(self.rng.randint(0, (1 << self.rng.randint(1, 44)) - 1), hex=True)
        return PInt(self.rng.randint(0, 9))

    def leaf(self):
        r = self.rng.random()
        if r < 0.30:  return Var(self.pick(self.vars))
        if r < 0.44:  return self.gen_int()
        if r < 0.52:  return self.float_lit()
        if r < 0.64:  return Str(self.str_word())
        if r < 0.70:  return Chr(self.char_content())
        if r < 0.82:  return self.ctor_ref()
        if r < 0.88:  return Accessor(self.pick(self.fields))
        if r < 0.93:  return OpRef(self.pick(BINOPS + PIPES))
        return Qual(self.pick(self.mods), self.pick(self.vars))

    def float_lit(self):
        """A Float literal's already-formatted source text. No PFloat pattern
        exists — verified directly against the app that the parser rejects a
        Float literal pattern outright ("Float patterns are not supported"),
        unlike Int, so this is expression-position only. Each form here was
        verified to parse/format/round-trip stably (bare, as a binop operand,
        negated, as a call argument, with a leading inline comment).

        ~30% of the time this is scientific notation instead — confirmed
        valid Gren via `Compiler/Parse/Number.gren`'s `exponentParser` (an
        exponent may follow either an integer or a fractional literal, with an
        optional `+`/`-` sign, `e` or `E`) and confirmed the formatter does
        NOT normalize it: case and sign are echoed verbatim (unlike a hex
        literal's forced-lowercase digits), since `FloatingPoint.text` is
        emitted as-is with no recomputation."""
        if self.chance(0.3):
            mantissa = self.pick(["1", "2.5", "0.5", "12", "3", "9.99"])
            e = self.pick(["e", "E"])
            sign = self.pick(["", "+", "-"])
            exp = str(self.rng.randint(0, 20))
            return FloatLit(mantissa + e + sign + exp)
        return FloatLit(self.pick(["0.0", "0.5", "1.0", "2.5", "3.14",
                                   "12.5", "100.0", "0.25"]))

    def str_word(self):
        """A Str/PStr content chunk: plain words normally, occasionally with
        an embedded escape sequence (`\\n`/`\\t`/`\\\\`/`\\"`/`\\u{...}`)
        spliced between them — verified directly against the app that each
        round-trips stably even though the formatter NORMALIZES some of them
        on format (a `\\u{...}` string escape expands to its literal
        character; the same escape in a char literal instead survives but
        with its hex lowercased) — exercising that normalization is the
        point."""
        words = [self.pick(WORDS) for _ in range(self.rng.randint(1, 2))]
        if self.chance(0.25):
            words.insert(self.rng.randint(0, len(words)), self.pick(STR_ESCAPES))
        return " ".join(words)

    def char_content(self):
        """An already-escaped char content for a `PChar` pattern or a `Chr`
        expression: a plain letter normally, else one of `CHAR_ESCAPES` (a
        `\\u{...}` escape survives with its hex lowercased on format — the same
        normalization the str-escape path exercises, now reached in char
        position too)."""
        if self.chance(0.3):
            return self.pick(CHAR_ESCAPES)
        return self.pick(["a", "b", "x", "y", "z"])

    def ctor_ref(self):
        """A constructor reference: bare, or (for a declared 1-arg variant)
        applied to a matching-shape argument — half the time drawn from this
        module's own `declared_ctors` (see `union`), else the generic
        built-in-style pool, same as before this method existed.

        `leaf()` calls this, and `leaf()` is in turn called directly by
        `inline()`, whose contract is a GUARANTEED single-line result (it
        feeds `if`/`when` conditions and scrutinees, rendered via `one_line`,
        which asserts). So this — like every other `leaf()` branch — must
        always be single-line: `broken=False`, and the argument is a plain
        `_flat_leaf()` rather than `atom()`/`mk_record()`, neither of which
        that guarantee (an `atom()` can recurse into a multi-line `Paren`, and
        a record's OWN field values recurse through `value()`, which can nest
        an `if`/`when`/`let`/lambda). The richer, possibly-multi-line
        applied-constructor shape — including a bare record-literal argument,
        `Ctor { a = 1 }`, legal Gren and previously never generated at all —
        lives in `mk_call` instead, which is only ever reached from `value()`
        (no single-line contract)."""
        if self.declared_ctors and self.chance(0.5):
            name, kind = self.pick(self.declared_ctors)
            if kind == "none" or self.chance(0.25):
                return Ctor(name)
            if kind == "record":
                fields = [(self.pick(self.fields) + str(j), self._flat_leaf())
                          for j in range(self.rng.randint(1, 2))]
                return Call(Ctor(name), [Record(fields, broken=False)])
            return Call(Ctor(name), [self._flat_leaf()])
        return Ctor(self.pick(self.ctors))

    def _flat_leaf(self):
        """Like `leaf()`, but never `ctor_ref` — Var/Int/Str/Qual only.
        Guaranteed single-line AND guaranteed not to recurse back into
        `ctor_ref` (which `leaf()` otherwise would, geometrically-decaying
        but uncapped). Used as a constructor-call argument in the
        single-line-guaranteed path."""
        r = self.rng.random()
        if r < 0.38:  return Var(self.pick(self.vars))
        if r < 0.56:  return self.gen_int()
        if r < 0.64:  return self.float_lit()
        if r < 0.78:  return Str(self.str_word())
        if r < 0.85:  return Chr(self.char_content())
        return Qual(self.pick(self.mods), self.pick(self.vars))

    def maybe_inline_comment(self, n):
        # inline comments ride single-line atoms only
        if isinstance(n, (Int, FloatLit, Str, Var, Qual, Ctor, Chr)) and n.pre is None:
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
        if r < 0.24:  return self.mk_binop(d)
        if r < 0.30:  return self.mk_pipeline(d)
        if r < 0.42:  return self.mk_record(d)
        if r < 0.50:  return self.mk_update(d)
        if r < 0.60:  return self.mk_array(d)
        if r < 0.63:  return self.mk_if(d)
        if r < 0.71:  return self.mk_when(d)
        if r < 0.79:  return self.mk_let(d)
        if r < 0.85:  return self.mk_lambda(d)
        if r < 0.91:  return self.mk_multiline_str()
        return self.atom(d)

    def mk_call(self, d):
        # A declared 1-arg constructor applied here (unlike `ctor_ref`'s
        # single-line-guaranteed version) may go multi-line/broken — this is
        # only ever reached from `value()`, which carries no single-line
        # contract. A "record" payload gets a BARE record-literal argument
        # (`Ctor { a = 1, b = 2 }`, no parens) — legal Gren, and previously
        # never generated at all: the existing `arg()` call-argument
        # machinery only ever offers `atom()` or `Paren(value)`, never an
        # unparenthesized record literal (verified directly against the app
        # before wiring this in).
        applyable = [c for c in self.declared_ctors if c[1] != "none"]
        if applyable and self.chance(0.3):
            name, kind = self.pick(applyable)
            if kind == "record":
                return Call(Ctor(name), [self.mk_record(d)], broken=self.chance(0.5))
            return Call(Ctor(name), [self.arg(d)], broken=self.chance(0.5))
        k = self.rng.randint(1, 3)
        return Call(Var(self.pick(self.vars)),
                    [self.arg(d) for _ in range(k)],
                    broken=self.chance(0.5))

    def mk_binop(self, d):
        # Chain length is usually short (2-4 operands) but a meaningful
        # fraction stretch long (5-8 operands / 4-7 operators) to exercise
        # precedence-split layout and long-chain wrapping. Ops are drawn from
        # the full BINOPS+PIPES pool, so a long chain mixes precedence levels
        # and pipe directions — the shape that surfaced seed 608's buried
        # mixed pipeline.
        if self.chance(0.30):
            k = self.rng.randint(5, 8)
        else:
            k = self.rng.randint(2, 4)
        operands = [self.arg(d) for _ in range(k)]
        pool = BINOPS + PIPES
        ops = [self.pick(pool) for _ in range(k - 1)]
        return Binop(operands, ops, broken=self.chance(0.5))

    def mk_pipeline(self, d):
        # A dedicated, longer PIPELINE chain: 4-9 operands (3-8 steps),
        # predominantly forward `|>` with occasional backward `<|` steps — the
        # mixed shape `makeBackwardPipelineBox` always breaks one-per-line.
        # Operands are `arg(d)` (atom or parenthesized), so each step body
        # stays a self-contained unit and the chain is a genuine flat pipeline
        # rather than nested sub-pipelines. Distinct from `mk_binop` (which
        # mixes in arithmetic/comparison ops): this stresses pure and
        # mixed-direction pipeline wrapping at length.
        k = self.rng.randint(4, 9)
        operands = [self.arg(d) for _ in range(k)]
        ops = ["<|" if self.chance(0.25) else "|>" for _ in range(k - 1)]
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
        binds = [self.let_bind(d) for _ in range(k)]
        return Let(binds, self.value(d))

    def let_bind(self, d):
        """One `let` binding: usually a plain value/destructure binding, but
        ~30% of the time a local FUNCTION binding (`f a b = ...`) — a distinct
        formatter path (the let-flow blank-line/signature machinery) that only
        fixtures reached before. A function binding, and a value binding whose
        LHS is a plain name, may carry a single-line type signature on the line
        above (`f : Int -> Int`), verified directly against the app. Params use
        `pattern_base` like lambda/decl params (bare ctor params parse as
        separate params — harmless: the tree is only an emission recipe, and
        the oracles compare format-vs-reformat, not tree-vs-parse)."""
        if self.chance(0.30):
            name = PVar(self.pick(self.vars))
            nparams = self.rng.randint(1, 2)
            params = [self.pattern_base(d) for _ in range(nparams)]
            sig = ("arrow", [self.gen_type(2) for _ in range(nparams + 1)]) \
                if self.chance(0.5) else None
            val, trailing = self.gen_body_with_trailing(lambda: self.value(d))
            return LetBind(name, val, params=params, sig=sig,
                           lead=self.comment(), trailing=trailing)
        lhs = self.let_pattern(d)
        sig = self.gen_type(2) if isinstance(lhs, PVar) and self.chance(0.3) else None
        val, trailing = self.gen_body_with_trailing(lambda: self.value(d))
        return LetBind(lhs, val, sig=sig,
                       lead=self.comment(), trailing=trailing)

    def let_pattern(self, depth):
        """A let-binding LHS: `PVar` most of the time, else a destructuring
        pattern legal there (verified directly against the app: `[ a, b ] =
        pair`, `(Just c) = maybeVal`, `_ = ignored`, `{ x, y } = point`,
        `Maybe.Nothing = pair` all parse — `PRecord`/`PArray`/0-arg-`PCtor`
        (qualified or not)/`PWild` are all fine bare; a `PVar as name` alias
        LHS does NOT parse there, so `PAs` is never used here). A 1-arg
        constructor pattern needs parens as a let-binding LHS (`(Just c) =
        ...`; bare `Just c = ...` fails to parse) — sidestepped by only ever
        using an ARITY-0 constructor here, rather than adding a
        pattern-level paren wrapper for this one caller."""
        r = self.rng.random()
        if r < 0.55:
            return PVar(self.pick(self.vars))
        if r < 0.65:
            return PWild()
        if r < 0.8:
            return PRecord([self.pick(self.fields) for _ in range(self.rng.randint(1, 2))])
        if r < 0.9 and depth > 0:
            k = self.rng.randint(0, 2)
            return PArray([self.pattern_base(depth - 1) for _ in range(k)])
        none_ctors = [c for c in self.declared_ctors if c[1] == "none"]
        if none_ctors and self.chance(0.5):
            return PCtor(self.pick(none_ctors)[0], [])
        mod = self.pick(self.mods) if self.chance(0.35) else None
        return PCtor(self.pick(self.ctors), [], mod=mod)

    def mk_lambda(self, d):
        k = self.rng.randint(1, 2)
        return Lambda([self.pattern_base(d) for _ in range(k)], self.value(d))

    # -- multi-line (triple-quoted) strings ---------------------------------
    # Content-line legality, verified directly against the built app: a row
    # may be indented DEEPER than the block's base column (freely — that
    # extra indent is just part of the row's own text), but never LESS —
    # under-indenting is a real parse error ("Multi-line string lines are not
    # indented equally"). A row may also be wholly empty (zero characters,
    # no padding at all) — always legal, that's the one exception.

    def multiline_string_line(self):
        r = self.rng.random()
        words = " ".join(self.pick(WORDS) for _ in range(self.rng.randint(1, 3)))
        if r < 0.5:
            text = words
        elif r < 0.65:
            text = "\\\"" + words + "\\\""                  # \"word\"
        elif r < 0.75:
            text = "\\\"\\\"\\\"" + words + "\\\"\\\"\\\""   # \"\"\"word\"\"\"
        elif r < 0.85:
            text = words + "\\\\"                            # trailing \\
        elif r < 0.93:
            text = words + "\t" + words                       # literal tab
        else:
            return None                                       # wholly empty row
        extra_indent = " " * (4 * self.rng.randint(0, 2))
        trailing_ws = "  " if self.chance(0.15) else ""
        return extra_indent + text + trailing_ws

    def mk_multiline_str(self):
        k = self.rng.randint(1, 3)
        trailing = None
        if not self._multiline_trailing_claimed:
            # A trailing `--` comment on a multiline string used as a
            # mid-chain binop/pipeline operand currently hits a real, OPEN
            # formatter bug (a backward-pipe step's operator glues onto the
            # same line as the `--`, silently swallowing the operator into
            # the comment text — an ast-mismatch). Left in place
            # DELIBERATELY, not restricted to block-only, per the user's
            # call 2026-07-22: the bug and its repro seeds are written up in
            # `gren-format-lib/tbd.md` (seeds 1480/2303/2767) for review
            # before deciding the fix; the generator should keep finding it
            # rather than being narrowed to hide it.
            trailing = self.comment()
            if trailing is not None:
                self._multiline_trailing_claimed = True
        return MultilineStr([self.multiline_string_line() for _ in range(k)],
                            trailing=trailing)

    # -- patterns ----------------------------------------------------------

    def pattern(self, depth):
        """Top-level pattern position (currently: a `when`-branch pattern
        only — see call sites). May wrap the base pattern in an `as` alias;
        guards against double-wrapping when `pattern_base` already produced
        one itself (an alias-of-an-alias, `(x as a) as b`, is untested and
        not generated).

        Deliberately NOT generated: a negative int literal pattern (`-3 ->`).
        Verified directly against the app that this parses ONLY as a `when`
        expression's very FIRST branch — `_ -> 1` / `-8 -> 0` / `_ -> 2`
        (negative pattern second) fails to parse with "Not a valid number",
        and even `-8 -> 0` / `-3 -> 1` (negative pattern second, first ALSO
        negative) fails the same way; only `-8 -> 0` as branch #1 works. A
        real (narrow) parser gap in the same family as compiler-common#31/#32
        — not modeled here since `pattern()` has no notion of "am I branch
        #1", and getting it wrong would put a quarantine-triggering shape
        back in the generator's own output."""
        base = self.pattern_base(depth)
        if not isinstance(base, PAs) and self.chance(0.08):
            return PAs(base, self.pick(self.vars))
        return base

    def pattern_base(self, depth):
        """Any NON-top-level pattern position: lambda/function/let-bound-function
        params, a ctor's own argument, an array item — `pattern()` above is
        the only caller that additionally allows the outermost `as`; every
        other position reaches its `as` through THIS function's own
        alias-wrapping instead, which recurses naturally into every position
        that itself calls `pattern_base` (ctor args, array items, params).

        The wrapping is bare here (`PAs(base, name)`, no parens baked in) —
        each RENDER call site adds exactly the wrap its own grammar position
        needs: `emit_pat`'s ctor-argument loop and the dedicated `emit_param`
        (function/lambda/let-binding params) both add one extra pair of
        parens around a `PAs` child; a plain array item needs none (verified
        directly against the app for all three positions, including the
        paren-count edge cases already known from `pattern()`'s v1.5 work —
        0-arg ctor / Int alias bases still need their OWN inner paren in
        every position, unchanged by nesting depth)."""
        base = self._pattern_base_core(depth)
        if not isinstance(base, PAs) and self.chance(0.08):
            return PAs(base, self.pick(self.vars))
        return base

    def _pattern_base_core(self, depth):
        r = self.rng.random()
        if r < 0.32:  return PVar(self.pick(self.vars))
        if r < 0.42:  return PWild()
        if r < 0.50:  return self.gen_pint()
        if r < 0.57:  return PStr(self.str_word())
        if r < 0.63:  return PChar(self.char_content())
        if r < 0.75:  return PRecord([self.pick(self.fields) for _ in range(self.rng.randint(1, 2))])
        if depth > 0 and r < 0.85:
            k = self.rng.randint(0, 2)
            return PArray([self.pattern_base(depth - 1) for _ in range(k)])
        # Constructor pattern. Current Gren allows AT MOST ONE argument (a
        # multi-field variant carries a record); `Ctor a b` does not parse.
        return self.pctor_ref(depth)

    def pctor_ref(self, depth):
        """Like `ctor_ref`, but for a `when`-branch/lambda/etc. pattern: half
        the time matches a declared union constructor with its REAL arity
        (0-arg bare, "record"-payload matched via `PRecord`, "value"-payload
        via a nested pattern) instead of the generic pool's arity-independent
        of any real declaration.

        A generic-pool pick (never a `declared_ctors` one — those are this
        module's OWN unions, in scope unqualified) is sometimes qualified
        (`Maybe.Just y`), reusing the same fake-module pool `Qual` draws
        from — verified directly against the app in every position this
        reaches: bare 0-arg, applied to a nested pattern, and (via
        `pattern`'s `PAs` wrapper) `as`-aliased."""
        if self.declared_ctors and self.chance(0.5):
            name, kind = self.pick(self.declared_ctors)
            if kind == "none" or depth <= 0:
                return PCtor(name, [])
            if kind == "record":
                return PCtor(name, [PRecord([self.pick(self.fields)
                                             for _ in range(self.rng.randint(1, 2))])])
            return PCtor(name, [self.pattern_base(depth - 1)])
        mod = self.pick(self.mods) if self.chance(0.35) else None
        if depth <= 0 or self.chance(0.4):
            return PCtor(self.pick(self.ctors), [], mod=mod)
        return PCtor(self.pick(self.ctors), [self.pattern_base(depth - 1)], mod=mod)

    # -- types (inline only) ----------------------------------------------

    def qualify_type_name(self, name):
        """Sometimes qualifies a builtin-style type name with a fake module
        (`Maybe.Maybe`, `Maybe.Int`) — reusing the same fake-module pool
        `Qual`/qualified constructor patterns already draw from (arbitrary
        pairing, since gren-format never type-checks). Verified directly
        against the app as a signature/alias-RHS/record-field/port/variant-
        arg type, both as a bare `con` and as an `app` head."""
        if self.chance(0.3):
            return self.pick(self.mods) + "." + name
        return name

    def gen_type_arg(self, depth, var_pool):
        """One argument of a type application. A concrete con, a var, or — when
        depth allows — a nested application (`Array (Maybe a)`). `_type_atom`
        parenthesizes a nested `app` automatically, so this returns the bare IR.
        Kept to con/var/app (no bare arrow or record arg) — those need their own
        verification pass and are a separate expansion target."""
        r = self.rng.random()
        if depth <= 0 or r < 0.5:
            return ("con", self.qualify_type_name(self.pick(TYPE_CONS)))
        if r < 0.75:
            return ("var", self.pick(var_pool))
        return self.gen_type_app(depth - 1, var_pool)

    def gen_type_app(self, depth, var_pool):
        """A type application `Head arg…` with a realistic arg count from
        `self.type_apps` — concrete (`Array String`), multi-arg (`Dict String
        Int`), and nested (`Array (Array a)`) via `gen_type_arg`. The head may
        be qualified like any other type name (`Array.Dict String Int`)."""
        head, arity = self.pick(self.type_apps)
        args = [self.gen_type_arg(depth, var_pool) for _ in range(arity)]
        return ("app", self.qualify_type_name(head), args)

    def gen_type(self, depth, vars=None, top=False):
        """`top=True` marks a call that generates the WHOLE type of a
        signature/alias RHS (the only two call sites that pass it) — never a
        nested field type, type-app arg, or arrow segment, which always leave
        it False. A record/exrecord type may only be emitted `broken`
        (multi-line, with per-field lead comments) when `top` is True: the
        flat single-line `emit_type` renderer is what handles every non-top
        occurrence, and it has no way to render a comment, so a comment on a
        non-top record field would be silently dropped from the output —
        `top` is the guarantee that never happens. See GENERATOR.md's
        "Record type comments" section."""
        r = self.rng.random()
        var_pool = vars if vars else ["a", "b", "c"]
        if depth <= 0 or r < 0.4:
            return ("con", self.qualify_type_name(self.pick(TYPE_CONS)))
        if r < 0.55:
            return ("var", self.pick(var_pool))
        if r < 0.7:
            return self.gen_type_app(depth, var_pool)
        if r < 0.85:
            k = self.rng.randint(2, 3)
            return ("arrow", [self.gen_type(depth - 1, vars) for _ in range(k)])
        k = self.rng.randint(1, 3)
        # Sometimes an EXTENSIBLE record `{ base | field : T }` — a type var
        # (drawn from the same pool as a bare `var` type) extends the record.
        # gren-format never type-checks, so any lowercase base parses; realistic
        # ones (an alias param, `type alias Ext a = { a | … }`) fall out
        # naturally since the pool is the enclosing params for an alias RHS.
        is_ext = self.chance(0.35)
        broken = top and self.chance(0.4)
        fields = []
        for i in range(k):
            ftype = self.gen_type(depth - 1, vars)  # top=False: never itself broken
            # A field's own-line lead comment needs a dedicated line to sit
            # on. A broken PLAIN record glues field 0 onto the `{` line (no
            # room before it), but a broken EXTENSIBLE record gives every
            # field, including 0, its own `| `/`, ` line below `{ base` — both
            # verified directly against the app (RecordTypeLayoutByAuthor /
            # ExtensibleRecordTypeTrailingComment / SignatureRecordTypeComment).
            eligible = broken and (is_ext or i > 0)
            lead = self.comment() if (eligible and self.chance(0.3)) else None
            fields.append((self.pick(self.fields) + str(i), ftype, lead))
        if is_ext:
            return ("exrecord", self.pick(var_pool), fields, broken)
        return ("record", fields, broken)

    def maybe_arrow_comment(self, t, broken):
        """Maybe a comment riding one of an author-broken arrow type's `->`
        continuations (README divergence #5) — never the first segment,
        which has no leading `->` to ride."""
        if not broken or t[0] != "arrow":
            return None
        segs = _flatten_arrow(t)
        if len(segs) < 2:
            return None
        c = self.comment(kinds=("line", "block"))
        if c is None:
            return None
        idx = self.rng.randint(1, len(segs) - 1)
        return (idx, c)

    # -- declarations / module --------------------------------------------

    def doc_comment(self):
        """Maybe a `{-| ... -}` doc-comment content: None, a one-line
        string, or a list of 1-3 raw content lines. Doc comments are
        AST-level, not Context — excluded from the comment-preservation
        oracle, so plain prose (no unique `kN` tokens) is fine."""
        if not self.chance(0.3):
            return None
        words = " ".join(self.pick(WORDS) for _ in range(self.rng.randint(2, 4)))
        if self.chance(0.6):
            return words.capitalize() + "."
        k = self.rng.randint(1, 3)
        return [" ".join(self.pick(WORDS) for _ in range(self.rng.randint(2, 4)))
                for _ in range(k)]

    def decl(self, i):
        name = "fn%d" % i
        nparams = self.rng.randint(0, 3)
        params = [self.pattern_base(self.max_depth) for _ in range(nparams)]
        body, trailing = self.gen_body_with_trailing(lambda: self.value(self.max_depth))
        sig = None
        sig_broken = False
        if self.chance(0.4):
            k = nparams + 1
            sig = ("arrow", [self.gen_type(2) for _ in range(k)]) if k > 1 \
                  else self.gen_type(2, top=True)
            sig_broken = sig[0] == "arrow" and self.chance(0.5)
        arrow_comment = self.maybe_arrow_comment(sig, sig_broken) if sig is not None else None
        doc = self.doc_comment()
        lead = None
        if doc is None and self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        return Decl(name, params, body, sig=sig, sig_broken=sig_broken,
                    doc=doc, lead=lead, trailing=trailing,
                    arrow_comment=arrow_comment)

    def type_params(self):
        if not self.chance(0.4):
            return []
        return self.rng.sample(["a", "b"], self.rng.randint(1, 2))

    def type_alias(self, i):
        name = "Alias%d" % i
        params = self.type_params()
        rhs = self.gen_type(2, params, top=True)
        broken = rhs[0] == "arrow" and self.chance(0.5)
        arrow_comment = self.maybe_arrow_comment(rhs, broken)
        doc = self.doc_comment()
        lead = None
        if doc is None and self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return TypeAliasDecl(name, params, rhs, broken=broken, doc=doc,
                             lead=lead, trailing=trailing,
                             arrow_comment=arrow_comment)

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
        var_pool = params if params else ["a", "b", "c"]
        if depth <= 0 or r < 0.45:
            return ("con", self.qualify_type_name(self.pick(TYPE_CONS)))
        if r < 0.65:
            return ("var", self.pick(var_pool))
        if r < 0.85:
            return self.gen_type_app(depth, var_pool)
        k = self.rng.randint(2, 3)
        return ("arrow", [self.variant_arg_type(depth - 1, params) for _ in range(k)])

    def variant_payload(self, params):
        r = self.rng.random()
        if r < 0.4:
            return None
        if r < 0.7:
            k = self.rng.randint(1, 2)
            fields = [(self.pick(self.fields) + str(j), self.gen_type(1, params), None)
                      for j in range(k)]
            return ("record", fields)
        return ("args", [self.variant_arg_type(1, params)])

    def union(self, i, name=None, params=None):
        name = name if name is not None else "Union%d" % i
        params = params if params is not None else self.type_params()
        k = self.rng.randint(1, 4)
        variants = []
        for _ in range(k):
            vname = self.pick(["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]) \
                    + str(self.next_cid())
            lead = self.comment() if self.chance(0.3) else None
            trailing = self.comment()
            payload = self.variant_payload(params)
            variants.append(Variant(vname, payload, lead=lead, trailing=trailing))
            kind = "none" if payload is None else payload[0]  # "none"/"record"/"args"->"value"
            self.declared_ctors.append((vname, "value" if kind == "args" else kind))
        # A `--` trailing comment or an own-line lead comment can't share the
        # variant-list's flat line (README "Custom types"), so either forces
        # the broken (one-variant-per-line) layout.
        forced = any(v.lead is not None for v in variants) or \
                 any(v.trailing is not None and v.trailing[0] == "line" for v in variants)
        broken = forced or self.chance(0.5)
        doc = self.doc_comment()
        lead = None
        if doc is None and self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        # The decl's own trailing comment rides the union's LAST rendered
        # line (`emit_decl`), same as the last variant's own trailing — a
        # PRE-EXISTING collision (found incidentally while verifying the
        # multiline-string trailing-comment addition, unrelated to it): if
        # the last variant already has a LINE (`--`) trailing comment, `--`
        # has no closing delimiter, so anything appended after it becomes
        # part of THAT SAME comment's text instead of a second, distinct
        # comment (`| Delta7 -- k8 -- k10` is really ONE comment reading
        # "k8 -- k10", not two) — silently merging two comments that were
        # meant to stay separate. A BLOCK (`{- -}`) last-variant trailing is
        # self-delimiting, so it's safe to append after; only the LINE case
        # needs the guard.
        last_is_line = variants and variants[-1].trailing is not None \
                       and variants[-1].trailing[0] == "line"
        trailing = None if last_is_line else self.comment()
        return UnionDecl(name, params, variants, broken=broken, doc=doc,
                         lead=lead, trailing=trailing)

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
        arrow_comment = self.maybe_arrow_comment(t, broken)
        doc = self.doc_comment()
        lead = None
        if doc is None and self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return PortDecl(name, t, broken=broken, doc=doc, lead=lead, trailing=trailing,
                        arrow_comment=arrow_comment)

    def manager_type(self, name):
        """The `command`/`subscription` handler type an effect module's
        `where { ... }` clause names — a union with a single `msg` type
        param, matching the real convention (`type MyCmd msg = ...` in
        core/src/Task.gren, core/src/Time.gren) though the parser does not
        check the name or its shape at all."""
        return self.union(0, name=name, params=["msg"])

    def effect_header(self):
        """Bake an effect module's manager declaration: which of
        `command`/`subscription` are present (never neither — the parser has
        no such shape), each naming a fresh `manager_type`, plus an optional
        short block comment on one handler. Always emitted command-first —
        verified directly against the app that gren-format canonicalizes the
        clause to that order unconditionally, so a subscription-first input
        both gets reordered AND (if it carried the comment) has the comment
        relocated by that reordering; baking canonical order sidesteps
        exercising that already-covered renormalization path (`AmbiguousEffectModule`)
        redundantly and keeps the comment-placement coverage clean. Returns
        `(effect, manager_decls)` — `effect` is `None` or a list of
        `(field, name, comment|None)` in emission order; `manager_decls` are
        the `UnionDecl`s to splice into the module's declarations."""
        r = self.rng.random()
        if r < 0.4:
            has_cmd, has_sub = True, False
        elif r < 0.7:
            has_cmd, has_sub = False, True
        else:
            has_cmd, has_sub = True, True
        entries = []
        decls = []
        if has_cmd:
            cname = "EffCmd%d" % self.rng.randint(0, 9999)
            cmt = self.comment(kinds=("block",))
            entries.append(("command", cname, cmt))
            decls.append(self.manager_type(cname))
        if has_sub:
            sname = "EffSub%d" % self.rng.randint(0, 9999)
            cmt = self.comment(kinds=("block",))
            entries.append(("subscription", sname, cmt))
            decls.append(self.manager_type(sname))
        return entries, decls

    def infix_decl(self, i):
        assoc = self.pick(["left", "right", "non"])
        prec = self.rng.randint(0, 9)
        symbol = self.custom_ops[i % len(self.custom_ops)]
        fn = "infixFn%d" % i
        lead = None
        if self.chance(self.crate):
            lead = [self.comment() or ("line", "k%d" % self.next_cid())]
        trailing = self.comment()
        return InfixDecl(assoc, prec, symbol, fn, lead=lead, trailing=trailing)

    def next_cid(self):
        c = self.cid
        self.cid += 1
        return c

    def import_exposing_items(self):
        """A mixed import `exposing` list — value names, type names (bare `T` or
        open `T(..)`), and operators `(|=)`, in arbitrary author order (the
        formatter sorts them operators → types → values). Distinct within the
        list; at least one item."""
        items = []
        items += self.rng.sample(self.vars, k=self.rng.randint(0, 2))
        for tn in self.rng.sample(self.exp_types, k=self.rng.randint(0, 2)):
            items.append(tn + ("(..)" if self.chance(0.4) else ""))
        items += self.rng.sample(self.exp_ops, k=self.rng.randint(0, 2))
        if not items:
            items.append(self.pick(self.vars))
        self.rng.shuffle(items)
        return items

    def import_stmt(self, i):
        """One `import` — see the `Import` class for the shapes covered.

        `blank`/`lead`/`anchor` are eligible on EVERY import including the
        first. An earlier version gated them behind `i > 0`, reasoning that a
        marker before the first import merely doubles up with the header's own
        spacing; that reasoning predates cd1afeb, which made the head of a run
        obey the same rule as the rest, so the head is now exactly the position
        worth generating — a `lead` there must travel with its import when the
        run sorts, and nothing else in the corpus proves it does."""
        r = self.rng.random()
        mod = self.pick(["Foo", "Bar", "Baz", "Qux"]) + str(i)
        as_name, exposing = None, None
        if r < 0.4:
            pass
        elif r < 0.6:
            as_name = "M" + str(i)
        elif r < 0.8:
            exposing = self.import_exposing_items()
        else:
            exposing = "(..)"
        lead, blank, anchor = None, False, None
        if self.chance(0.2):
            lead = self.forced_comment()
        if self.chance(0.2):
            blank = True
            # A section header: own-line comment with the blank UNDER it, so it
            # leads nothing and must stay put while the run below it sorts.
            # Only generated with `blank`, which is what makes it anchored —
            # without one it would be an ordinary `lead`.
            if self.chance(0.4):
                anchor = self.forced_comment()
        trailing = self.comment()
        item_lead = item_trailing = None
        if isinstance(exposing, list) and self.chance(0.3):
            idx = self.rng.randrange(len(exposing))
            c = self.forced_comment()
            if c is not None:
                if self.chance(0.5):
                    item_lead = (idx, c)
                else:
                    item_trailing = (idx, c)
        return Import(mod, as_name=as_name, exposing=exposing, lead=lead, blank=blank,
                      trailing=trailing, item_lead=item_lead, item_trailing=item_trailing,
                      anchor=anchor)

    def module(self):
        name = "Gen%d" % self.rng.randint(0, 999)
        # Skewed toward small, but with a long tail: a run only exercises the
        # sort if it has several imports in it, and the boundary markers split
        # what is generated into shorter runs still.
        nimp = self.rng.choice([0, 1, 2, 2, 3, 3, 4, 5, 6, 7])
        imports = [self.import_stmt(i) for i in range(nimp)]
        # Own-line comments below the last import — they lead nothing and stay
        # at the end of the block while the run sorts.
        imports_tail = []
        if imports and self.chance(0.15):
            imports_tail = self.forced_comments(self.rng.randint(1, 2))
        ninfix = self.rng.randint(0, 2) if self.chance(0.4) else 0
        infixes = [self.infix_decl(i) for i in range(ninfix)]
        # `effect module`/`port module` are mutually exclusive header
        # keywords (the parser has no combined form — see `GENERATOR.md`),
        # so an effect module never generates a `port` declaration below;
        # its manager types are spliced into `decls` up front instead.
        effect, manager_decls = (None, [])
        if self.chance(0.2):
            effect, manager_decls = self.effect_header()
        ndecls = self.rng.randint(1, 4)
        decls = list(manager_decls)
        for i in range(ndecls):
            r = self.rng.random()
            if r < 0.65:
                decls.append(self.decl(i))
            elif r < 0.8:
                decls.append(self.type_alias(i))
            elif r < 0.95:
                decls.append(self.union(i))
            elif effect is not None:
                decls.append(self.union(i))
            else:
                decls.append(self.port(i))
        return Module(name, imports, decls, infixes=infixes, doc=self.doc_comment(),
                      exposing=self.module_exposing(decls), effect=effect,
                      imports_tail=imports_tail)

    def module_exposing(self, decls):
        """The module header's export list. Half the time the wildcard `(..)`;
        otherwise an EXPLICIT list of the real declared names — a union may be
        exposed open (`Name(..)`, exposing its constructors) or closed, every
        other decl by its bare name. Arbitrary order (the formatter sorts it
        operators → types → values). Explicit lists reference only names this
        module actually declares, so the module is well-formed, not just
        parseable."""
        if self.chance(0.5):
            return "(..)"
        items = []
        for d in decls:
            if isinstance(d, UnionDecl):
                items.append(d.name + ("(..)" if self.chance(0.5) else ""))
            else:
                items.append(d.name)
        self.rng.shuffle(items)
        return "(" + ", ".join(items) + ")"


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


# ─────────────────── permutation oracle (author-order invariance) ─────────

def _base_name(item):
    """The sort key of an exposing item, minus its `(..)` suffix / operator
    parens — two items with the same base name are a TIE, and a tie makes the
    author's order observable through the stable sort."""
    s = item[:-4] if item.endswith("(..)") else item
    return s.strip("()")


def _split_runs(imports):
    """Split into runs at the blank lines. A blank line is the only boundary
    (docs/sorting.md), so an import carrying `blank` starts a new run."""
    runs, cur = [], []
    for imp in imports:
        if imp.blank and cur:
            runs.append(cur)
            cur = []
        cur.append(imp)
    if cur:
        runs.append(cur)
    return runs


def _reverse_run(run):
    """Reverse one import run, leaving the position-anchored markers behind.

    `blank` and `anchor` describe the SLOT, not the import — the run's blank
    line and the section header above it stay where they are while the imports
    beneath them move. Returns None if the run cannot be safely reordered."""
    if len(run) < 2:
        return None
    if any(imp.anchor is not None for imp in run[1:]):
        return None  # an anchor off the head slot: not a shape we can pin
    names = [imp.mod for imp in run]
    if len(set(names)) != len(names):
        return None  # duplicate module names sort stably, so order is observable
    head_blank, head_anchor = run[0].blank, run[0].anchor
    rev = list(reversed(run))
    for imp in rev:
        imp.blank, imp.anchor = False, None
    rev[0].blank, rev[0].anchor = head_blank, head_anchor
    return rev


def _reverse_exposing(imp):
    """Reverse an import's `exposing` list, index 0 pinned, remapping the
    commented item's index. True if anything moved.

    Index 0 is pinned because a comment leading the FIRST item is not attached
    to that item at all: the parser hands it back as a header comment after
    `exposing` (docs/sorting.md, "A comment written before the first name"),
    so it stays at the front of the list while the names sort. The same comment
    at index >= 1 travels with its name. Moving an item across that boundary
    would change the output for a legitimate reason and report a false find."""
    items = imp.exposing
    if not isinstance(items, list) or len(items) < 3:
        return False  # with index 0 pinned, < 3 items has no other arrangement
    bases = [_base_name(it) for it in items]
    if len(set(bases)) != len(bases):
        return False
    n = len(items)
    perm = [0] + list(range(n - 1, 0, -1))  # old index now sitting in each slot
    imp.exposing = [items[j] for j in perm]
    inv = {old: new for new, old in enumerate(perm)}
    if imp.item_lead is not None:
        idx, c = imp.item_lead
        imp.item_lead = (inv[idx], c)
    if imp.item_trailing is not None:
        idx, c = imp.item_trailing
        imp.item_trailing = (inv[idx], c)
    return True


def _reverse_header_exposing(s):
    """Reverse the module header's export list (a pre-rendered string). It
    carries no comments, so this is a plain reordering. Returns None if the
    list is `(..)`, too short, or not the simple comma-separated vocabulary
    `module_exposing` builds."""
    if not (s.startswith("(") and s.endswith(")")):
        return None
    inner = s[1:-1].strip()
    if inner == ".." or "," not in inner:
        return None
    parts = [p.strip() for p in inner.split(",")]
    for p in parts:
        if "(" in p and not p.endswith("(..)"):
            return None  # not a shape this splitter can round-trip
    if len(set(_base_name(p) for p in parts)) != len(parts):
        return None
    return "(" + ", ".join(reversed(parts)) + ")"


def permute_module(m):
    """A copy of `m` with every sortable list written in a DIFFERENT author
    order, each comment still attached to the same owner. None if there is
    nothing safely reorderable.

    This is the sort's actual contract: reordering imports within a run, or
    names within an `exposing` list, must not change the formatted output.
    Unlike every other oracle here it needs no model of where a comment is
    supposed to land — only that both author orders agree on where it lands.
    A comment that travels with the wrong neighbour is invisible to the
    comment-multiset oracle (which discards positions) and to idempotency
    (a wrong-but-stable placement is still a fixed point); it shows up here.

    Reversal rather than a shuffle: it is a maximal reordering, and being
    deterministic it keeps `--seed` an exact replay."""
    p = copy.deepcopy(m)
    changed = False

    runs = _split_runs(p.imports)
    out = []
    for run in runs:
        rev = _reverse_run(run)
        if rev is None:
            out.extend(run)
        else:
            out.extend(rev)
            changed = True
    p.imports = out

    for imp in p.imports:
        if _reverse_exposing(imp):
            changed = True

    head = _reverse_header_exposing(p.exposing)
    if head is not None:
        p.exposing = head
        changed = True

    return p if changed else None


def check(src, tmpdir, m=None):
    """Run all oracles on `src`. Returns (bucket, detail_dict).
    bucket == 'ok' on full pass; 'quarantine' for a parse failure (generator bug).

    `m` is the module `src` was emitted from; passing it enables the
    author-order permutation oracle, which needs the tree rather than the text.
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

    # Oracle 4: author-order invariance. Rewriting the same module with its
    # imports / exposing names in a different order must format identically.
    if m is not None:
        bucket, detail = _check_permutation(m, src, formatted, tmpdir)
        if bucket is not None:
            return bucket, detail

    return "ok", {"formatted": formatted}


def _check_permutation(m, src, formatted, tmpdir):
    """Format the reordered twin of `m` and require the same output.
    Returns (None, None) when the oracle passes or does not apply.

    A failure of ANY kind on the twin is reported as `sort-order` rather than
    as its own crash/idempotency class, because the artifact a human needs is
    the pair of inputs — the twin's own bucket is visible in the message."""
    perm = permute_module(m)
    if perm is None:
        return None, None
    try:
        psrc = emit_module(perm)
    except Exception as e:
        # The permuter built a tree the emitter cannot render — a bug in this
        # script, not a formatter find. Surfaced as gen-error rather than
        # swallowed, so it cannot quietly disable the oracle.
        return "gen-error", {"msg": "permuted emit failed: %r" % (e,)}
    if psrc == src:
        return None, None
    # Reordering must not lose, add, or rename a comment — the generator's
    # comments are unique `kN` tokens, so this is exact. A permuter that
    # dropped one would show up as a formatter find (two orders, two outputs)
    # when the real fault is in this script; check it before blaming anyone.
    if sorted(re.findall(r"k\d+", src)) != sorted(re.findall(r"k\d+", psrc)):
        return "gen-error", {"msg": "permutation changed the comment multiset"}
    ppath = os.path.join(tmpdir, "permuted.gren")
    with open(ppath, "w") as f:
        f.write(psrc)
    try:
        pshow = run_app(["--show", ppath])
    except subprocess.TimeoutExpired:
        return "sort-order", {"msg": "reordered twin timed out", "permuted": psrc}
    pout = pshow.stdout + pshow.stderr
    if pshow.returncode != 0:
        return "sort-order", {"msg": "reordered twin failed to format: %s"
                                     % first_real_line(pout),
                              "permuted": psrc, "stderr": pout}
    if pshow.stdout != formatted:
        return "sort-order", {"msg": "output depends on the author's order",
                              "permuted": psrc, "formatted": formatted,
                              "perm_formatted": pshow.stdout}
    return None, None


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
    yield m, "infixes", 0
    yield m, "imports", 0
    yield m, "imports_tail", 0
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
    if getattr(m, "doc", None) is not None:
        yield clear_attr(m, "doc")
    if getattr(m, "effect", None):
        for idx, (field, ename, cmt) in enumerate(m.effect):
            if cmt is not None:
                def clr(entries=m.effect, i=idx):
                    entries[i] = (entries[i][0], entries[i][1], None)
                yield clr
    for d in m.infixes:
        if d.lead:
            yield clear_attr(d, "lead")
        if d.trailing is not None:
            yield clear_attr(d, "trailing")
    for imp in m.imports:
        if imp.lead is not None:
            yield clear_attr(imp, "lead")
        if imp.anchor is not None:
            yield clear_attr(imp, "anchor")
        if imp.trailing is not None:
            yield clear_attr(imp, "trailing")
        if imp.item_lead is not None:
            yield clear_attr(imp, "item_lead")
        if imp.item_trailing is not None:
            yield clear_attr(imp, "item_trailing")
    for d in m.decls:
        if getattr(d, "doc", None) is not None:
            yield clear_attr(d, "doc")
        if d.lead:
            yield clear_attr(d, "lead")
        if d.trailing is not None:
            yield clear_attr(d, "trailing")
        if getattr(d, "arrow_comment", None) is not None:
            yield clear_attr(d, "arrow_comment")
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
            if isinstance(node, MultilineStr) and node.trailing is not None:
                yield clear_attr(node, "trailing")
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
            dropped_name = c.decls[i].name
            del c.decls[i]
            # An explicit header export list names the declared decls; once one
            # is gone, fall back to `(..)` so the shrunk module never exposes a
            # removed name (harmless — it parses — but confusing in a repro).
            c.exposing = "(..)"
            # Likewise, if the dropped decl was an effect module's manager
            # type, drop its entry from the where-clause too (and the whole
            # clause if that empties it — an effect module can't have
            # neither `command` nor `subscription`).
            if c.effect is not None:
                remaining = [e for e in c.effect if e[1] != dropped_name]
                c.effect = remaining if remaining else None
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
            bucket, _ = check(src, tmpdir, v)
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
    elif bucket == "sort-order":
        # The artifact is the PAIR: same module, two author orders, two
        # outputs. Both inputs and the diff go in, so the reader never has to
        # re-derive the permutation.
        perm_src = detail.get("permuted", "")
        with open(os.path.join(fdir, "permuted.gren"), "w") as f:
            f.write(perm_src)
        lines.append("input.gren and permuted.gren are the same module written")
        lines.append("in two author orders; the sort should erase the difference.")
        lines.append("")
        a, b = detail.get("formatted"), detail.get("perm_formatted")
        if a is not None and b is not None:
            with open(os.path.join(fdir, "formatted.gren"), "w") as f:
                f.write(a)
            with open(os.path.join(fdir, "permuted.formatted.gren"), "w") as f:
                f.write(b)
            import difflib
            lines.append("format(input) vs format(permuted):")
            lines += list(difflib.unified_diff(
                a.splitlines(), b.splitlines(),
                "format(input)", "format(permuted)", lineterm=""))
        else:
            lines.append("stderr:")
            lines.append(detail.get("stderr", ""))
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
        bucket, detail = check(src, tmp, m)
        if bucket == "ok":
            return seed, "ok", detail, None, None
        if bucket == "quarantine":
            return seed, "quarantine", detail, src, None
        # a real find — shrink it
        try:
            minm = shrink(m, bucket, tmp)
            minsrc = emit_module(minm)
        except Exception:
            minm, minsrc = None, src
        if bucket == "sort-order" and minm is not None:
            # The report's permuted twin and diff have to describe the SHRUNK
            # module, not the one the find came in on — `detail` here still
            # holds the unminimized pair.
            b2, d2 = check(minsrc, tmp, minm)
            if b2 == bucket:
                detail = d2
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
            bucket, detail = check(src, tmp, m)
            print("seed %d -> %s: %s" % (args.seed, bucket, detail.get("msg", "")))
            if bucket == "sort-order" and detail.get("permuted"):
                print("=" * 60)
                print("PERMUTED (same module, different author order):")
                print(detail["permuted"])
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
             "sort-order", "timeout", "gen-error", "quarantine"]
    counts = {b: len(v) for b, v in buckets.items()}
    ok = counts.get("ok", 0)
    finds = sum(counts.get(b, 0) for b in
                ("crash", "ast-mismatch", "non-idempotent", "comment-loss",
                 "sort-order", "timeout"))
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
