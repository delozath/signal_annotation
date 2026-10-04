"""Viewport-only min/max envelope. Never used as annotation data."""
import numpy as np
from ..domain import Channel


def visible_samples(channel: Channel, start: float, end: float, budget: int = 4000):
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        return np.array([]), np.array([])
    lo = max(0, int(np.floor(start * channel.sample_rate)) - 1)
    hi = min(len(channel.values), int(np.ceil(end * channel.sample_rate)) + 2)
    if lo >= hi:
        return np.array([]), np.array([])
    values = np.asarray(channel.values)
    if hi - lo <= budget:
        indices = np.arange(lo, hi)
    else:
        indices = [lo, hi - 1]
        # Per bucket: retain extrema and a missing-data marker, ordered by time.
        edges = np.linspace(lo, hi, max(2, budget // 3), dtype=int)
        for a, b in zip(edges[:-1], edges[1:]):
            chunk = values[a:b]
            finite = np.flatnonzero(np.isfinite(chunk))
            if len(finite):
                indices.extend([a + finite[np.argmin(chunk[finite])], a + finite[np.argmax(chunk[finite])]])
            missing = np.flatnonzero(~np.isfinite(chunk))
            if len(missing):
                indices.append(a + missing[0])
        indices = np.unique(indices)
    return indices / channel.sample_rate, values[indices]
