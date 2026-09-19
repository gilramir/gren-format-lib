"""The `node` the gates run the built `gren-format` app with.

The app on the `geng` branch is built by the Geng fork, whose runtime needs
Node 22 or later (it calls `String.prototype.isWellFormed`), and the `node`
first on a plain PATH may be older. So the gates do not take `node` from the
PATH by default: `GREN_FORMAT_NODE` names one outright, and failing that the
devbox profile of the geng-lang checkout this repository is vendored into is
used when it is there (`../../../../.devbox/...` from `tests/`), and plain
`node` only when neither is.
"""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_GENG_LANG_NODE = os.path.join(
    _HERE, "..", "..", "..", "..", ".devbox", "nix", "profile", "default", "bin", "node"
)

NODE = (
    os.environ.get("GREN_FORMAT_NODE")
    or (os.path.abspath(_GENG_LANG_NODE) if os.path.exists(_GENG_LANG_NODE) else None)
    or "node"
)
