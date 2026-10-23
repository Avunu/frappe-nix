"""The bench's dependencies, in Python: the node_modules
refresh, the fallback lock generator, the bench patch record and ``bench setup
requirements``.

Each was bash in lib/node-modules.nix, lib/node-locks.nix, lib/bench-patches.nix
and lib/scripts.nix; tests/node-modules.sh, node-locks.sh, bench-patches.sh and
setup-requirements.sh are their acceptance suite, run unchanged against these.
The node_modules refresh and the patch record run on every shell entry, so they
import nothing they do not use and take no click.
"""
