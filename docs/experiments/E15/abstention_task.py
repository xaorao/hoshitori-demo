"""E15: can Inspect AI (MIT, pinned clone) carry our offline scorer harness? A deterministic rules arm is a solver; planted abstention
scenarios are samples; a custom scorer compares action classes. One sample raises inside the solver to see how errors reach the metrics."""
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, accuracy, scorer, CORRECT, INCORRECT
from inspect_ai.solver import Generate, Solver, TaskState, solver

SCENARIOS = [
    # (id, evidence_complete, edge, expected)
    ("s01", True, 0.05, "CALL"), ("s02", True, 0.001, "NO_CALL"), ("s03", False, 0.05, "FAILED_EVIDENCE"), ("s04", True, 0.04, "CALL"),
    ("s05", True, 0.0, "NO_CALL"), ("s06", False, 0.0, "FAILED_EVIDENCE"), ("s07", True, 0.06, "CALL"), ("s08", True, 0.002, "NO_CALL"),
    ("s09", True, 0.03, "CALL"), ("s10", False, 0.04, "FAILED_EVIDENCE"), ("s11", True, 0.05, "CALL"), ("s12", True, 0.0005, "NO_CALL"),
]

@solver
def rules_arm(min_edge: float = 0.01, boom_on: str = "s09") -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        md = state.metadata
        if state.sample_id == boom_on:
            raise RuntimeError("planted operational failure in the solver")
        if not md["evidence_complete"]:
            action = "FAILED_EVIDENCE"
        elif md["edge"] >= min_edge:
            action = "CALL"
        else:
            action = "NO_CALL"
        state.output.completion = action
        return state
    return solve

@scorer(metrics=[accuracy()])
def action_match():
    async def score(state: TaskState, target: Target) -> Score:
        return Score(value=CORRECT if state.output.completion.strip() == target.text else INCORRECT, answer=state.output.completion,
                     metadata={"agent_version": "rules-v1", "cycle_id": state.metadata.get("cycle_id")})
    return score

@task
def abstention():
    return Task(dataset=[Sample(id=i, input=f"scenario {i}", target=exp, metadata={"evidence_complete": ev, "edge": edge, "cycle_id": "c-" + i}) for i, ev, edge, exp in SCENARIOS],
                solver=rules_arm(), scorer=action_match())
