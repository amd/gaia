# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The old name of :mod:`gaiapy`, kept so code that imports ``gaia_agent`` still runs.

``import gaia_agent`` and ``import gaia_agent.anything`` return the very same
module objects as their ``gaiapy`` spellings, never a second copy, so a class
imported through either name is one class.
"""

import importlib
import importlib.abc
import importlib.util
import sys

import gaiapy

_OLD = __name__
_NEW = gaiapy.__name__


class _OldNameFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ``gaia_agent.x`` to the already-importable ``gaiapy.x``."""

    def __init__(self):
        self._real_specs = {}

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        real = importlib.util.find_spec(_NEW + fullname[len(_OLD) :])
        if real is None:
            return None
        # The real origin gives ``python -m gaia_agent.x`` a ``__file__``.
        return importlib.util.spec_from_loader(fullname, self, origin=real.origin)

    def create_module(self, spec):
        module = importlib.import_module(_NEW + spec.name[len(_OLD) :])
        self._real_specs[spec.name] = module.__spec__
        return module

    def exec_module(self, module):
        """Nothing to run, but undo the import system's swap of ``__spec__``."""
        module.__spec__ = self._real_specs.pop(module.__spec__.name)

    def get_code(self, fullname):
        """``python -m gaia_agent.x`` runs ``gaiapy.x``'s code as ``__main__``."""
        new = _NEW + fullname[len(_OLD) :]
        return importlib.util.find_spec(new).loader.get_code(new)


sys.meta_path.insert(0, _OldNameFinder())
sys.modules[_OLD] = gaiapy
