"""Flow Planner — LLM-driven, no regex."""
from __future__ import annotations
import json, re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from flow_registry import PARTNER_FLOWS, STEP_CATALOG

class StepType(str, Enum):
    ENTITY = "entity"
    DATA_CHANGE = "data_change"

@dataclass
class PlannedStep:
    name: str
    step_type: StepType = StepType.ENTITY
    data_changes: dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> dict:
        d = {"name": self.name, "type": self.step_type.value}
        if self.data_changes: d["data_changes"] = self.data_changes
        return d

@dataclass
class ExecutionPlan:
    partner: str
    original_flow: list[str]
    planned_flow: list[PlannedStep]
    modifications: list[str]
    user_query: str = ""
    is_flow_request: bool = True
    def to_dict(self) -> dict:
        return {"partner": self.partner, "original_flow": self.original_flow,
                "planned_flow": [s.to_dict() for s in self.planned_flow],
                "modifications": self.modifications, "is_flow_request": self.is_flow_request}
    def get_step_names(self) -> list[str]:
        return [s.name for s in self.planned_flow]
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

def build_planner_system_prompt() -> str:
    partners = "\n".join(f"  {p}: {' -> '.join(s)}" for p, s in PARTNER_FLOWS.items())
    steps = "\n".join(f"  - {n}: {m.description} | needs: {m.required_inputs} | produces: {m.outputs}" for n, m in STEP_CATALOG.items())
    return f"""You are a Flow Planner Agent for a banking system.
Analyze user messages and produce a JSON execution plan.

## Partners
{partners}

## Valid Steps
{steps}

## Task
1. Is this a flow request? YES if partner + flow intent. NO for general queries.
2. If YES: identify partner, start from default, apply modifications (reorder, data_change, add, remove). Understand casual language.
3. Output ONLY JSON:

Flow: {{"is_flow_request": true, "partner": "CRED", "original_flow": ["pan","kyc","loanonboarding"], "planned_flow": [{{"name":"pan","type":"entity"}},{{"name":"change(loanid)","type":"data_change","data_changes":{{"loanid":null}}}},{{"name":"loanonboarding","type":"entity"}}], "modifications": ["Inserted change(loanid) before loanonboarding"]}}

Not flow: {{"is_flow_request": false, "partner": null, "original_flow": [], "planned_flow": [], "modifications": []}}

Rules: lowercase step names exactly as listed. data_change name: change(fieldname). value null if not specified. ONLY JSON, no markdown."""

class LLMFlowPlanner:
    def __init__(self, llm):
        self.llm = llm
        self.system_prompt = build_planner_system_prompt()
    async def plan_async(self, user_query: str) -> ExecutionPlan:
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = await self.llm.ainvoke([SystemMessage(content=self.system_prompt), HumanMessage(content=user_query)])
        return self._parse(resp.content, user_query)
    def plan_sync(self, user_query: str) -> ExecutionPlan:
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = self.llm.invoke([SystemMessage(content=self.system_prompt), HumanMessage(content=user_query)])
        return self._parse(resp.content, user_query)
    def _parse(self, raw: str, q: str) -> ExecutionPlan:
        c = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        c = re.sub(r"\s*```$", "", c).strip()
        m = re.search(r"\{[\s\S]*\}", c)
        if m: c = m.group(0)
        try: data = json.loads(c)
        except json.JSONDecodeError:
            return ExecutionPlan(partner="", original_flow=[], planned_flow=[],
                modifications=["ERROR: parse failed"], user_query=q, is_flow_request=False)
        if not data.get("is_flow_request"):
            return ExecutionPlan(partner="", original_flow=[], planned_flow=[],
                modifications=[], user_query=q, is_flow_request=False)
        return ExecutionPlan(
            partner=(data.get("partner") or "").upper(),
            original_flow=data.get("original_flow", []),
            planned_flow=[PlannedStep(name=s["name"], step_type=StepType(s.get("type","entity")),
                data_changes=s.get("data_changes",{})) for s in data.get("planned_flow",[])],
            modifications=data.get("modifications",[]), user_query=q, is_flow_request=True)
