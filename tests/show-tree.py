#!/usr/bin/env python3
"""Render the Logical Printing Tree or Render.Doc tree for a Gren file as ASCII art.

Calls `gren-format.sh --lpt` or `--render-doc` to get the JSON dump, then prints
it as a `├──`/`└──` tree, in one of two styles:

- regular (default): every node in the JSON, one line each, no collapsing.
  Works generically off the JSON shape — a dict field that is itself a
  `{"type": ...}` object or a list of such objects becomes a child branch;
  every other field is shown inline as `key=value` on the node's own line.
- --condensed: a README-style rendering that collapses a subtree onto one
  line where doing so doesn't lose information, matching the spirit (not a
  byte-exact reproduction) of the worked examples in gren-format-lib/README.md:
    - LPT: a subtree collapses to `Type  "merged source text"` when every
      leaf inside it sits on a single source row (mirrors the formatter's own
      "one row -> one line" rule) and the merged text fits in --width columns.
      A node spanning multiple source rows always expands, since that's
      exactly the layout decision being made visible.
    - Render.Doc: a `Concat` spine is flattened into a `Seq[ ... ]` list first
      (adjacent `Text` nodes merged, `Empty` dropped); a single-child wrapper
      (`Nest`/`Group`/`Align`/`Reset`) chains onto its child with `›`. The
      result collapses onto one line if it fits in --width columns.
  Either way, whatever doesn't collapse expands into a normal indented tree.

Usage:
    ./show-tree.py File.gren                    # LPT, verbose
    ./show-tree.py --condensed File.gren         # LPT, condensed
    ./show-tree.py --render-doc File.gren        # Render.Doc, verbose
    ./show-tree.py --render-doc --condensed File.gren
    ./show-tree.py --condensed --width 100 File.gren
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GREN_FORMAT = os.path.join(HERE, "..", "..", "gren-format", "gren-format.sh")

BRACKETS = {"paren": ("(", ")"), "curly": ("{", "}"), "square": ("[", "]")}


def fetch_json(gren_format_sh, flag, path):
    result = subprocess.run([gren_format_sh, flag, path], capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)
    return json.loads(result.stdout)


def quote(s):
    return json.dumps(s)


def child_prefix_for(prefix, connector):
    """The bar under a branch only continues if this connector was a
    mid-list '├── ' — a '└── ' (last sibling) or a synthetic root label
    ('', '[i] ') has nothing more below it to connect to."""
    return prefix + ("│   " if connector == "├── " else "    ")


def print_tree(node, prefix, connector, one_line, expand):
    """Shared tree printer: `one_line(node)` returns a collapsed string or
    None; `expand(node)` returns (label, children) for when it doesn't fit."""
    line = one_line(node)
    if line is not None:
        print(prefix + connector + line)
        return
    label, children = expand(node)
    if not children:
        print(prefix + connector + label)
        return
    print(prefix + connector + label)
    cprefix = child_prefix_for(prefix, connector)
    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        print_tree(child, cprefix, "└── " if is_last else "├── ", one_line, expand)


# ---------------------------------------------------------------------------
# Regular (verbose) mode: generic JSON-tree printer, no per-node-type knowledge.
# ---------------------------------------------------------------------------

def is_child_node(v):
    return isinstance(v, dict) and "type" in v


def is_child_list(v):
    return isinstance(v, list) and v and all(is_child_node(item) for item in v)


def verbose_one_line(node):
    return None  # never collapse in verbose mode


def verbose_expand(node):
    scalars = [
        f"{k}={v!r}"
        for k, v in node.items()
        if k != "type" and not is_child_node(v) and not is_child_list(v)
    ]
    label = node.get("type", "?")
    if scalars:
        label += "  " + ", ".join(scalars)

    children = []
    for k, v in node.items():
        if is_child_node(v):
            children.append(v)
        elif is_child_list(v):
            children.extend(v)
    return label, children


# ---------------------------------------------------------------------------
# Condensed mode — Render.Doc: flatten Concat spines, chain single-child
# wrappers with '›', collapse to one line when it fits in --width.
# ---------------------------------------------------------------------------

def renderdoc_flatten(node):
    if node.get("type") != "Concat":
        return [node]
    items = renderdoc_flatten(node["a"]) + renderdoc_flatten(node["b"])
    merged = []
    for item in items:
        if item.get("type") == "Empty":
            continue
        if item.get("type") == "Text" and merged and merged[-1].get("type") == "Text":
            merged[-1] = {"type": "Text", "text": merged[-1]["text"] + item["text"]}
        else:
            merged.append(item)
    return merged or [{"type": "Empty"}]


def renderdoc_condensed_text(node):
    t = node["type"]
    if t == "Text":
        return quote(node["text"])
    if t in ("Empty", "HardNl", "Nl", "BreakDoc"):
        return t
    if t == "Concat":
        items = renderdoc_flatten(node)
        if len(items) == 1:
            return renderdoc_condensed_text(items[0])
        return "Seq[ " + ", ".join(renderdoc_condensed_text(i) for i in items) + " ]"
    if t == "Nest":
        return f"Nest {node['n']} › {renderdoc_condensed_text(node['doc'])}"
    return f"{t} › {renderdoc_condensed_text(node['doc'])}"  # Group/Align/Reset


def make_renderdoc_one_line(width):
    def one_line(node):
        text = renderdoc_condensed_text(node)
        return text if len(text) <= width else None
    return one_line


def renderdoc_expand(node):
    t = node["type"]
    if t == "Concat":
        return "Seq", renderdoc_flatten(node)
    if t == "Nest":
        return f"Nest {node['n']}", [node["doc"]]
    if t in ("Group", "Align", "Reset"):
        return t, [node["doc"]]
    return t, []  # Text/Empty/HardNl/Nl/BreakDoc too long to matter, no children


# ---------------------------------------------------------------------------
# Condensed mode — LPT: collapse a subtree to its merged source text only
# when every leaf in it sits on one source row (the formatter's own
# "one row -> one line" rule), so expansion always signals "this construct
# spans multiple source rows."
# ---------------------------------------------------------------------------

LPT_LEAVES_WITH_RANGE = {"UnbreakableText", "RecordUpdate", "PrefixGlue", "EmptyBracketed", "MultilineString"}
LPT_LEAVES_WITH_TEXT = {"UnbreakableText", "SingleLineComment", "BlockComment", "DocComment", "SynthesizedText"}


def lpt_row_span(node):
    t = node["type"]
    if t in LPT_LEAVES_WITH_RANGE:
        return (node["startRow"], node["endRow"])
    if t in ("SingleLineComment", "BlockComment", "DocComment"):
        start = node["startRow"]
        return (start, start + node["value"].count("\n"))
    if t in ("EmptyLine", "SynthesizedText"):
        return None
    spans = [s for s in (lpt_row_span(c) for c in node.get("children", [])) if s is not None]
    if not spans:
        return None
    return (min(s[0] for s in spans), max(s[1] for s in spans))


def lpt_raw_text(node):
    t = node["type"]
    if t in LPT_LEAVES_WITH_TEXT:
        return node["value"]
    if t == "MultilineString":
        return "\n".join(node["lines"])
    if t == "EmptyLine":
        return ""

    parts = [lpt_raw_text(c) for c in node.get("children", [])]
    if t in ("AllAcrossOrAllVertical", "AlwaysVertical"):
        open_, close_ = BRACKETS[node["brackets"]]
        return open_ + ", ".join(parts) + close_
    if t == "EmptyBracketed":
        open_, close_ = BRACKETS[node["brackets"]]
        return open_ + close_
    if t == "RecordUpdate":
        return "{ " + node["baseName"] + " | " + ", ".join(parts) + " }"
    if t in ("PrefixGlue", "Glue"):
        prefix = node.get("prefix", "")
        return prefix + "".join(parts)
    return " ".join(p for p in parts if p != "")


# Box types that always expand (never flatten to merged text), regardless of
# row span or width — kept visible because the box itself is the interesting
# layout decision, the way gren-format-lib/README.md's worked example does
# for Binop/OpAndRhs.
NEVER_COLLAPSE = {"Binop"}


def lpt_contains_forced_expand(node):
    if node["type"] in NEVER_COLLAPSE:
        return True
    return any(lpt_contains_forced_expand(c) for c in node.get("children", []))


def lpt_binop_operator(node):
    """The operator token lives inside the OpAndRhs child, not on Binop
    itself — dig it out so a forced-expand Binop can still show `Binop "++"`
    like the README does."""
    children = node.get("children", [])
    if len(children) >= 2 and children[1].get("type") == "OpAndRhs":
        rhs_children = children[1].get("children", [])
        if rhs_children and rhs_children[0].get("type") == "UnbreakableText":
            return rhs_children[0]["value"]
    return None


def lpt_label(node):
    t = node["type"]
    if t == "OriginalRows":
        return f"OriginalRows[{node['stype']}]"
    if t == "Binop":
        op = lpt_binop_operator(node)
        return f"Binop {quote(op)}" if op is not None else "Binop"
    return t


def make_lpt_one_line(width):
    def one_line(node):
        span = lpt_row_span(node)
        if span is not None and span[0] != span[1]:
            return None  # spans multiple source rows: always expand
        if lpt_contains_forced_expand(node):
            return None  # a NEVER_COLLAPSE box lives inside: keep it visible
        text = lpt_raw_text(node)
        if node["type"] in LPT_LEAVES_WITH_TEXT:
            # Bare leaf: no point repeating its type name, and no extra
            # quoting — a string-literal token's raw text already carries
            # its own quote characters (matches the README's plain display).
            return text
        label = lpt_label(node)
        line = f"{label}  {quote(text)}" if text else label
        if len(line) <= width or not node.get("children"):
            return line
        return None
    return one_line


def lpt_expand(node):
    return lpt_label(node), node.get("children", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", help="path to a .gren source file")
    parser.add_argument("--render-doc", action="store_true", help="show the Render.Doc tree instead of the LPT")
    parser.add_argument("--condensed", action="store_true", help="collapse subtrees onto one line (README style)")
    parser.add_argument("--width", type=int, default=80, help="line-width cap for --condensed (default: 80)")
    parser.add_argument("--gren-format-sh", default=GREN_FORMAT, help="path to gren-format.sh (default: ../../gren-format/gren-format.sh)")
    args = parser.parse_args()

    flag = "--render-doc" if args.render_doc else "--lpt"
    data = fetch_json(args.gren_format_sh, flag, args.file)

    if args.render_doc:
        one_line = make_renderdoc_one_line(args.width) if args.condensed else verbose_one_line
        expand = renderdoc_expand if args.condensed else verbose_expand
        for i, root in enumerate(data):
            print_tree(root, "", f"[{i}] ", one_line, expand)
    else:
        one_line = make_lpt_one_line(args.width) if args.condensed else verbose_one_line
        expand = lpt_expand if args.condensed else verbose_expand
        print_tree(data, "", "", one_line, expand)


if __name__ == "__main__":
    main()
