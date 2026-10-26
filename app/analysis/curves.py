"""Bounded min/max sampling that preserves invalid runs as explicit breaks."""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import heapq

from replay_protocol import ChannelQuality
from .models import AnalysisError


@dataclass(frozen=True, slots=True)
class PlotPoint:
    time_s: float
    value: float | None
    quality: ChannelQuality


def window_points(channel, start, end):
    first = bisect_left(channel.times, start)
    last = bisect_right(channel.times, end)
    points = []
    if first == len(channel.times) or channel.times[first] != start:
        sample = channel.sample(start)
        points.append(PlotPoint(start, sample.value, sample.quality))
    for index in range(first, last):
        q = channel.quality[index]
        points.append(PlotPoint(channel.times[index], channel.values[index] if q.valid else None, q))
    if not points or points[-1].time_s != end:
        sample = channel.sample(end)
        points.append(PlotPoint(end, sample.value, sample.quality))
    if channel.max_gap_s is not None:
        for index in range(max(0, first - 1), min(len(channel.times) - 1, last)):
            left, right = channel.times[index:index + 2]
            if right - left <= channel.max_gap_s:
                continue
            lo, hi = max(start, left), min(end, right)
            if lo >= hi:
                continue
            middle = lo + (hi - lo) / 2
            if not lo < middle < hi:
                raise AnalysisError('CURVE_GAP_PRECISION', 'Time precision cannot represent a break; query each side separately')
            provenance = tuple(dict.fromkeys((*channel.quality[index].source_indices,
                                             *channel.quality[index + 1].source_indices)))
            points.append(PlotPoint(middle, None, ChannelQuality(origin='missing', valid=False,
                source_indices=provenance, method='analysis.max_gap.v1')))
    return sorted(points, key=lambda point: point.time_s)


def _min_max(points, quota):
    if len(points) <= quota:
        return points
    buckets = quota // 2
    selected = set()
    for bucket in range(buckets):
        first = bucket * len(points) // buckets
        end = (bucket + 1) * len(points) // buckets
        # First minimum and last maximum also retain the extent of constant runs.
        selected.add(min(range(first, end), key=lambda i: (points[i].value, i)))
        selected.add(max(range(first, end), key=lambda i: (points[i].value, i)))
    return [points[index] for index in sorted(selected)]


def reduce_points(points, budget):
    if len(points) <= budget:
        return points
    runs = []
    for point in points:
        valid = point.value is not None
        if not runs or (runs[-1][0].value is not None) != valid:
            runs.append([])
        runs[-1].append(point)
    quotas = [min(2, len(run)) if run[0].value is not None else 1 for run in runs]
    required = sum(quotas)
    if budget < required:
        raise AnalysisError('CURVE_BUDGET_TOO_SMALL', 'Increase max_points or narrow the range to retain peaks and breaks',
                            details={'required_points': required, 'max_points': budget})
    heap = [(-len(run) / quotas[index], index) for index, run in enumerate(runs)
            if run[0].value is not None and quotas[index] < len(run)]
    heapq.heapify(heap)
    remaining = budget - required
    while remaining and heap:
        _, index = heapq.heappop(heap)
        quotas[index] += 1
        remaining -= 1
        if quotas[index] < len(runs[index]):
            heapq.heappush(heap, (-len(runs[index]) / quotas[index], index))
    result = []
    for run, quota in zip(runs, quotas):
        result.extend(_min_max(run, quota) if run[0].value is not None else run[:1])
    return result
