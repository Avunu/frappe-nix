# An app that predates PEP 621. It belongs in apps/ and on PYTHONPATH, but it
# cannot be a uv workspace member — the same policy compute_membership applies
# in lib/sh/apps.sh.
from setuptools import setup

setup(name="legacy", version="0.0.0")
