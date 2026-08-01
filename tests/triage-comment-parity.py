#!/usr/bin/env python3
"""Cluster the comment-axis parity divergences into reviewable families.

`./matrix-syntax.py --comments` registers ~26k divergences from elm-format in
matrix-comment-baseline.json, of which ~16k are UNREVIEWED (tbd.md item 1). The
baseline records a reason per cell and nothing else, so reviewing them means
regenerating the output pairs -- and 16k diffs is not a review, it is a wall.

This is the sampler tbd.md's "suggested approach" asks for, made mechanical: it
re-runs the UNREVIEWED cells, re-runs each cell's UNCOMMENTED form for reference,
and sorts every divergence into one family by three independent features.

  parens   elm deleted a redundant paren gren keeps (README #10). Detected and
           then factored OUT of every other feature: otherwise a #10 cell that
           also moves its comment lands in a bucket per paren shape, and the
           real families never surface.

  owner    the comment's index in the paren-free code-token stream. Both
           formatters emit the same code tokens, so the comment is the only
           thing that can move. Same index => they agree what it is attached to
           and the divergence is layout only. Different index => the family is
           named by the TOKENS THE COMMENT CROSSED, which is the reviewable
           sentence: "the comment was written after `{`; gren hoists it above
           the whole record, elm keeps it inside".

  layout   each side's comment-deleted output against THAT SAME FORMATTER's
           output for the uncommented cell. gren unchanged + elm changed means
           elm re-flowed because of the comment and gren did not -- the
           deliberate direction (#12 / #18, "your line breaks are your layout").
           gren changed + elm unchanged is the suspicious direction, and gets
           its own families rather than being merged with the first.

This tool CLASSIFIES; it does not register anything. Nothing here writes the
baseline -- the 2026-07-15 lesson (README "Reclassifying is not a formality")
was that a plausible blanket test can clear 40 of 46 cells and still be wrong,
so a family is a unit of REVIEW, not a verdict. Each family is stated so that a
reviewer can accept or reject it as a whole and then hand-write the rule into
`comment_family` in matrix-syntax.py with the evidence attached.

    ./triage-comment-parity.py --collect -j 12     # regenerate the pairs
    ./triage-comment-parity.py                     # the family table
    ./triage-comment-parity.py --show A1           # examples from one family
    ./triage-comment-parity.py --show A1 -n 6      # more of them
    ./triage-comment-parity.py --keys A1           # its baseline keys, one per line
    ./triage-comment-parity.py --spread A1         # its construct x context coverage

REVIEWING, rather than understanding, wants a different cut -- `--review`. It
buckets on the DISAGREEMENT: the lines the two outputs differ on, with names and
literals flattened and the surrounding context dropped, so the same question
asked of `1` / `1.5` / `'c'` and asked inside a call argument / a record field /
a pipeline step collapses to one entry with a count. That turns the pile into a
few hundred decisions, front-loaded -- the top 30 entries typically cover ~60% of
it.

    ./triage-comment-parity.py --review            # entries, biggest first
    ./triage-comment-parity.py --review -n 25      # more per page
    ./triage-comment-parity.py --review --from 25  # the next page
    ./triage-comment-parity.py --review --family B2   # within one family
    ./triage-comment-parity.py --group 7           # entry 7 in full, every cell key
    ./triage-comment-parity.py --key intLit/updateField@flat#g9.line.trail
"""
import argparse
import collections
import concurrent.futures as cf
import difflib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
PAIRS = HERE / "comment-parity-pairs.jsonl"       # gitignored working data
BASES = HERE / "comment-parity-bases.jsonl"

MARK = "¤"
COMMENT_RE = re.compile(r"\{-\s*" + MARK + r"\s*-\}|--\s*" + MARK + r"[ \t]*")
TOKEN_RE = re.compile(r"@C|[A-Za-z_][A-Za-z0-9_.]*|\d+\.?\d*|'[^']*'|\"[^\"]*\"|[()\[\]{},]|[|<>+*/\\=:.-]+")
PARENS = {"(", ")"}
OPENERS = {"{", "["}
CLOSERS = {"}", "]"}
KEYWORDS = {"if", "then", "else", "when", "case", "of", "is", "let", "in"}
GENERIC = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*|\d+\.?\d*|'[^']*'|\"[^\"]*\")$")


def load_matrix():
    spec = importlib.util.spec_from_file_location("mx", HERE / "matrix-syntax.py")
    mx = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    spec.loader.exec_module(mx)
    return mx


# ------------------------------------------------------------------ COLLECT

def collect(jobs):
    """Re-run every UNREVIEWED comment cell, and every uncommented syntax cell.

    The predicate audit is skipped -- the axis reports 0 predicate lies, and this
    tool only needs the output pair.
    """
    mx = load_matrix()
    unreviewed = {k for k, v in mx.load_comment_baseline().items() if v == "UNREVIEWED"}
    syntax_cells = mx.enumerate_cells(mx.CONSTRUCTS, mx.CONTEXTS, mx.VARIANTS)
    comment_cells, _, _ = mx.enumerate_comment_cells(
        syntax_cells, list(mx.COMMENT_KINDS), mx.COMMENT_POSITIONS)
    todo = [c for c in comment_cells if mx.comment_key(c) in unreviewed]
    print(f"{len(todo)} UNREVIEWED cells, {len(syntax_cells)} base cells", file=sys.stderr)

    def gren_format(source):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "M.gren"
            path.write_text(source)
            try:
                shown = mx.run("--show", path)
            except subprocess.TimeoutExpired:
                return None
            return mx.to_elm(shown.stdout).strip() if shown.returncode == 0 else None

    def elm_format(source):
        try:
            r = subprocess.run([mx.ELM_FORMAT, "--stdin"], input=mx.to_elm(source),
                               capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    def one_comment(cell):
        gren = gren_format(cell["source"])
        if gren is None:
            return dict(key=mx.comment_key(cell), error="show-failed")
        elm = elm_format(cell["source"])
        if elm is None:
            return dict(key=mx.comment_key(cell), error="untranslatable")
        if gren == elm:
            return dict(key=mx.comment_key(cell), error="no-longer-diverges")
        return dict(key=mx.comment_key(cell), construct=cell["construct"],
                    context=cell["context"], variant=cell["variant"],
                    ordinal=cell["ordinal"], ckind=cell["kind"], cpos=cell["position"],
                    source=cell["source"], gren=gren, elm=elm)

    def one_base(cell):
        construct, context, variant = cell
        source = mx.source_for(mx.variant_atom(construct, variant), context.template)
        key = f"{construct.name}/{context.name}@{variant}"
        return dict(key=key, gren=gren_format(source) or "", elm=elm_format(source) or "")

    for path, items, fn in ((BASES, syntax_cells, one_base), (PAIRS, todo, one_comment)):
        done = 0
        with path.open("w") as fh, cf.ThreadPoolExecutor(jobs) as ex:
            for r in ex.map(fn, items):
                fh.write(json.dumps(r) + "\n")
                done += 1
                if done % 1000 == 0:
                    print(f"  {path.name}: {done}/{len(items)}", file=sys.stderr, flush=True)
        print(f"wrote {done} -> {path.name}", file=sys.stderr)


# ----------------------------------------------------------------- FEATURES

def body(out):
    """The rendered decl body: everything after `v =`, trailing blanks dropped."""
    lines = out.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("v ="):
            rest = ([] if ln.strip() == "v =" else [ln[3:].strip()]) + lines[i + 1:]
            while rest and not rest[-1].strip():
                rest.pop()
            return rest
    return lines


def toks(out, drop_parens=False):
    t = TOKEN_RE.findall(COMMENT_RE.sub(" @C ", "\n".join(body(out))))
    return [x for x in t if x not in PARENS] if drop_parens else t


def stripped(out):
    """Body lines with the comment deleted; lines it emptied are dropped."""
    res = []
    for ln in body(out):
        if MARK in ln:
            ln = COMMENT_RE.sub("", ln)
        if ln.strip():
            res.append(ln.rstrip())
    return res


def base_lines(out):
    return [ln.rstrip() for ln in body(out) if ln.strip()]


def role(out):
    """Where the comment sits on its own line: alone | leading | trailing | inner."""
    for ln in body(out):
        if MARK not in ln:
            continue
        s = ln.strip()
        if not COMMENT_RE.sub("", s).strip():
            return "alone"
        if s.startswith(("{-", "--")):
            return "leading"
        if s.endswith("-}") or re.search(r"--\s*" + MARK + r"\s*$", s):
            return "trailing"
        return "inner"
    return "?"


def blank_above(out):
    lines = body(out)
    for i, ln in enumerate(lines):
        if MARK in ln:
            return i > 0 and not lines[i - 1].strip()
    return False


def crossed(gi, ei, code):
    """The code tokens the comment passed over. A marker at index i sits just
    before code[i], so the span is the code slice between the two indices."""
    lo, hi = (gi, ei) if gi < ei else (ei, gi)
    return code[lo:hi]


# ----------------------------------------------------------------- FAMILIES
#
# id -> (one-line title, longer statement). The id is what --show / --keys take.
FAMILIES = {
    "A1": ("gren hoists a comment out of the container it was written inside",
           "The comment was written just after an opening `{` / `[`. gren lifts it "
           "onto its own line ABOVE the whole container; elm-format keeps it inside, "
           "on the first item's line. An attachment decision (Comments.gren), not a "
           "layout one -- tbd.md names this family in its suggested approach."),
    "A2": ("gren re-homes a comment written after `,` back onto the previous item",
           "The author wrote the comment leading item N (just after the comma). gren "
           "attaches it trailing item N-1, elm-format keeps it leading item N. Same "
           "trailing-vs-leading axis as #13, but across a comma instead of an operator, "
           "and here it is GREN that moves the comment off where it was written."),
    "A3": ("gren moves a comment written after a record update's `|` to before it",
           "`{ rec | {- c -} fld = 1 }` -- gren renders the comment above the `|`, "
           "elm-format keeps it after. Same shape as A2 for the update bar."),
    "A4": ("gren moves a comment written before a field's `=` to after it",
           "`{ fld {- c -} = 1 }` -- gren renders `{ fld = {- c -} 1 }`, elm-format "
           "keeps it in front of the `=`. gren moves it off the name it was written "
           "beside and onto the value."),
    "A5": ("gren pulls a comment written after a container's closer back inside it",
           "`fn a { rec | a = 1 } {- c -} last` -- gren renders it before the `}`, "
           "elm-format keeps it after. The inverse direction of A1, and the one that "
           "changes which construct the comment reads as belonging to."),
    "A6": ("comment at a keyword boundary (`in` / `else` / `then` / `of` / `->`)",
           "The comment sits in the gap around a keyword and the two formatters put it "
           "on opposite sides. #20 documents the `in` case as forced by a missing "
           "source position for the keyword; `else` / `then` / `of` / `->` are the same "
           "shape and it should be confirmed whether the same reasoning covers them."),
    "B1": ("elm-format floats the comment out on its own line, set off by a blank line",
           "elm lifts the comment away from the code it was written beside and gives it "
           "its own row with a blank line above, breaking the construct open to do it; "
           "gren leaves the author's layout alone. This is the documented #12 / #18 "
           "direction -- 'your line breaks are your layout'."),
    "B7": ("elm-format breaks the surrounding code further, without floating the comment",
           "elm ends up with more lines than gren but the comment itself is not lifted "
           "away -- elm splits the code around it (typically dropping a `<|` / `|>` "
           "operand onto its own row). About elm's operator layout under a comment, "
           "not about comment placement."),
    "B2": ("gren breaks a construct open that elm-format keeps flat",
           "The suspicious direction of B1: a comment makes GREN re-flow (often blowing a "
           "one-line body into 5+ lines) where elm-format renders it inline. A comment "
           "forcing verticality that the code alone would not."),
    "B3": ("both re-flow, and the comment's own line lands at a different indent",
           "Same lines, same words, different leading whitespace -- typically gren's +4 "
           "step against elm's align-under-the-operand column."),
    "B4": ("only the comment line's own indentation differs",
           "Every code line is byte-identical; the comment sits on its own line at a "
           "different column. The cheapest family to decide and the easiest to fix or "
           "accept wholesale."),
    "B5": ("gren strands the comment alone on a line where elm keeps it beside code",
           "Same slot, same code layout, but gren gives the comment its own row. This is "
           "the shape of the two pairing bugs (7c20e15, cd774f5), which is exactly why "
           "the auto-classifier refuses to touch it -- review individually."),
    "B6": ("both re-flow into the same number of lines, arranged differently",
           "Neither side is 'more broken'; the words land on different rows. Small and "
           "heterogeneous -- read these."),
    "X":  ("unclassified", "Did not match any family rule -- read individually."),
}


def classify(r, base):
    """(family id, one-line detail, feature dict) for one divergence."""
    g, e = r["gren"], r["elm"]
    gt_p, et_p = toks(g), toks(e)
    gt, et = toks(g, True), toks(e, True)
    if "@C" not in gt or "@C" not in et:
        return "X", "marker missing from one side", {}

    gcode = [t for t in gt if t != "@C"]
    ecode = [t for t in et if t != "@C"]
    if gcode != ecode:
        return "X", "code tokens differ even with parens ignored", {}
    parens = [t for t in gt_p if t != "@C"] != [t for t in et_p if t != "@C"]

    gi, ei = gt.index("@C"), et.index("@C")
    gs, es = stripped(g), stripped(e)
    b = base.get(f'{r["construct"]}/{r["context"]}@{r["variant"]}')
    gren_reflow = bool(b) and gs != base_lines(b["gren"])
    elm_reflow = bool(b) and es != base_lines(b["elm"])
    who = ("both" if gren_reflow and elm_reflow else "gren" if gren_reflow
           else "elm" if elm_reflow else "neither")
    grole, erole = role(g), role(e)
    info = dict(parens=parens, who=who, grole=grole, erole=erole,
                glines=len(gs), elines=len(es), blank_above_elm=blank_above(e))

    if gi != ei:
        span = crossed(gi, ei, gcode)
        info["crossed"] = " ".join(t if t in KEYWORDS or not GENERIC.match(t) else "term"
                                   for t in span)
        later = "elm" if ei > gi else "gren"
        detail = f'comment crosses `{info["crossed"]}` ({later} puts it later); ' \
                 f'{grole}->{erole}; re-flowed by {who}'
        kws = [t for t in span if t in KEYWORDS or t == "->"]
        if kws:
            return "A6", detail, info
        if "=" in span:
            return "A4", detail, info
        if "|" in span:
            return "A3", detail, info
        if "," in span:
            return "A2", detail, info
        if any(t in CLOSERS for t in span) and later == "elm":
            return "A5", detail, info
        if all(t in OPENERS for t in span):
            return "A1", detail, info
        return "X", detail, info

    if gs == es:
        if grole != erole:
            return "B5", f"gren {grole} / elm {erole}", info
        return "B4", "only the comment line's own indent differs", info

    if [ln.split() for ln in gs] == [ln.split() for ln in es]:
        return "B3", f"same lines, indent differs; re-flowed by {who}", info
    if len(gs) < len(es):
        detail = f'gren {len(gs)}L vs elm {len(es)}L; re-flowed by {who}'
        # The blank line above elm's comment is the signature of elm FLOATING the
        # comment away (#12/#18). Without it, elm merely broke the code around a
        # comment that stayed put -- a different question, so a different family.
        return ("B1" if info["blank_above_elm"] else "B7"), detail, info
    if len(gs) > len(es):
        return "B2", f"gren {len(gs)}L vs elm {len(es)}L; re-flowed by {who}", info
    return "B6", f"{len(gs)}L both; re-flowed by {who}", info



# ------------------------------------------------------------------- REVIEW
#
# The families above are a way to UNDERSTAND the pile. Reviewing it needs a
# different cut: the same layout question asked of `1`, `1.5`, `'c'` and `"s"`
# is four cells and one decision, and reading it four times is how a reviewer
# runs out of patience before running out of pile.
#
# So group by SHAPE: normalize every identifier to `x` and every literal to `0`
# in the source and in both outputs, then bucket on the resulting triple. What
# survives is one entry per distinct layout disagreement, with a count.

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
LIT_RE = re.compile(r"\d+\.?\d*|'[^']*'|\"[^\"]*\"")
KEEP = {"if", "then", "else", "when", "is", "case", "of", "let", "in", "as",
        "module", "exposing", "type", "alias", "port"}


def shape(text):
    """`decl(text)` with every name and literal flattened to `x`, indentation
    kept exactly. Two cells with the same shape pose the same layout question:
    whether the operand is `1`, `1.5`, `'c'` or `one` never changes the answer,
    but where it lands does."""
    out = []
    for ln in decl(text).split("\n"):
        indent = len(ln) - len(ln.lstrip())
        body = ln.strip()
        if MARK not in body:
            body = LIT_RE.sub("x", body)
            body = IDENT_RE.sub(lambda m: m.group(0) if m.group(0) in KEEP else "x", body)
        else:
            # Keep the marker intact; flatten around it.
            head, sep, tail = body.partition(MARK)
            def flat(t):
                return IDENT_RE.sub(lambda m: m.group(0) if m.group(0) in KEEP else "x",
                                    LIT_RE.sub("x", t))
            body = flat(head) + sep + flat(tail)
        out.append(" " * indent + body)
    return "\n".join(out)


def disagreement(r):
    """Just the lines the two outputs differ on, flattened and re-based to
    column 0. The surrounding CONTEXT is dropped on purpose: the same question
    asked inside a call argument, a record field and a pipeline step is one
    question, and it is the atom-local gaps that generate all 25 of them."""
    g = shape(r["gren"]).split("\n")
    e = shape(r["elm"]).split("\n")
    parts = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, g, e).get_opcodes():
        if tag != "equal":
            parts.append((tag, tuple(g[i1:i2]), tuple(e[j1:j2])))
    lines = [ln for _, a, b in parts for ln in a + b if ln.strip()]
    base = min((len(ln) - len(ln.lstrip()) for ln in lines), default=0)

    def rebase(block):
        return tuple(ln[base:] if len(ln) - len(ln.lstrip()) >= base else ln.lstrip()
                     for ln in block)

    return tuple((tag, rebase(a), rebase(b)) for tag, a, b in parts)


def review_groups(rows):
    """Rows bucketed by (family, disagreement), biggest first. Each bucket is
    one decision to make."""
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[(r["family"], disagreement(r))].append(r)
    return sorted(buckets.values(), key=lambda rs: -len(rs))


def print_group(i, rs, verbose=False):
    r = rs[0]
    fam = r["family"]
    title = FAMILIES.get(fam, ("?", ""))[0]
    keys = collections.Counter((x["construct"], x["context"]) for x in rs)
    print(f"[{i}]  {len(rs)} cells  ({fam}: {title})")
    print(f"     {len(keys)} construct x context pairs, "
          f"kinds: {'/'.join(sorted({x['ckind'] for x in rs}))}, "
          f"positions: {'/'.join(sorted({x['cpos'] for x in rs}))}")
    print(f"     e.g. {r['key']}")
    show_example(r, indent="     ")
    if verbose:
        for x in rs[1:]:
            print(f"     also {x['key']}")
        print()


# ------------------------------------------------------------------- REPORT

def load():
    if not PAIRS.exists() or not BASES.exists():
        sys.exit(f"no data -- run `{pathlib.Path(__file__).name} --collect` first")
    rows = [json.loads(l) for l in PAIRS.read_text().splitlines() if l.strip()]
    base = {}
    for l in BASES.read_text().splitlines():
        d = json.loads(l)
        base[d["key"]] = d
    ok = [r for r in rows if "gren" in r]
    skipped = collections.Counter(r["error"] for r in rows if "error" in r)
    for r in ok:
        r["family"], r["detail"], r["info"] = classify(r, base)
    return ok, skipped


def decl(out):
    """Just the declaration -- the module header is identical in every cell."""
    return "\n".join(out.split("\n")[3:]).rstrip()


def show_example(r, indent="  "):
    for label, text in (("wrote", r["source"]), ("gren", r["gren"]), ("elm ", r["elm"])):
        lines = decl(text).split("\n")
        print(f"{indent}{label} | {lines[0]}")
        for ln in lines[1:]:
            print(f"{indent}     | {ln}")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true", help="regenerate the output pairs")
    ap.add_argument("-j", "--jobs", type=int, default=12)
    ap.add_argument("--show", metavar="FAMILY", help="print examples from one family")
    ap.add_argument("--keys", metavar="FAMILY", help="print that family's baseline keys")
    ap.add_argument("--spread", metavar="FAMILY", help="its construct x context coverage")
    ap.add_argument("-n", type=int, default=3, help="examples to print (default 3)")
    ap.add_argument("--review", action="store_true",
                    help="one entry per distinct layout disagreement, biggest first")
    ap.add_argument("--family", metavar="FAMILY", help="restrict --review to one family")
    ap.add_argument("--group", type=int, metavar="N",
                    help="print review group N in full (with every cell key)")
    ap.add_argument("--from", dest="start", type=int, default=0,
                    help="--review: skip the first N groups")
    ap.add_argument("--key", metavar="KEY",
                    help="print one cell by its baseline key, with its review group")
    args = ap.parse_args()

    if args.collect:
        collect(args.jobs)
        return 0

    ok, skipped = load()
    by_family = collections.defaultdict(list)
    for r in ok:
        by_family[r["family"]].append(r)

    if args.keys:
        for r in by_family.get(args.keys, []):
            print(r["key"])
        return 0

    if args.spread:
        rs = by_family.get(args.spread, [])
        spread = collections.Counter((r["construct"], r["context"]) for r in rs)
        print(f"{args.spread}: {len(rs)} cells over {len(spread)} construct x context pairs")
        for (c, x), n in spread.most_common():
            print(f"  {n:5d}  {c}/{x}")
        return 0

    if args.key:
        hit = [r for r in ok if r["key"] == args.key]
        if not hit:
            sys.exit(f"no UNREVIEWED cell with key {args.key!r} "
                     "(it may be registered, or fixed -- try --collect)")
        r = hit[0]
        groups = review_groups(ok)
        idx = next(i for i, g in enumerate(groups) if any(x["key"] == args.key for x in g))
        print(f'{args.key}  [{r["family"]}: {r["detail"]}]')
        print(f"  review group [{idx}], {len(groups[idx])} cells\n")
        show_example(r)
        return 0

    if args.review or args.group is not None:
        rows = ok if not args.family else by_family.get(args.family, [])
        groups = review_groups(rows)
        if args.group is not None:
            if not 0 <= args.group < len(groups):
                sys.exit(f"no group {args.group} (there are {len(groups)})")
            print_group(args.group, groups[args.group], verbose=True)
            return 0
        covered = sum(len(g) for g in groups)
        print(f"{len(groups)} distinct layout disagreements over {covered} cells"
              + (f" in {args.family}" if args.family else ""))
        for n in (10, 25, 50, 100):
            if n < len(groups):
                run = sum(len(g) for g in groups[:n])
                print(f"    top {n:4d} groups cover {run:5d} cells ({100 * run / covered:.0f}%)")
        print()
        end = min(len(groups), args.start + args.n)
        for i in range(args.start, end):
            print_group(i, groups[i])
        shown = sum(len(groups[i]) for i in range(args.start, end))
        print(f"groups {args.start}..{end - 1} of {len(groups)} "
              f"({shown} of {covered} cells).  `--group N` for one in full; "
              f"`--from {end} -n {args.n}` for the next page.")
        return 0

    if args.show:
        rs = by_family.get(args.show, [])
        title, statement = FAMILIES.get(args.show, ("?", ""))
        print(f"{args.show} -- {title}\n{statement}\n\n{len(rs)} cells\n")
        # Spread the examples over distinct construct/context pairs rather than
        # printing n neighbours of one cell, which are near-duplicates.
        seen, shown = set(), 0
        for r in rs:
            sig = (r["construct"], r["context"])
            if sig in seen:
                continue
            seen.add(sig)
            print(f'  {r["key"]}  [{r["detail"]}]')
            show_example(r)
            shown += 1
            if shown >= args.n:
                break
        return 0

    total = len(ok)
    print(f"{total} divergences classified"
          + (f"  ({sum(skipped.values())} skipped: {dict(skipped)})" if skipped else "") + "\n")
    for fid in sorted(by_family, key=lambda f: -len(by_family[f])):
        rs = by_family[fid]
        title = FAMILIES.get(fid, ("?", ""))[0]
        pct = 100.0 * len(rs) / total
        print(f"  {fid:3s} {len(rs):6d}  {pct:4.1f}%  {title}")
        sub = collections.Counter(r["detail"] for r in rs)
        for d, n in sub.most_common(3):
            print(f"                     {n:6d}  {d}")
    print(f"\n{len(by_family)} families. `--show <id>` for examples, "
          f"`--spread <id>` for coverage, `--keys <id>` for the cell list.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
