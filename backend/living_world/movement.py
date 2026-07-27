"""Programmatic room-graph travel planning."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from typing import Iterable


@dataclass(frozen=True)
class RouteEdge:
    from_room_id: int
    to_room_id: int
    travel_minutes: int = 10
    mode: str = "walk"
    service_id: str | None = None
    danger: int = 0

    def __post_init__(self) -> None:
        if self.travel_minutes <= 0:
            raise ValueError("travel_minutes must be positive")
        if self.danger < 0:
            raise ValueError("danger must be non-negative")


@dataclass(frozen=True)
class RoutePlan:
    room_ids: tuple[int, ...]
    edges: tuple[RouteEdge, ...]
    travel_minutes: int
    danger: int


def shortest_route(
    edges: Iterable[RouteEdge],
    *,
    from_room_id: int,
    to_room_id: int,
    avoid_danger_above: int | None = None,
) -> RoutePlan | None:
    """Dijkstra over authored room edges with deterministic tie-breaking."""
    if from_room_id == to_room_id:
        return RoutePlan((from_room_id,), (), 0, 0)

    adjacency: dict[int, list[RouteEdge]] = {}
    for edge in edges:
        if avoid_danger_above is not None and edge.danger > avoid_danger_above:
            continue
        adjacency.setdefault(edge.from_room_id, []).append(edge)
    for outgoing in adjacency.values():
        outgoing.sort(
            key=lambda edge: (
                edge.travel_minutes,
                edge.danger,
                edge.to_room_id,
                edge.mode,
                edge.service_id or "",
            )
        )

    queue: list[tuple[int, int, tuple[int, ...], int, tuple[RouteEdge, ...]]] = [
        (0, 0, (from_room_id,), from_room_id, ())
    ]
    best: dict[int, tuple[int, int, tuple[int, ...]]] = {}
    while queue:
        minutes, danger, rooms, room_id, used_edges = heapq.heappop(queue)
        score = (minutes, danger, rooms)
        if room_id in best and best[room_id] <= score:
            continue
        best[room_id] = score
        if room_id == to_room_id:
            return RoutePlan(rooms, used_edges, minutes, danger)
        for edge in adjacency.get(room_id, ()):
            heapq.heappush(
                queue,
                (
                    minutes + edge.travel_minutes,
                    danger + edge.danger,
                    (*rooms, edge.to_room_id),
                    edge.to_room_id,
                    (*used_edges, edge),
                ),
            )
    return None


def next_travel_event(
    plan: RoutePlan,
    *,
    current_room_id: int,
    depart_at: int,
) -> dict | None:
    """Create the next durable macro-movement event from a route plan."""
    for edge in plan.edges:
        if edge.from_room_id == current_room_id:
            return {
                "kind": "npc_arrive_room",
                "due_at": depart_at + edge.travel_minutes,
                "from_room_id": edge.from_room_id,
                "to_room_id": edge.to_room_id,
                "mode": edge.mode,
                "service_id": edge.service_id,
                "danger": edge.danger,
            }
    return None
