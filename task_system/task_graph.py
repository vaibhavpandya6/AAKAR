"""Task dependency graph — DAG validation, readiness detection, and parallel scheduling."""

from collections import defaultdict, deque
from typing import Any, Dict, List, Set

import structlog

logger = structlog.get_logger()


class InvalidDAGError(Exception):
    """Raised when the task graph is malformed.

    Attributes:
        reason: Human-readable description of the problem.
        details: Additional structured context (e.g. conflicting IDs, cycle path).
    """

    def __init__(self, reason: str, details: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details

    def __str__(self) -> str:
        base = self.reason
        if self.details:
            base += f" | details: {self.details}"
        return base


class TaskGraph:
    """Validates and schedules a set of tasks modelled as a directed acyclic graph.

    Each task may declare a ``depends_on`` list of task IDs that must complete
    before it can start.  The graph is validated once and the results are used to:
      - detect which tasks are immediately runnable
      - produce wave-based parallel execution batches
    """

    # ──────────────────────────────────────────────────────────────────────
    # Build & validate
    # ──────────────────────────────────────────────────────────────────────

    def build_from_dag(self, tasks: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Build and validate the dependency graph from a task list.

        The graph is represented as an adjacency list:
        ``{task_id: [ids_that_depend_on_it]}``

        Validation steps:
        1. All task IDs are unique.
        2. Every dependency ID exists in the task set.
        3. No directed cycles (Kahn's topological sort).

        Args:
            tasks: List of task dicts. Each may contain a ``depends_on`` list
                   of string task IDs.

        Returns:
            Adjacency list ``{task_id: [downstream_task_ids]}``.

        Raises:
            InvalidDAGError: On duplicate IDs, unknown dependencies, or cycles.
        """
        if not tasks:
            return {}

        # ── 1. Collect IDs ─────────────────────────────────────────────────
        id_to_task: Dict[str, Dict[str, Any]] = {}
        duplicates: List[str] = []

        for task in tasks:
            tid = str(task.get("id", ""))
            if not tid:
                raise InvalidDAGError(
                    "Task is missing required 'id' field",
                    details=task,
                )
            if tid in id_to_task:
                duplicates.append(tid)
            id_to_task[tid] = task

        if duplicates:
            raise InvalidDAGError(
                "Duplicate task IDs detected",
                details={"duplicate_ids": duplicates},
            )

        known_ids: Set[str] = set(id_to_task.keys())

        # ── 2. Build adjacency list + in-degree map ────────────────────────
        # adjacency[A] = [B, C] means A must complete before B and C start
        adjacency: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = {tid: 0 for tid in known_ids}

        unknown_deps: List[Dict[str, str]] = []

        for task in tasks:
            tid = str(task["id"])
            deps: List[str] = [str(d) for d in (task.get("depends_on") or [])]

            for dep_id in deps:
                if dep_id not in known_ids:
                    unknown_deps.append({"task_id": tid, "missing_dependency": dep_id})
                    continue
                adjacency[dep_id].append(tid)
                in_degree[tid] += 1

        if unknown_deps:
            raise InvalidDAGError(
                "Tasks reference dependency IDs that do not exist in the task list",
                details={"unknown_dependencies": unknown_deps},
            )

        # ── 3. Kahn's algorithm — cycle detection ──────────────────────────
        # Initialise queue with zero-in-degree nodes
        queue: deque[str] = deque(
            tid for tid, deg in in_degree.items() if deg == 0
        )
        processed_count = 0

        while queue:
            node = queue.popleft()
            processed_count += 1
            for downstream in adjacency.get(node, []):
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        if processed_count != len(known_ids):
            # Not all nodes were processed → cycle exists
            nodes_in_cycle = [
                tid for tid, deg in in_degree.items() if deg > 0
            ]
            raise InvalidDAGError(
                "Cycle detected in task dependency graph — tasks cannot form a loop",
                details={
                    "nodes_in_cycle": nodes_in_cycle,
                    "hint": (
                        "Remove the dependency that creates the loop. "
                        f"Affected task IDs: {nodes_in_cycle}"
                    ),
                },
            )

        logger.info(
            "task_dag_built",
            total_tasks=len(tasks),
            total_dependencies=sum(in_degree.values()),  # 0 after Kahn — use original
        )

        # Return plain dict (not defaultdict) for clean serialisation
        return dict(adjacency)

    # ──────────────────────────────────────────────────────────────────────
    # Readiness
    # ──────────────────────────────────────────────────────────────────────

    def get_ready_tasks(
        self,
        tasks: List[Dict[str, Any]],
        completed_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Return tasks whose dependencies are all satisfied.

        A task is ready when:
        - It has no ``depends_on`` entries, OR
        - All of its ``depends_on`` IDs appear in ``completed_ids``.

        Already-completed tasks are excluded from the result.

        Args:
            tasks: Full task list.
            completed_ids: IDs of tasks that have finished successfully.

        Returns:
            List of task dicts that can start immediately.
        """
        completed_set = set(str(cid) for cid in completed_ids)
        ready: List[Dict[str, Any]] = []

        for task in tasks:
            tid = str(task.get("id", ""))

            # Skip already completed
            if tid in completed_set:
                continue

            deps = [str(d) for d in (task.get("depends_on") or [])]

            if all(dep in completed_set for dep in deps):
                ready.append(task)

        logger.debug(
            "ready_tasks_computed",
            ready_count=len(ready),
            completed_count=len(completed_ids),
            total_tasks=len(tasks),
        )
        return ready

    # ──────────────────────────────────────────────────────────────────────
    # Execution order — wave batching
    # ──────────────────────────────────────────────────────────────────────

    def get_execution_order(
        self, tasks: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """Group tasks into parallel execution waves.

        Within each batch, tasks have no inter-dependencies and can run
        concurrently.  Later batches depend on all earlier ones completing.

        The algorithm is a level-order (BFS) traversal of the DAG:
        - Wave 0: tasks with no dependencies
        - Wave 1: tasks whose deps are all in wave 0
        - Wave N: tasks whose deps are all in waves 0..N-1

        Args:
            tasks: Full task list.

        Returns:
            Ordered list of batches.  Each batch is a list of task dicts
            that can be executed in parallel.

        Raises:
            InvalidDAGError: If the task list contains cycles or unknown deps.
        """
        if not tasks:
            return []

        # Full validation first
        self.build_from_dag(tasks)

        id_to_task: Dict[str, Dict[str, Any]] = {
            str(t["id"]): t for t in tasks
        }
        in_degree: Dict[str, int] = {tid: 0 for tid in id_to_task}
        adjacency: Dict[str, List[str]] = defaultdict(list)

        for task in tasks:
            tid = str(task["id"])
            for dep_id in [str(d) for d in (task.get("depends_on") or [])]:
                if dep_id in id_to_task:
                    adjacency[dep_id].append(tid)
                    in_degree[tid] += 1

        # BFS level order
        batches: List[List[Dict[str, Any]]] = []
        current_wave = [tid for tid, deg in in_degree.items() if deg == 0]

        while current_wave:
            batch = [id_to_task[tid] for tid in current_wave]
            batches.append(batch)

            next_wave: List[str] = []
            for tid in current_wave:
                for downstream in adjacency.get(tid, []):
                    in_degree[downstream] -= 1
                    if in_degree[downstream] == 0:
                        next_wave.append(downstream)

            current_wave = next_wave

        logger.info(
            "execution_order_computed",
            total_tasks=len(tasks),
            wave_count=len(batches),
            wave_sizes=[len(b) for b in batches],
        )
        return batches
