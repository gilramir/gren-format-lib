# The Gren Formatter Library

This package is the library behind
[`gren-format`](https://github.com/gilramir/gren-format): given the parser's
output for a Gren source file, it produces a formatted version of that
file — consistent spacing, consistent indentation, comments and blank
lines kept where they belong, while also honoring the single-line or multi-line
formatting choice of the author of the code.

How to call it, with a worked example, is in the docs for the **Formatter**
module — the one function you need. Everything else — a formatted example,
the formatting philosophy, the seven comment rules, known limitations,
performance, and the comparison with `elm-format` — is in **[the documentation
index](https://github.com/gilramir/gren-format-lib/blob/main/docs/index.md)**, which also
links to every companion document.

---

## Exposed modules

Most of what this package exposes is not the formatting API — it is there so
that `gren-format` can look inside a format, and so that the test suite can
reach the pieces it checks.

| Reason | Module | What it provides |
|---|---|---|
| **Formatter API** | `Formatter` | The whole API: `prettyPrint ast context` → formatted source, or an error. |
| **Formatter API** | `Formatter.Logical` | Stage one alone — parsed module + comments → Logical Printing Tree. |
| **Formatter API** | `Formatter.Render` | Stage two alone — Logical Printing Tree → the final string. |
| **Inspection** | `Formatter.Logical.LPTJson` | The Logical Printing Tree as JSON (`--lpt`). |
| **Inspection** | `Compiler.Ast.Source.Json` | A parsed module as JSON (`--pre-ast`, `--post-ast`). |
| **Inspection** | `Compiler.Parse.Context.Json` | The parse context — every comment and its position — as JSON (`--pre-context`, `--post-context`). |
| **Inspection** | `Formatter.Audit.DecisionTrace` | The layout decisions a format took, and which ones moved between two formats (`--decisions`). |
| **Inspection** | `Formatter.Audit.PredicateAgreement` | Checks the "will this break?" predicates against what the renderer actually emits (`--audit-predicates`). |
| **Verification** | `Compiler.Ast.Compare` | Position-independent comparison of two parsed modules — the proof that formatting did not change the code's meaning. |
| **Testing** | `Formatter.Logical.LiteralFormat` | String, char and hex literal escaping. Exposed only so the test suite can reach it; callers go through `Formatter.prettyPrint`. |
