"""
Flow Executor — Step-by-Step with Pause/Resume
═══════════════════════════════════════════════
Executes a plan one step at a time.
When it hits a data_change node with a null value, it PAUSES
and returns a "needs_input" status so BOB can ask the user.
The user provides the value, BOB calls resume_flow, and
execution continues from where it left off.

State is stored in-memory keyed by session_id.
"""

import json
from datetime import datetime
from typing import Any
from flow_planner import ExecutionPlan, PlannedStep, StepType
from flow_registry import get_step_metadata


class FlowSession:
    """Tracks one execution session's state."""

    def __init__(self, plan: ExecutionPlan, initial_data: dict = None):
        self.plan = plan
        self.data: dict[str, Any] = initial_data or {}
        self.current_index: int = 0           # which step we're on
        self.execution_log: list[dict] = []
        self.errors: list[dict] = []
        self.status: str = "ready"            # ready | running | paused | completed | failed
        self.paused_at_step: str | None = None
        self.pending_fields: dict[str, str] = {}  # field_name -> description of what's needed
        self.created_at: str = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "partner": self.plan.partner,
            "status": self.status,
            "planned_flow": self.plan.get_step_names(),
            "current_step_index": self.current_index,
            "current_step": (
                self.plan.planned_flow[self.current_index].name
                if self.current_index < len(self.plan.planned_flow) else "done"
            ),
            "steps_total": len(self.plan.planned_flow),
            "steps_completed": len(self.execution_log),
            "paused_at_step": self.paused_at_step,
            "pending_fields": self.pending_fields,
            "execution_log": self.execution_log,
            "data": self.data,
            "errors": self.errors,
        }


class StepExecutor:
    """
    Executes a plan step by step. Pauses at data_change nodes
    that need user input.
    """

    def __init__(self, mcp_tools: list = None):
        self.mcp_tools = mcp_tools
        # In-memory session store. In production use Redis/DB.
        self.sessions: dict[str, FlowSession] = {}

    def create_session(
        self, session_id: str, plan: ExecutionPlan, initial_data: dict = None
    ) -> FlowSession:
        """Create a new execution session from a plan."""
        session = FlowSession(plan, initial_data)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> FlowSession | None:
        return self.sessions.get(session_id)

    def execute_until_pause(self, session_id: str) -> dict:
        """
        Run steps from current_index forward.
        Stops when:
          - A data_change node has null value → status="paused"
          - All steps complete → status="completed"
          - An error occurs → status="failed"
        Returns the session state dict.
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' not found"}

        session.status = "running"
        steps = session.plan.planned_flow

        while session.current_index < len(steps):
            step = steps[session.current_index]
            print("EXECUTING",step)

            if step.step_type == StepType.DATA_CHANGE:
                result = self._handle_data_change(session, step)
                if result == "paused":
                    return session.to_dict()
                # If data change was applied (value was provided), continue
            else:
                result = self._handle_entity(session, step)
                if result == "failed":
                    session.status = "failed"
                    return session.to_dict()

            session.current_index += 1

        # All steps done
        session.status = "completed"
        return session.to_dict()

    def resume_with_data(self, session_id: str, field_values: dict) -> dict:
        """
        Resume a paused session after user provides data change values.

        Args:
            session_id: The session to resume
            field_values: Dict of {field_name: value} from the user
        """
        session = self.sessions.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' not found"}

        if session.status != "paused":
            return {"error": f"Session is '{session.status}', not paused"}

        step = session.plan.planned_flow[session.current_index]

        # Apply the user-provided values
        changes_applied = {}
        for field_name, value in field_values.items():
            old = session.data.get(field_name)
            session.data[field_name] = value
            changes_applied[field_name] = {"old": old, "new": value}

        session.execution_log.append({
            "step": step.name,
            "type": "data_change",
            "changes": changes_applied,
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "source": "user_provided",
        })

        session.paused_at_step = None
        session.pending_fields = {}
        session.current_index += 1

        # Continue execution from next step
        return self.execute_until_pause(session_id)

    def _handle_data_change(self, session: FlowSession, step: PlannedStep) -> str:
        """
        Handle a data_change step.
        If value is null → pause and ask user.
        If value is provided → apply and continue.
        """
        needs_input = {}

        for field_name, value in step.data_changes.items():
            if value is None:
                # No value provided — need to pause and ask user
                needs_input[field_name] = (
                    f"Please provide a value for '{field_name}' "
                    f"(needed before the next step)"
                )
            else:
                # Value was provided in the plan — apply directly
                old = session.data.get(field_name)
                session.data[field_name] = value
                session.execution_log.append({
                    "step": step.name,
                    "type": "data_change",
                    "changes": {field_name: {"old": old, "new": value}},
                    "timestamp": datetime.now().isoformat(),
                    "status": "success",
                    "source": "plan_provided",
                })

        if needs_input:
            # Pause execution — BOB will ask the user
            session.status = "paused"
            session.paused_at_step = step.name
            session.pending_fields = needs_input
            return "paused"

        return "ok"

    def _handle_entity(self, session: FlowSession, step: PlannedStep) -> str:
        """Execute an entity step (API call)."""
        step_meta = get_step_metadata(step.name)

        entry = {
            "step": step.name,
            "type": "entity",
            "started_at": datetime.now().isoformat(),
            "status": "running",
        }

        try:
            # Check missing inputs
            if step_meta:
                missing = [i for i in step_meta.required_inputs if i not in session.data]
                if missing:
                    entry["warnings"] = f"Missing inputs: {missing}"

            # Try MCP tools
            if self.mcp_tools:
                match = next((t for t in self.mcp_tools if t.name == step.name), None)
                if match:
                    result = match.invoke(session.data)
                    if isinstance(result, str):
                        try:
                            result = json.loads(result)
                        except json.JSONDecodeError:
                            result = {"raw": result}
                else:
                    result = {"status": "success", "message": f"{step.name} (simulated)"}
            else:
                result = {"status": "success", "message": f"{step.name} executed"}

            # Merge outputs
            if step_meta:
                for k in step_meta.outputs:
                    session.data[k] = result.get(k, f"{k}_value")

            entry["status"] = "success"
            entry["completed_at"] = datetime.now().isoformat()
            entry["result"] = result

        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            session.errors.append({"step": step.name, "error": str(e)})
            session.execution_log.append(entry)
            return "failed"

        session.execution_log.append(entry)
        return "ok"
