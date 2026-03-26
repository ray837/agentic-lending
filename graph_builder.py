"""Dynamic LangGraph Builder — plan to runtime graph."""
from __future__ import annotations
import json, traceback
from datetime import datetime
from typing import Any, TypedDict
from langgraph.graph import StateGraph, END
from flow_planner import ExecutionPlan, PlannedStep, StepType
from flow_registry import get_step_metadata


class FlowState(TypedDict, total=False):
    partner: str; plan_id: str; current_step: str; status: str
    started_at: str; completed_at: str; data: dict[str, Any]
    execution_log: list[dict[str, Any]]; errors: list[dict[str, str]]


def make_entity_node(step: PlannedStep, mcp_tools: list = None):
    step_meta = get_step_metadata(step.name)
    def entity_node(state: FlowState) -> dict:
        data, log, errors = state.get("data", {}), state.get("execution_log", []), state.get("errors", [])
        entry = {"step": step.name, "type": "entity", "started_at": datetime.now().isoformat(), "status": "running"}
        try:
            if step_meta:
                missing = [i for i in step_meta.required_inputs if i not in data]
                if missing: entry["warnings"] = f"Missing: {missing}"
            if mcp_tools:
                match = next((t for t in mcp_tools if t.name == step.name), None)
                if match:
                    result = match.invoke(data)
                    result = json.loads(result) if isinstance(result, str) else result
                else:
                    result = {"status": "success", "message": f"{step.name} (no MCP tool match)"}
            else:
                result = {"status": "success", "message": f"{step.name} executed"}
            if step_meta:
                for k in step_meta.outputs: data[k] = result.get(k, f"{k}_value")
            entry.update(status="success", completed_at=datetime.now().isoformat(), result=result)
        except Exception as e:
            entry.update(status="failed", error=str(e))
            errors.append({"step": step.name, "error": str(e)})
        log.append(entry)
        return {"current_step": step.name, "data": data, "execution_log": log, "errors": errors}
    entity_node.__name__ = f"node_{step.name}"
    return entity_node


def make_data_change_node(step: PlannedStep):
    def data_change_node(state: FlowState) -> dict:
        data, log = state.get("data", {}), state.get("execution_log", [])
        changes = {}
        for f, v in step.data_changes.items():
            old = data.get(f); data[f] = v if v is not None else f"__PENDING_{f}__"
            changes[f] = {"old": old, "new": data[f]}
        log.append({"step": step.name, "type": "data_change", "changes": changes,
            "timestamp": datetime.now().isoformat(), "status": "success"})
        return {"current_step": step.name, "data": data, "execution_log": log}
    data_change_node.__name__ = f"node_{step.name.replace('(','_').replace(')','_')}"
    return data_change_node


class FlowExecutor:
    def __init__(self, mcp_tools: list = None):
        self.mcp_tools = mcp_tools

    def execute(self, plan: ExecutionPlan, initial_data: dict = None) -> dict:
        graph = StateGraph(FlowState)
        names = []
        for i, step in enumerate(plan.planned_flow):
            name = f"{i}_{step.name.replace('(','_').replace(')','')}"
            names.append(name)
            if step.step_type == StepType.DATA_CHANGE:
                graph.add_node(name, make_data_change_node(step))
            else:
                graph.add_node(name, make_entity_node(step, self.mcp_tools))
        if names:
            graph.set_entry_point(names[0])
            for j in range(len(names)-1): graph.add_edge(names[j], names[j+1])
            graph.add_edge(names[-1], END)
        compiled = graph.compile()
        state: FlowState = {
            "partner": plan.partner, "plan_id": f"{plan.partner}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "current_step": "", "status": "running", "started_at": datetime.now().isoformat(),
            "completed_at": "", "data": initial_data or {}, "execution_log": [], "errors": [],
        }
        final = compiled.invoke(state)
        final["status"] = "failed" if final.get("errors") else "completed"
        final["completed_at"] = datetime.now().isoformat()
        return final
