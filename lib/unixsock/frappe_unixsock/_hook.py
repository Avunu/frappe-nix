"""Post-import hooks.

Python has no public "run this once module X has been imported" API, so this is
the usual shape: a meta-path finder installed at the front of ``sys.meta_path``
that, for registered names only, resolves the *real* spec and swaps in a loader
which fires callbacks after ``exec_module`` returns.

Resolving the real spec re-enters ``sys.meta_path`` and would find this finder
again, hence the ``_resolving`` guard.

Callbacks run inside the import, so an exception propagates as an ImportError
on the module being loaded — which is what makes the fail-loud behaviour in
:func:`.._patch.require` work.

A byte-for-byte copy of ``frappe_devguard._hook``. Duplicated rather than
shared on purpose: this package ships to production and that one must not, so
a production interpreter cannot be made to import anything from devguard. It is
pure stdlib and knows nothing about Frappe, so the two copies cannot drift in a
way that matters; two registries coexist happily, since each inserts itself at
``sys.meta_path[0]`` and each ``find_spec`` re-enters ``importlib.util`` so the
other wraps the loader underneath.
"""

import importlib.util
import sys
import threading

_SENTINELS = ("_inner", "_fullname", "_registry")


class _Loader:
    """Delegating loader that fires callbacks once the module has executed."""

    def __init__(self, inner, fullname, registry):
        self._inner = inner
        self._fullname = fullname
        self._registry = registry

    def create_module(self, spec):
        create = getattr(self._inner, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module):
        self._inner.exec_module(module)
        self._registry.fire(self._fullname, module)

    def __getattr__(self, name):
        # Guard against recursing before __init__ has bound the delegate.
        if name in _SENTINELS:
            raise AttributeError(name)
        return getattr(self._inner, name)


class _Registry:
    def __init__(self):
        self._callbacks = {}
        self._resolving = set()
        self._lock = threading.RLock()

    def register(self, fullname, callback):
        if fullname in sys.modules:
            # Already imported — install() ran late; apply straight away.
            callback(sys.modules[fullname])
            return
        with self._lock:
            self._callbacks.setdefault(fullname, []).append(callback)

    def fire(self, fullname, module):
        with self._lock:
            callbacks = self._callbacks.pop(fullname, ())
        for callback in callbacks:
            callback(module)

    def find_spec(self, fullname, path=None, target=None):
        with self._lock:
            if fullname not in self._callbacks or fullname in self._resolving:
                return None
            self._resolving.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        except (AttributeError, ImportError, ValueError):
            return None
        finally:
            with self._lock:
                self._resolving.discard(fullname)

        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader, fullname, self)
        return spec


_REGISTRY = _Registry()


def on_import(fullname, callback):
    """Run ``callback(module)`` once ``fullname`` has finished importing."""
    if _REGISTRY not in sys.meta_path:
        sys.meta_path.insert(0, _REGISTRY)
    _REGISTRY.register(fullname, callback)
