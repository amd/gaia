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

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return importlib.import_module(_NEW + spec.name[len(_OLD) :])

    def exec_module(self, module):
        """Nothing to run: the module was executed under its real name."""


sys.meta_path.insert(0, _OldNameFinder())
sys.modules[_OLD] = gaiapy
