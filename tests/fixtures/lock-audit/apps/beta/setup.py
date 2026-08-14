# A legacy app with no pyproject.toml: a workspace member the audit must skip
# rather than fail on.
from setuptools import setup
setup(name="beta")
