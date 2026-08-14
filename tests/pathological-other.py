#!/usr/bin/env python3
"""Boundary/pathological-input sweep: the non-depth-shaped cases
`pathological-nesting.py` doesn't reach.

`pathological-nesting.py` varies NESTING DEPTH (parens/list/record/lambda/
if-chain/unary-minus/binop-chain/pipeline-chain) and found the one performance
bug this repo has (an O(2^depth) render hang). Depth is one axis; this script
covers the others: very long identifiers, files
that are all comments, empty modules, CRLF line endings, unicode in
strings/identifiers, huge single-line input. None of those are nesting-depth
shaped, so the bisection-on-depth engine above doesn't reach them.

Two kinds of probe:

  size sweeps — geometrically grow a size parameter (identifier length,
      flat-list/record/module BREADTH, comment/string length) and bisect to
      the exact breaking point, same engine as pathological-nesting.py.
      Breadth/length bugs (an O(n^2) string- or line-building routine) look
      exactly like depth bugs from the outside: fine up to a point, then a
      crash/hang/timeout. A flat wide list is also literally "huge single-line
      input" — layout here is author-driven (no page width), so a list
      written on one line stays on one line no matter how long.

  one-shot scenarios — discrete cases with no natural size axis: empty
      modules, a small hand-written all-comment file, CRLF line endings
      (batched over every local fixture, diffed against the known-canonical
      LF formatting), and hand-picked unicode identifiers/string content
      chosen from the parser's actual character classes (`\\p{Ll}`/`\\p{Lu}`
      for leading chars — see `compiler-common`'s `Parse/Variable.gren`) so
      the legal/illegal cases are deliberate, not guessed. Each runs `--show`
      once and is bucketed the same way `corpus-check.py` buckets a failure.
      A clean parse-reject on an illegal identifier is expected and not a
      finding; a crash is, regardless of whether the input was legal.

Usage:
    ./pathological-other.py                    # everything
    ./pathological-other.py --sweep-only
    ./pathological-other.py --scenario-only
    ./pathological-other.py --size long-identifier
    ./pathological-other.py --scenario unicode-identifiers
    ./pathological-other.py -v

Rebuild the `gren-format` app first (`cd ../../gren-format && ./build.sh`) —
this shells out to it.
"""

import argparse
import os
import subprocess
import sys

from corpus import corpus_files

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "..", "gren-format", "app")
OUT_DIR = os.path.join(HERE, "pathological-other-out")


# ── Shared plumbing (classify/run_app mirror pathological-nesting.py) ───────

def classify(stdout, stderr, code, timed_out):
    if timed_out:
        return "timeout"
    if code == 0:
        return "ok"
    blob = stdout + stderr
    if "Maximum call stack size exceeded" in blob:
        return "stack-overflow"
    if "FAILED TO PARSE" in blob:
        return "parse-reject"
    if "NOT IDEMPOTENT" in blob:
        return "non-idempotent"
    if "Please report this" in blob or "box:" in blob or "unreachable" in blob:
        return "crash"
    return "error"  # includes AST-mismatch, which prints no distinct banner


def run_app(flag, path, timeout):
    try:
        r = subprocess.run(["node", APP, flag, path],
                           capture_output=True, text=True, timeout=timeout)
        return classify(r.stdout, r.stderr, r.returncode, False), r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return "timeout", "", "(timed out after %ss)" % timeout


def write_src(name, src):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    return path


# ── Size sweeps ───────────────────────────────────────────────────────────
#
# Each source_fn(n) returns a COMPLETE module source (unlike
# pathological-nesting.py's shape_fn, which returns a bare expression to be
# spliced into a fixed template) since breadth here is often the whole file's
# shape, not one expression's.

def src_long_identifier(n):
    name = "x" + "a" * max(0, n - 1)
    return "module Fuzz exposing (%s)\n\n%s =\n    1\n" % (name, name)


def src_long_string(n):
    return 'module Fuzz exposing (x)\n\nx =\n    "%s"\n' % ("a" * n)


def src_long_comment(n):
    return "module Fuzz exposing (x)\n\n-- %s\nx =\n    1\n" % ("a" * n)


def src_wide_list(n):
    return "module Fuzz exposing (x)\n\nx =\n    [" + ", ".join(["1"] * n) + "]\n"


def src_wide_record(n):
    fields = ", ".join("f%d = 1" % i for i in range(n))
    return "module Fuzz exposing (x)\n\nx =\n    { " + fields + " }\n"


def src_wide_module(n):
    decls = "\n\n\n".join("x%d =\n    %d" % (i, i) for i in range(n))
    return "module Fuzz exposing (..)\n\n\n%s\n" % decls


def src_wide_comments_only(n):
    comments = "\n".join("-- comment number %d" % i for i in range(n))
    return "module Fuzz exposing (..)\n\n\n%s\n" % comments


SIZE_SHAPES = {
    "long-identifier": src_long_identifier,
    "long-string": src_long_string,
    "long-comment": src_long_comment,
    "wide-list": src_wide_list,
    "wide-record": src_wide_record,
    "wide-module": src_wide_module,
    "wide-comments-only": src_wide_comments_only,
}


def probe_size(shape_name, flag, source_fn, n, timeout, verbose):
    src = source_fn(n)
    path = os.path.join(OUT_DIR, "_probe.gren")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    bucket, out, err = run_app(flag, path, timeout)
    if verbose:
        print("  %-20s %-9s n=%-8d %s" % (shape_name, flag, n, bucket), file=sys.stderr)
    return bucket, src


def save_repro(shape_name, n, src):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%s_%d.gren" % (shape_name, n))
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    return path


def find_boundary(shape_name, flag, source_fn, start, factor, max_size, timeout, verbose):
    last_ok = 0
    n = start
    bucket = "ok"
    src = ""
    while True:
        bucket, src = probe_size(shape_name, flag, source_fn, n, timeout, verbose)
        if bucket != "ok":
            break
        last_ok = n
        if n >= max_size:
            return last_ok, None, None, None
        n = min(int(n * factor) + 1, max_size)

    lo, hi, fail_bucket = last_ok, n, bucket
    while hi - lo > 1:
        mid = (lo + hi) // 2
        b, _ = probe_size(shape_name, flag, source_fn, mid, timeout, verbose)
        if b == "ok":
            lo = mid
        else:
            hi = mid
            fail_bucket = b

    _, src = probe_size(shape_name, flag, source_fn, hi, timeout, verbose)
    return lo, hi, fail_bucket, src


# Same rationale as pathological-nesting.py: native crash thresholds jitter a
# little run to run, so require the parser boundary to clear the formatter's
# by a margin before calling it "format-stage" rather than "parse-stage".
STAGE_MARGIN = 1.15


def sweep_size_shape(shape_name, source_fn, start, factor, max_size, timeout, verbose):
    show_last_ok, show_boundary, show_bucket, show_src = find_boundary(
        shape_name, "--show", source_fn, start, factor, max_size, timeout, verbose)

    if show_boundary is None:
        return {"shape": shape_name, "status": "no-break",
                "last_ok": show_last_ok, "boundary": None,
                "bucket": None, "stage": None, "repro": None,
                "parse_boundary": None}

    repro_path = save_repro(shape_name, show_boundary, show_src)

    parse_last_ok, parse_boundary, _pb, _ps = find_boundary(
        shape_name, "--pre-ast", source_fn, show_boundary, factor, max_size, timeout, verbose)

    if parse_boundary is None:
        stage = "format"
    elif parse_boundary >= show_boundary * STAGE_MARGIN:
        stage = "format"
    else:
        stage = "parse"

    return {"shape": shape_name, "status": "break",
            "last_ok": show_last_ok, "boundary": show_boundary,
            "bucket": show_bucket, "stage": stage, "repro": repro_path,
            "parse_boundary": parse_boundary}


# ── One-shot scenarios ───────────────────────────────────────────────────

def scenario_empty_module():
    cases = {
        "empty-module-dotdot": "module Fuzz exposing (..)\n",
        "empty-module-blank-lines": "module Fuzz exposing (..)\n\n\n\n",
        "empty-module-leading-comment": "-- header note\nmodule Fuzz exposing (..)\n",
    }
    return run_oneshot_cases("empty-module", cases)


def scenario_all_comment_file():
    src = (
        "module Fuzz exposing (..)\n"
        "\n"
        "-- a line comment with nothing to attach to\n"
        "{- a block comment, also orphaned -}\n"
        "{-| a doc comment with no declaration below it -}\n"
        "-- trailing note\n"
    )
    return run_oneshot_cases("all-comment-file", {"all-comment-file": src})


# Every non-ASCII code point below is spelled as an explicit \u/\U escape
# (never a literal multi-byte character in this source file) so the exact
# code point is unambiguous regardless of editor/terminal normalization.
CAFE_LOWER = "caf\u00e9"                # e + LATIN SMALL LETTER E WITH ACUTE
CAFE_UPPER = "Caf\u00e9"
DELTA_LOWER = "\u03b4elta"              # GREEK SMALL LETTER DELTA
DELTA_UPPER = "\u0394elta"              # GREEK CAPITAL LETTER DELTA
PRIVET_LOWER = "\u043f\u0440\u0438\u0432\u0435\u0442_\u043c\u0438\u0440"  # "privet_mir" in Cyrillic
PRIVET_UPPER = "\u041f\u0440\u0438\u0432\u0435\u0442"                # "Privet"
COMBINING_ACUTE = "\u0301"              # Mn category -- not Ll/Lu/alphanumeric
ZWSP = "\u200b"                         # zero-width space -- not a letter either
HAN_WORDS = "\u6587\u5b57"              # Lo category -- has no case, so isn't Ll/Lu
EMOJI_GRINNING = "\U0001F600"
FAMILY_ZWJ = "\U0001F468\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466"  # man+ZWJ+woman+ZWJ+girl+ZWJ+boy
RTL_ARABIC = "\u0627\u0644\u0633\u0644\u0627\u0645"  # "as-salam"
DESERET = "\U00010437"                  # astral-plane (supplementary-plane) letter


def scenario_unicode_identifiers():
    # Leading chars are checked against \p{Ll} (lowercase var/field names) or
    # \p{Lu} (uppercase type/constructor names); inner chars additionally
    # allow Char.isAlphaNum and '_'. Combining marks (category Mn) match
    # neither, so they should stop the chomp, not crash it.
    cases = {
        "latin1-lower": "module Fuzz exposing (%s)\n\n%s =\n    1\n" % (CAFE_LOWER, CAFE_LOWER),
        "latin1-upper": "module Fuzz exposing (%s)\n\ntype %s = %s\n" % (CAFE_UPPER, CAFE_UPPER, CAFE_UPPER),
        "greek-lower": "module Fuzz exposing (%s)\n\n%s =\n    1\n" % (DELTA_LOWER, DELTA_LOWER),
        "greek-upper-ctor": "module Fuzz exposing (%s)\n\ntype %s = %s\n" % (DELTA_UPPER, DELTA_UPPER, DELTA_UPPER),
        "cyrillic-lower": "module Fuzz exposing (%s)\n\n%s =\n    1\n" % (PRIVET_LOWER, PRIVET_LOWER),
        "cyrillic-upper-ctor": "module Fuzz exposing (%s)\n\ntype %s = %s\n" % (PRIVET_UPPER, PRIVET_UPPER, PRIVET_UPPER),
        "combining-mark-inner": (
            "module Fuzz exposing (x)\n\nx =\n    let\n        e%sclair =\n"
            "            1\n    in\n    1\n" % COMBINING_ACUTE
        ),
        "han-leading-illegal": (
            "module Fuzz exposing (x)\n\nx =\n    let\n        %s =\n"
            "            1\n    in\n    1\n" % HAN_WORDS
        ),
        "emoji-leading-illegal": (
            "module Fuzz exposing (x)\n\nx =\n    let\n        %sabc =\n"
            "            1\n    in\n    1\n" % EMOJI_GRINNING
        ),
        "zwsp-inner-illegal": (
            "module Fuzz exposing (x)\n\nx =\n    let\n        ab%scd =\n"
            "            1\n    in\n    1\n" % ZWSP
        ),
    }
    return run_oneshot_cases("unicode-identifiers", cases)


def scenario_unicode_strings():
    def mk(content):
        escaped = content.replace("\\", "\\\\").replace('"', '\\"')
        return 'module Fuzz exposing (x)\n\nx =\n    "%s"\n' % escaped

    cases = {
        "emoji-zwj-family": mk("family %s emoji" % FAMILY_ZWJ),
        "combining-stack": mk("e%s%s%s stacked accents" % (COMBINING_ACUTE, COMBINING_ACUTE, COMBINING_ACUTE)),
        "rtl-mixed": mk("hello %s bye" % RTL_ARABIC),
        "astral-plane": mk("deseret %s char" % DESERET),
        "mixed-scripts": mk("%s %s %s %s" % (CAFE_LOWER, PRIVET_LOWER, HAN_WORDS, EMOJI_GRINNING)),
    }
    # Also a multiline (triple-quoted) variant of the richest case, since the
    # multiline-string path has its own escape/whitespace handling
    # (see MultilineStringControlChars.formatted.gren for the control-char
    # analogue this doesn't duplicate).
    cases["multiline-mixed-scripts"] = (
        'module Fuzz exposing (x)\n\nx =\n    """\n    %s %s %s %s\n    """\n'
        % (CAFE_LOWER, PRIVET_LOWER, HAN_WORDS, EMOJI_GRINNING)
    )
    return run_oneshot_cases("unicode-strings", cases)


def run_oneshot_cases(group, cases):
    results = []
    for case_name, src in cases.items():
        path = write_src("%s__%s.gren" % (group, case_name), src)
        bucket, out, err = run_app("--show", path, 15.0)
        results.append({"group": group, "case": case_name, "bucket": bucket,
                        "detail": (out + err).strip().splitlines()[:1]})
    return results


def scenario_crlf_corpus():
    """Convert every local canonical fixture to CRLF and require --show on the
    CRLF version to reproduce byte-identical output to the (already-canonical,
    already fixed-point) LF fixture. This is a stronger oracle than "does it
    crash": it also catches a CRLF byte leaking into output, or column/row
    math going wrong once "\\r\\n" replaces "\\n" as the line separator."""
    results = []
    fixtures = corpus_files(".formatted.gren")
    for fixture in fixtures:
        name = os.path.basename(fixture)
        with open(fixture, "rb") as f:
            canonical = f.read()
        crlf = canonical.replace(b"\n", b"\r\n")
        path = os.path.join(OUT_DIR, "_crlf_probe.gren")
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(crlf)
        try:
            r = subprocess.run(["node", APP, "--show", path],
                               capture_output=True, timeout=15.0)
            if r.returncode != 0:
                bucket = classify(r.stdout.decode("utf-8", "replace"),
                                  r.stderr.decode("utf-8", "replace"),
                                  r.returncode, False)
            elif r.stdout == canonical:
                bucket = "ok"
            else:
                bucket = "output-mismatch"
        except subprocess.TimeoutExpired:
            bucket = "timeout"
        if bucket != "ok":
            results.append({"group": "crlf-corpus", "case": name, "bucket": bucket,
                            "detail": []})
    if not results:
        results.append({"group": "crlf-corpus", "case": "*all %d fixtures*" % len(fixtures),
                        "bucket": "ok", "detail": []})
    return results


SCENARIOS = {
    "empty-module": scenario_empty_module,
    "all-comment-file": scenario_all_comment_file,
    "unicode-identifiers": scenario_unicode_identifiers,
    "unicode-strings": scenario_unicode_strings,
    "crlf-corpus": scenario_crlf_corpus,
}


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", action="append", choices=sorted(SIZE_SHAPES),
                    help="limit size sweeps to these (repeatable); default: all")
    ap.add_argument("--scenario", action="append", choices=sorted(SCENARIOS),
                    help="limit scenarios to these (repeatable); default: all")
    ap.add_argument("--sweep-only", action="store_true", help="skip one-shot scenarios")
    ap.add_argument("--scenario-only", action="store_true", help="skip size sweeps")
    ap.add_argument("--start", type=int, default=100, help="starting size (default 100)")
    ap.add_argument("--factor", type=float, default=3.0, help="growth factor per step (default 3.0)")
    ap.add_argument("--max-size", type=int, default=20000,
                    help="give up and report clean if no break by this size (default 20000)")
    ap.add_argument("--timeout", type=float, default=15.0, help="per-run timeout in seconds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(APP):
        print("app not found: %s\n(cd ../../gren-format && ./build.sh)" % APP,
              file=sys.stderr)
        return 2

    n_format_stage = 0
    n_scenario_findings = 0

    if not args.scenario_only:
        sizes = args.size or sorted(SIZE_SHAPES)
        print("size sweeps: %d shape(s), start=%d factor=%s max-size=%d timeout=%ss"
              % (len(sizes), args.start, args.factor, args.max_size, args.timeout))
        results = []
        for name in sizes:
            print("\n%s:" % name)
            r = sweep_size_shape(name, SIZE_SHAPES[name], args.start, args.factor,
                                 args.max_size, args.timeout, args.verbose)
            results.append(r)
            if r["status"] == "no-break":
                print("  clean up to size %d (no break found)" % r["last_ok"])
            else:
                pb = "no break by max-size" if r["parse_boundary"] is None else "breaks@%d" % r["parse_boundary"]
                print("  --show breaks at size %d (clean up to %d): %s, %s-stage"
                      % (r["boundary"], r["last_ok"], r["bucket"], r["stage"]))
                print("  --pre-ast (parse only): %s" % pb)
                print("  repro: %s" % r["repro"])

        print("\n%-20s %-10s %-12s %-14s %-12s %-10s"
              % ("shape", "status", "clean-upto", "bucket", "parse-upto", "stage"))
        for r in results:
            if r["status"] == "no-break":
                print("%-20s %-10s %-12d %-14s %-12s %-10s"
                      % (r["shape"], "clean", r["last_ok"], "-", "-", "-"))
            else:
                parse_upto = "inf" if r["parse_boundary"] is None else str(r["parse_boundary"])
                print("%-20s %-10s %-12d %-14s %-12s %-10s"
                      % (r["shape"], "BREAKS@%d" % r["boundary"], r["last_ok"], r["bucket"], parse_upto, r["stage"]))
                if r["stage"] == "format":
                    n_format_stage += 1

    if not args.sweep_only:
        scenario_names = args.scenario or sorted(SCENARIOS)
        print("\nscenarios: %d group(s)" % len(scenario_names))
        for name in scenario_names:
            rs = SCENARIOS[name]()
            bad = [r for r in rs if r["bucket"] not in ("ok", "parse-reject")]
            status = "PASS" if not bad else "FAIL"
            print("  %-20s %s (%d case(s))" % (name, status, len(rs)))
            for r in rs:
                marker = " " if r["bucket"] in ("ok", "parse-reject") else "!"
                print("   %s %-30s %s" % (marker, r["case"], r["bucket"]))
                if args.verbose and r["detail"]:
                    print("       %s" % r["detail"][0])
            n_scenario_findings += len(bad)

    print()
    if n_format_stage or n_scenario_findings:
        print("FAIL: %d size-sweep format-stage break(s), %d scenario finding(s) — "
              "see repros in %s/" % (n_format_stage, n_scenario_findings, OUT_DIR))
        return 1
    print("PASS: no format-stage breaks, no scenario findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
