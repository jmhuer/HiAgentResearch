"""Compatibility shim — prefer hiagentresearch.src.core.config."""

from hiagentresearch.src.core.config import *  # noqa: F403
from hiagentresearch.src.core import config as _config_module

main = _config_module.main
