"""Arm P of the head-to-head. The reader itself moved to `review/plain.py` (2026-11-06) so that
production code (`review.layered`) does not import from the eval folder; this module re-exports it
so every existing import and the pre-registered prompt stay exactly as they were."""

from review.plain import *  # noqa: F403
from review.plain import _members, _pieces, _shown  # noqa: F401
