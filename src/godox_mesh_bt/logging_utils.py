"""Logging helpers for CLI verbosity flags.

Examples
--------
>>> verbosity_to_log_level(2)
10
"""

from __future__ import annotations

import logging


def verbosity_to_log_level(verbose: int) -> int:
    """Convert repeatable ``-v`` flags into a logging level.

    Parameters
    ----------
    verbose
        Verbosity count from argparse.

    Returns
    -------
    int
        Standard library logging level.

    Examples
    --------
    >>> verbosity_to_log_level(0)
    30
    >>> verbosity_to_log_level(1)
    20
    >>> verbosity_to_log_level(2)
    10
    """

    if verbose <= 0:
        return logging.WARNING
    if verbose == 1:
        return logging.INFO
    return logging.DEBUG


def configure_logging(verbose: int) -> int:
    """Configure root logging for a CLI invocation.

    Parameters
    ----------
    verbose
        Verbosity count from argparse.

    Returns
    -------
    int
        The logging level that was applied.

    Examples
    --------
    >>> configure_logging(0)
    30
    """

    level = verbosity_to_log_level(verbose)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    else:
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)
    return level
