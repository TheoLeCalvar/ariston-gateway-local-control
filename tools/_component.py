"""Make the integration's pure protocol modules importable from the tools.

The stdlib-only codecs (kura, menu, energy, tprog) live once, in
custom_components/ariston_local; each imports standalone (its `from . import kura`
falls back to a top-level `import kura`). Import this module, then `import kura`.

The directory is *appended*, not prepended: it also holds HA platform modules
named select.py and datetime.py that would otherwise shadow the stdlib.
"""
import os
import sys

COMPONENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "ariston_local")

if COMPONENT_DIR not in sys.path:
    sys.path.append(COMPONENT_DIR)
