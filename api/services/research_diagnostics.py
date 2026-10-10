"""Compatibility import for the shared platform service."""

import sys
from . import acquisition_diagnostics as _implementation

sys.modules[__name__] = _implementation
