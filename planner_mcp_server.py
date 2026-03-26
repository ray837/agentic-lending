"""
Planner Agent — FastMCP Server (Plan / Execute / Resume)
════════════════════════════════════════════════════════
pip install fastmcp langchain-groq langchain-core

Three tools, three stages:
  1. plan_flow      → LLM parses query, returns plan for user to review
  2. execute_flow   → Takes confirmed plan, runs steps, PAUSES at data_change if value missing
  3. resume_flow    → User provides missing data, execution continues

Conversation example:
  User: "run CRED flow, change loanid before loan, kyc after loan"
  BOB calls plan_flow → returns plan: [pan, change(loanid), loanonboarding, kyc]
  BOB shows plan: "Here's the plan. Proceed?"
  User: "yes go ahead"
  BOB calls execute_flow → runs pan OK, hits change(loanid) with null → PAUSES
  BOB asks: "I need a value for loanid to continue."
  User: "use LOAN-999"
  BOB calls resume_flow with {"loanid": "LOAN-999"} → continues loanonboarding, kyc → DONE
"""

import json
import uuid
from dotenv import load_dotenv
from fastmcp import FastMCP
from langchain_groq import ChatGroq

from flow_planner import LLMFlowPlanner, PlannedStep, StepType, ExecutionPlan
from flow_registry import list_partners, get_partner_flow
from flow_executor import StepExecutor

load_dotenv()

# ── Planner's own LLM ────────────────────────────────────────────────────
groq_api_key = ""
planner_llm = ChatGroq(groq_api_key=groq_api_key, model_name="openai/gpt-oss-120b")
flow_planner = LLMFlowPlanner(llm=planner_llm)

# ── Executor with session state ──────────────────────────────────────────
executor = StepExecutor(mcp_tools=None)

# ── FastMCP Server ───────────────────────────────────────────────────────
mcp = FastMCP(
    name="PlannerAgent",
    instructions=(
        "Flow planner + executor for banking partners. "
        "Step 1: call plan_flow to generate a plan from user request. "
        "Step 2: show the plan to the user and ask for confirmation. "
        "Step 3: call execute_flow with the plan. If it pauses at a data change, ask the user for the value. "
        "Step 4: call resume_flow with the value to continue. "
        f"Partners: {', '.join(list_partners())}."
    ),
)


@mcp.tool()
async def plan_flow(user_request: str) -> str:
    """
    Generate an execution plan from a natural language request.
    Returns the plan for user review — does NOT execute.
    BOB should show this plan to the user and ask for confirmation
    before calling execute_flow.

    Args:
        user_request: e.g. "run CRED flow with kyc after loanonboarding"
                      or "change loanid before loan and skip kyc for PAYTM"
    """
    try:
        plan = await flow_planner.plan_async(user_request)
    except Exception as e:
        return json.dumps({"error": f"Planning failed: {e}"})

    if not plan.is_flow_request:
        return json.dumps({"is_flow_request": False, "message": "Not a flow request."})

    if not plan.partner:
        return json.dumps({"error": "Could not identify partner.", "available": list_partners()})

    # Flag which steps will need user input
    data_change_steps = []
    for s in plan.planned_flow:
        if s.step_type == StepType.DATA_CHANGE:
            null_fields = [f for f, v in s.data_changes.items() if v is None]
            if null_fields:
                data_change_steps.append({
                    "step": s.name,
                    "fields_needing_input": null_fields,
                    "note": "Execution will pause here to ask for values",
                })

    return json.dumps({
        "partner": plan.partner,
        "original_flow": plan.original_flow,
        "planned_flow": [s.to_dict() for s in plan.planned_flow],
        "planned_flow_summary": plan.get_step_names(),
        "modifications": plan.modifications,
        "data_change_warnings": data_change_steps,
        "next_action": "Show this plan to the user. If they approve, call execute_flow with the planned_flow JSON.",
    }, indent=2)


@mcp.tool()
def execute_flow(
    partner: str,
    planned_flow_json: str,
    initial_data: str = "{}",
    session_id: str = "",
) -> str:
    """
    Execute a confirmed plan step by step.
    PAUSES at data_change nodes when values are missing.
    Returns session state including status: "completed", "paused", or "failed".

    When status is "paused":
      - pending_fields tells you what values are needed
      - Call resume_flow with the session_id and the user-provided values

    Args:
        partner: Partner name from the plan (e.g. "CRED")
        planned_flow_json: The planned_flow array from plan_flow result, as JSON string.
        initial_data: Optional JSON of seed data e.g. '{"pan_number":"ABCDE1234F"}'
        session_id: Optional. If empty, a new session is created.
    """
    # Parse inputs
    try:
        steps_data = json.loads(planned_flow_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid planned_flow_json"})

    try:
        init_data = json.loads(initial_data) if initial_data else {}
    except json.JSONDecodeError:
        init_data = {}

    # Reconstruct plan
    planned_steps = []
    for s in steps_data:
        planned_steps.append(PlannedStep(
            name=s["name"],
            step_type=StepType(s.get("type", "entity")),
            data_changes=s.get("data_changes", {}),
        ))

    plan = ExecutionPlan(
        partner=partner.upper(),
        original_flow=get_partner_flow(partner),
        planned_flow=planned_steps,
        modifications=[],
        is_flow_request=True,
    )

    # Create session
    if not session_id:
        session_id = f"{partner}_{uuid.uuid4().hex[:8]}"

    executor.create_session(session_id, plan, init_data)

    # Execute until pause or completion
    result = executor.execute_until_pause(session_id)
    result["session_id"] = session_id

    # Add guidance for BOB
    if result.get("status") == "paused":
        result["next_action"] = (
            f"Execution paused at '{result.get('paused_at_step')}'. "
            f"Ask the user for values: {result.get('pending_fields')}. "
            f"Then call resume_flow with session_id='{session_id}' and the values."
        )
    elif result.get("status") == "completed":
        result["next_action"] = "All steps completed successfully. Summarize results for the user."
    elif result.get("status") == "failed":
        result["next_action"] = "Execution failed. Show errors to the user."

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def resume_flow(session_id: str, field_values_json: str) -> str:
    """
    Resume a paused execution after user provides missing data values.
    Continues from where it stopped. May pause again if another
    data_change node is encountered.

    Args:
        session_id: The session_id from the paused execute_flow result.
        field_values_json: JSON object with field values from the user.
                          e.g. '{"loanid": "LOAN-999"}' or '{"loan_amount": 50000}'
    """
    try:
        field_values = json.loads(field_values_json)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid field_values_json"})

    result = executor.resume_with_data(session_id, field_values)
    result["session_id"] = session_id

    if result.get("status") == "paused":
        result["next_action"] = (
            f"Still paused at '{result.get('paused_at_step')}'. "
            f"Need: {result.get('pending_fields')}. "
            f"Call resume_flow again with the values."
        )
    elif result.get("status") == "completed":
        result["next_action"] = "All steps completed. Summarize for the user."

    return json.dumps(result, indent=2, default=str)


@mcp.tool()
def list_partner_flows() -> str:
    """List all available partners and their default flows."""
    return json.dumps({p: get_partner_flow(p) for p in list_partners()}, indent=2)


@mcp.tool()
def get_session_status(session_id: str) -> str:
    """Check the current status of an execution session."""
    session = executor.get_session(session_id)
    if not session:
        return json.dumps({"error": f"Session '{session_id}' not found"})
    return json.dumps(session.to_dict(), indent=2, default=str)


if __name__ == "__main__":
    mcp.run()
