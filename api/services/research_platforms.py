"""Compatibility import for the shared platform service."""

import sys
from . import platform_browser as _implementation

sys.modules[__name__] = _implementation
