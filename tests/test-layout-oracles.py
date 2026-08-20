#!/usr/bin/env python3
"""Non-vacuity check for gen-random.py's three LAYOUT oracles (7-9).

Each of them watches a bug class that every round-trip oracle is blind to, so
"it reports nothing" is the expected reading and is indistinguishable from "it
is dead". This replays each oracle against the output the formatter ACTUALLY
produced before the fix it exists to have caught -- read out of git, not
hand-written -- and requires it to fire there and to be silent on today's.

    ./test-layout-oracles.py        # exit 0 = all three still see their bug

Run it whenever a layout oracle's predicate or exclusion list is touched. A
widened exclusion that quietly covers the original bug fails here, which is the
whole point.
"""
import importlib.util, os, subprocess, sys, tempfile, types
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gr", os.path.join(HERE, "gen-random.py"))
gr = importlib.util.module_from_spec(spec); spec.loader.exec_module(gr)
LIB = os.path.dirname(HERE)
def git_show(rev, path):
    return subprocess.run(["git","-C",LIB,"show","%s:%s"%(rev,path)],
                          capture_output=True, text=True, check=True).stdout

FX = "tests/testfiles/BinopsAndPipelines/"
fails = 0
def want(label, cond):
    global fails
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: fails += 1

print("Oracle 7  stranded-operator  (299c912)")
pre  = git_show("299c912^", FX+"PipelineBareLambdaOperand.formatted.gren")
mid  = git_show("299c912",  FX+"PipelineBareLambdaOperand.formatted.gren")
post = git_show("HEAD",     FX+"PipelineBareLambdaOperand.formatted.gren")
b, d = gr._check_stranded_operator(pre)
want("fires on the pre-fix output: %s" % (d or {}).get("msg"), b == "stranded-operator")
# At 299c912 itself the fixture STILL held one dangle -- the bare `if`/`when`/
# `let` operand that commit left open on purpose (`bareIfStillDangles`), closed
# later by e1322bd. The oracle finds that one too: same shape, same bug.
b, d = gr._check_stranded_operator(mid)
want("still fires at 299c912, on the dangle it left open: %s" % (d or {}).get("msg"),
     b == "stranded-operator")
b, _ = gr._check_stranded_operator(post)
want("silent at HEAD, once e1322bd closed that one too", b is None)

print("Oracle 8  spontaneous-break  (5fff8cc)")
pre = git_show("5fff8cc^", FX+"PipelineMixedOps.formatted.gren")
dirty = git_show("5fff8cc^", FX+"PipelineMixedOps.dirty.gren")
assert "oneLine y =\n    y |> f |> g <| 0" in dirty, "the author wrote it flat"
body = gr.Binop([gr.Var("y"), gr.Var("f"), gr.Var("g"), gr.Int(0)],
                ["|>", "|>", "<|"], broken=False)
m = types.SimpleNamespace(decls=[gr.Decl("oneLine", [gr.PVar("y")], body)])
b, d = gr._check_spontaneous_break(m, pre)
want("fires on the pre-fix output: %s" % (d or {}).get("msg"), b == "spontaneous-break")
post = git_show("5fff8cc", FX+"PipelineMixedOps.formatted.gren")
b, _ = gr._check_spontaneous_break(m, post)
want("silent on the post-fix output", b is None)

print("Oracle 9  break-ignored  (afa9ea5)")
# afa9ea5's own trigger is fixed, so the differential is shown against a break
# the formatter is KNOWN not to honour and which the shipped site list excludes
# for that reason: a one-item array. Widening the list makes it fire, which is
# what proves the machinery -- and narrowing it back makes it silent, which is
# what proves the exclusion is doing the work rather than the oracle being dead.
mod = gr.generate(3, 3, 0.0)
mod.decls = [d for d in mod.decls if isinstance(d, gr.Decl)][:1]
mod.decls[0].body = gr.Array([gr.Int(1)], broken=True)
mod.decls[0].sig = None
src = gr.emit_module(mod)
assert "[ 1\n" in src, src
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "M.gren")
    open(p, "w").write(src)
    r = subprocess.run(["node", gr.APP, "--show", p], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    formatted = r.stdout
    b, _ = gr._check_break_ignored(mod, src, formatted, tmp)
    want("silent with the shipped site list (Array needs 2+ items)", b is None)
    gr._BREAK_FLAG_MIN["Array"] = 1
    b, d = gr._check_break_ignored(mod, src, formatted, tmp)
    want("fires once a 1-item array is admitted: %s" % (d or {}).get("msg"),
         b == "break-ignored")
    gr._BREAK_FLAG_MIN["Array"] = 2
sys.exit(1 if fails else 0)
