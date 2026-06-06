"""Proxy rotation adapter implementing the ProxySelectorPort protocol.

This module provides :class:`ProxyRotator`, a weighted, health-aware proxy
selection strategy that favours proxies with higher ``weight × health_score``
products.  Unhealthy or dead proxies are automatically excluded from
selection.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Set

from ...domain.models import ProxyNode

# Minimum health score for a proxy to be considered healthy.
_HEALTH_THRESHOLD: float = 0.3


class ProxyRotator:
    """Health-aware weighted proxy selector.

    Maintains a pool of :class:`ProxyNode` instances, filtering out dead
    and unhealthy nodes before performing a weighted random selection.
    Thread safety is ensured via an ``asyncio.Lock``.

    Attributes:
        _proxies: Immutable tuple of configured proxy nodes.
        _dead: Set of proxy URLs that have been marked as dead.
        _lock: Asyncio lock for thread-safe selection.
        _logger: Logger for diagnostic output.
    """

    __slots__ = ("_proxies", "_dead", "_lock", "_logger")

    def __init__(
        self,
        proxies: tuple[ProxyNode, ...],
        logger: logging.Logger,
    ) -> None:
        """Initialize the proxy rotator.

        Args:
            proxies: Tuple of proxy nodes to rotate through.  An empty
                tuple is allowed and will cause :meth:`select` to always
                return ``None``.
            logger: Logger instance for diagnostic output.
        """
        self._proxies = proxies
        self._dead: Set[str] = set()
        self._lock = asyncio.Lock()
        self._logger = logger

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _healthy(self) -> list[ProxyNode]:
        """Return the list of alive, healthy proxy candidates.

        A proxy is considered healthy when it is **not** in the dead set
        and its ``health_score`` exceeds the threshold
        (``_HEALTH_THRESHOLD``, default 0.3).

        Returns:
            List of proxy nodes eligible for selection.
        """
        return [
            p
            for p in self._proxies
            if p.url not in self._dead and p.health_score > _HEALTH_THRESHOLD
        ]

    @staticmethod
    def _weighted_choice(candidates: list[ProxyNode]) -> ProxyNode:
        """Select a proxy using weighted random sampling.

        The selection probability for each candidate is proportional to
        ``weight × health_score``, giving preference to healthier and
        higher-weighted proxies.

        Args:
            candidates: Non-empty list of healthy proxy nodes.

        Returns:
            The selected proxy node.

        Raises:
            ValueError: If *candidates* is empty.
        """
        if not candidates:
            raise ValueError("Cannot select from an empty candidate list.")

        weights = [p.weight * p.health_score for p in candidates]
        total = sum(weights)

        # When all weights collapse to zero, fall back to uniform selection.
        if total <= 0.0:
            return random.choice(candidates)

        # random.choices returns a list; we take the first (and only) element.
        return random.choices(candidates, weights=weights, k=1)[0]

    # ------------------------------------------------------------------
    #  ProxySelectorPort: select
    # ------------------------------------------------------------------

    async def select(self, attempt: int) -> ProxyNode | None:
        """Select a healthy proxy for the given attempt number.

        Thread-safe: uses an ``asyncio.Lock`` to guard against concurrent
        selections that could otherwise race on the dead-set.

        Args:
            attempt: Current attempt number (used for logging only).

        Returns:
            A :class:`ProxyNode` selected via weighted sampling, or
            ``None`` when no healthy proxies are available.
        """
        async with self._lock:
            candidates = self._healthy()

        if not candidates:
            self._logger.warning(
                "Attempt %d: no healthy proxies available "
                "(total=%d, dead=%d).",
                attempt,
                len(self._proxies),
                len(self._dead),
            )
            return None

        selected = self._weighted_choice(candidates)
        self._logger.debug(
            "Attempt %d: selected proxy %s (weight=%.2f, health=%.2f).",
            attempt,
            selected.url,
            selected.weight,
            selected.health_score,
        )
        return selected

    # ------------------------------------------------------------------
    #  ProxySelectorPort: mark_dead
    # ------------------------------------------------------------------

    def mark_dead(self, proxy: ProxyNode | None) -> None:
        """Mark a proxy as dead after persistent failure.

        Once marked, the proxy will be excluded from all future
        :meth:`select` calls.  This method is a no-op when *proxy*
        is ``None``.

        Args:
            proxy: The :class:`ProxyNode` to mark as dead, or ``None``.
        """
        if proxy is None:
            return

        if proxy.url in self._dead:
            self._logger.debug("Proxy %s is already marked as dead.", proxy.url)
            return

        self._dead.add(proxy.url)
        self._logger.info(
            "Marked proxy %s as dead (dead count: %d/%d).",
            proxy.url,
            len(self._dead),
            len(self._proxies),
        )
