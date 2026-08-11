#!/usr/bin/env python3
"""Non-vacuity check for the two oracle-6 buckets no live bug reaches today.

`rui-ast-mismatch` was proven against the real app (stash the fix, the four
known seeds all fire). These two are proven by driving the oracle with a
stubbed `run_app`, so the wiring — not just the predicate — is what is tested.
"""
import importlib.util, os, subprocess, sys, tempfile

spec = importlib.util.spec_from_file_location(
    "genrandom", "/home/gram/prj/gren-format/gren-format-lib/tests/gen-random.py")
G = importlib.util.module_from_spec(spec)
sys.modules["genrandom"] = G
spec.loader.exec_module(G)

SRC = "module M exposing (a)\n\nimport Foo\n\n\n-- k1\n-- k2\na =\n    0\n"


class Fake:
    def __init__(self, stdout):
        self.stdout, self.stderr, self.returncode = stdout, "", 0


def drive(outputs, src=SRC, formatted=None):
    """Run the oracle with `run_app` answering from `outputs` in order."""
    calls = iter(outputs)
    G.run_app = lambda args, **kw: Fake(next(calls))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "input.gren")
        open(path, "w").write(src)
        return G._check_remove_unused_imports(
            src, formatted if formatted is not None else src, path, tmp)


fails = 0

# 1. a clean transform: both passes agree, comments in order -> no finding
b, _ = drive([SRC, SRC])
print("clean transform            ->", b)
fails += b is not None

# 2. the second removal pass changes the file -> rui-not-fixpoint
b, d = drive([SRC, SRC.replace("-- k2\n", "")])
print("second pass differs        ->", b)
fails += b != "rui-not-fixpoint"

# 3. removal REORDERS two comments -> rui-comment-order
swapped = SRC.replace("-- k1\n-- k2\n", "-- k2\n-- k1\n")
b, d = drive([swapped, swapped])
print("comments swapped           ->", b)
fails += b != "rui-comment-order"

# 3b. a swap the SORT explains is legal (authored and formatted disagree)
b, _ = drive([SRC.replace("-- k1\n-- k2\n", "-- k2\n-- k1\n")] * 2,
             src=SRC, formatted=SRC.replace("-- k1\n-- k2\n", "-- k2\n-- k1\n"))
print("swap the sort explains     ->", b)
fails += b is not None

# 4. removal DUPLICATES a comment -> rui-comment-order
dup = SRC.replace("-- k2\n", "-- k2\n-- k2\n")
b, d = drive([dup, dup])
print("comment duplicated         ->", b)
fails += b != "rui-comment-order"

# 5. removal DELETES a comment -> allowed, no finding
gone = SRC.replace("-- k1\n", "")
b, _ = drive([gone, gone])
print("comment deleted (legal)    ->", b)
fails += b is not None

# 6. a module with no imports is skipped entirely (run_app must never be called)
G.run_app = lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "i.gren")
    noimp = "module M exposing (a)\n\n\na =\n    0\n"
    open(p, "w").write(noimp)
    b, _ = G._check_remove_unused_imports(noimp, noimp, p, tmp)
print("no imports -> skipped      ->", b)
fails += b is not None

print("\n%s" % ("FAILED %d check(s)" % fails if fails else "all 7 wiring checks pass"))
sys.exit(1 if fails else 0)
