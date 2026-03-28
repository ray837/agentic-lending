"""
═══════════════════════════════════════════════════════════════════════════════
COMPLETE MCP SERVER — Banking Flow Execution System
═══════════════════════════════════════════════════════════════════════════════

This MCP server provides:
  1. Session management (create, resume, list)
  2. User input collection via Pydantic modals → auto-map to API_LOGS
  3. Flow planning (standard + custom) with confirmation
  4. Data change handling with confirmation
  5. Flow execution (only after confirmation)
  6. Persistent API_LOGS storage

WORKFLOW:
  1. create_session("CRED") → Initialize session
  2. requirement_gathering(session_id, ...) → Collect customer data (LoanCreationModal)
  3. plan_flow(session_id, "run loan flow") → Plan with confirmation
  4. confirm_and_execute(session_id) → Execute after confirmation
  5. handle_request(session_id, "change loan to 1 lakh") → Data changes

Partners: CRED, PAYTM, PHONEPE, RAZORPAY, SLICE
"""

import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

# FastMCP import
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    try:
        from fastmcp import FastMCP
        MCP_AVAILABLE = True
    except ImportError:
        MCP_AVAILABLE = False

# Local imports
from modals import (
    LoanCreationModal,
    KYCModal,
    BankDetailsModal,
    OTPVerificationModal,
    DataChangeModal,
    merge_modal_to_api_logs,
)
from planner import (
    FlowPlanner,
    FlowPlan,
    FlowState,
    ActionType,
    STEP_CATALOG,
    PARTNER_CONFIGS,
    get_all_available_steps,
)
from executor import FlowExecutor
from storage import APILogsStore


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALIZE SERVICES
# ═══════════════════════════════════════════════════════════════════════════════

STORAGE_DIR = "./api_logs_storage"
DEBUG_LOG_DIR = "./execution_logs"

# Storage
store = APILogsStore(STORAGE_DIR)

# Planner & Executor
planner = FlowPlanner(llm=None)  # Set llm for better intent classification
executor = FlowExecutor(store, DEBUG_LOG_DIR, dry_run=True)  # dry_run=True for testing


# ═══════════════════════════════════════════════════════════════════════════════
# MCP SERVER INITIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

if MCP_AVAILABLE:
    mcp = FastMCP(
        name="BankingFlowServer",
        instructions="""
Banking Flow Execution MCP Server

CAPABILITIES:
• Collect user data via Pydantic modals (auto-maps to API_LOGS)
• Plan flows (standard/custom) with user confirmation
• Handle data change requests with confirmation  
• Execute APIs step-by-step after confirmation
• Persist sessions to JSON files

WORKFLOW:
1. create_session(partner) → Initialize session
2. requirement_gathering(session_id, ...) → Collect customer data
3. plan_flow(session_id, request) → Plan execution
4. confirm_and_execute(session_id) → Execute after confirmation
5. handle_request(session_id, message) → Data changes, queries

PARTNERS: CRED, PAYTM, PHONEPE, RAZORPAY, SLICE

CONFIRMATION REQUIRED:
All flows and data changes require explicit user confirmation before execution.
""",
    )
else:
    mcp = None


# ═══════════════════════════════════════════════════════════════════════════════
# IMPLEMENTATION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _create_session(partner: str, loan_amount: int = 50000, tenure_months: int = 12) -> Dict:
    """Create a new session and initialize API_LOGS."""
    if partner.upper() not in PARTNER_CONFIGS:
        return {"success": False, "error": f"Unknown partner: {partner}"}

    session_id, api_logs = store.create_session(
        partner=partner,
        initial_data={"LOAN_AMOUNT": loan_amount, "TENURE_MONTHS": tenure_months},
    )

    config = PARTNER_CONFIGS[partner.upper()]
    return {
        "success": True,
        "session_id": session_id,
        "partner": partner.upper(),
        "base_url": config.get("base_url"),
        "default_flow_steps": len(config.get("default_flow", [])),
        "api_logs": api_logs,
        "next_action": "Use requirement_gathering to collect customer data",
    }


def _resume_session(session_id: str) -> Dict:
    """Resume an existing session."""
    api_logs = store.load_session(session_id)
    if not api_logs:
        return {"success": False, "error": f"Session {session_id} not found"}

    pending = planner.get_pending_plan(session_id)
    exec_state = executor.get_execution_state(session_id)

    return {
        "success": True,
        "session_id": session_id,
        "partner": api_logs.get("PARTNER"),
        "has_pending_plan": pending is not None,
        "pending_confirmation": pending.confirmation_message if pending else None,
        "is_paused": exec_state is not None and exec_state.state == FlowState.PAUSED,
        "api_logs": api_logs,
    }


def _requirement_gathering(session_id: str, customer_data: LoanCreationModal) -> Dict:
    """
    Collect loan requirements using LoanCreationModal.
    The modal fields automatically map to API_LOGS.
    """
    api_logs = store.load_session(session_id)
    if not api_logs:
        return {"success": False, "error": f"Session {session_id} not found"}

    # Auto-map modal to API_LOGS
    api_logs = merge_modal_to_api_logs(api_logs, customer_data)
    api_logs["EXECUTION_TRACE"].append({
        "type": "requirement_gathering",
        "data": customer_data.to_api_logs(),
        "timestamp": datetime.now().isoformat(),
    })
    api_logs["FLOW_STATE"] = "requirements_collected"
    store.save_session(session_id, api_logs)

    return {
        "success": True,
        "session_id": session_id,
        "collected_data": customer_data.to_api_logs(),
        "api_logs": api_logs,
        "next_action": "Use plan_flow to plan execution",
    }


async def _plan_flow(session_id: str, user_request: str) -> Dict:
    """Plan a flow based on user request. Returns plan requiring confirmation."""
    api_logs = store.load_session(session_id)
    if not api_logs:
        return {"success": False, "error": f"Session {session_id} not found"}

    plan = await planner.plan(
        user_message=user_request,
        session_id=session_id,
        partner=api_logs.get("PARTNER", "CRED"),
        api_logs=api_logs,
    )

    return {
        "success": True,
        "plan_id": plan.plan_id,
        "action_type": plan.action_type.value,
        "state": plan.state.value,
        "steps": [s.to_dict() for s in plan.steps],
        "data_changes": plan.data_changes,
        "confirmation_message": plan.confirmation_message,
    }


async def _confirm_and_execute(session_id: str, confirmed: bool = True) -> Dict:
    """Confirm and execute a pending plan."""
    api_logs = store.load_session(session_id)
    if not api_logs:
        return {"success": False, "error": f"Session {session_id} not found"}

    # Handle confirmation
    plan = await planner.plan(
        user_message="yes" if confirmed else "no",
        session_id=session_id,
        partner=api_logs.get("PARTNER", "CRED"),
        api_logs=api_logs,
    )

    if plan.state == FlowState.CANCELLED:
        return {"success": True, "cancelled": True, "message": "Flow cancelled."}

    if plan.state != FlowState.CONFIRMED:
        return {"success": False, "error": "No pending plan to confirm"}

    # Execute
    result = await executor.execute(plan, api_logs)
    return {
        "success": result.success,
        "state": result.state.value,
        "completed_steps": [s.to_dict() for s in result.completed_steps],
        "message": result.message,
        "error": result.error,
        "api_logs": store.load_session(session_id),
    }


async def _handle_request(session_id: str, user_message: str) -> Dict:
    """Handle any user request - data changes, custom flows, queries, confirmations."""
    api_logs = store.load_session(session_id)
    if not api_logs:
        return {"success": False, "error": f"Session {session_id} not found"}

    plan = await planner.plan(
        user_message=user_message,
        session_id=session_id,
        partner=api_logs.get("PARTNER", "CRED"),
        api_logs=api_logs,
    )

    response = {"plan_id": plan.plan_id, "action_type": plan.action_type.value, "state": plan.state.value}

    if plan.action_type == ActionType.QUERY:
        response["message"] = plan.confirmation_message
    elif plan.state == FlowState.PENDING_CONFIRMATION:
        response["confirmation_message"] = plan.confirmation_message
        response["data_changes"] = plan.data_changes
        response["steps"] = [s.to_dict() for s in plan.steps]
    elif plan.state == FlowState.CONFIRMED:
        result = await executor.execute(plan, api_logs)
        response["execution_result"] = result.to_dict()
        response["api_logs"] = store.load_session(session_id)

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

if mcp:
    @mcp.tool()
    def create_session(partner: str, loan_amount: int = 50000, tenure_months: int = 12) -> str:
        """Create a new session. Partners: CRED, PAYTM, PHONEPE, RAZORPAY, SLICE"""
        return json.dumps(_create_session(partner, loan_amount, tenure_months), indent=2, default=str)

    @mcp.tool()
    def resume_session(session_id: str) -> str:
        """Resume an existing session."""
        return json.dumps(_resume_session(session_id), indent=2, default=str)

    @mcp.tool()
    def list_sessions(partner: str = "") -> str:
        """List all saved sessions."""
        sessions = store.list_sessions(partner if partner else None)
        return json.dumps({"sessions": sessions, "total": len(sessions)}, indent=2)

    @mcp.tool()
    def requirement_gathering(
        session_id: str,
        customer_name: str,
        pan_number: str,
        dob: str,
        mobile: str,
        email: str = "",
        loan_amount: int = 50000,
        loan_type: str = "personal",
        tenure_months: int = 12,
        purpose: str = "",
    ) -> str:
        """
        Collect loan requirements. Uses LoanCreationModal and auto-maps to API_LOGS.

        Args:
            customer_name: Full name as per documents
            pan_number: PAN card (e.g., ABCDE1234F)
            dob: Date of birth (YYYY-MM-DD)
            mobile: 10-digit mobile number
        """
        try:
            modal = LoanCreationModal(
                customer_name=customer_name,
                pan_number=pan_number,
                dob=dob,
                mobile=mobile,
                email=email or None,
                loan_amount=loan_amount,
                loan_type=loan_type,
                tenure_months=tenure_months,
                purpose=purpose or None,
            )
        except Exception as e:
            return json.dumps({"success": False, "error": f"Validation failed: {e}"}, indent=2)

        return json.dumps(_requirement_gathering(session_id, modal), indent=2, default=str)

    @mcp.tool()
    def collect_kyc(session_id: str, aadhaar_number: str, address: str, city: str, state: str, pincode: str) -> str:
        """Collect KYC details using KYCModal."""
        try:
            modal = KYCModal(aadhaar_number=aadhaar_number, address=address, city=city, state=state, pincode=pincode)
            api_logs = store.load_session(session_id)
            if not api_logs:
                return json.dumps({"success": False, "error": "Session not found"})
            api_logs = merge_modal_to_api_logs(api_logs, modal)
            store.save_session(session_id, api_logs)
            return json.dumps({"success": True, "collected_data": modal.to_api_logs()}, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    def collect_bank_details(session_id: str, bank_account: str, ifsc: str, account_holder_name: str = "") -> str:
        """Collect bank details using BankDetailsModal."""
        try:
            modal = BankDetailsModal(bank_account=bank_account, ifsc=ifsc, account_holder_name=account_holder_name or None)
            api_logs = store.load_session(session_id)
            if not api_logs:
                return json.dumps({"success": False, "error": "Session not found"})
            api_logs = merge_modal_to_api_logs(api_logs, modal)
            store.save_session(session_id, api_logs)
            return json.dumps({"success": True, "collected_data": modal.to_api_logs()}, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, indent=2)

    @mcp.tool()
    async def plan_flow(session_id: str, user_request: str) -> str:
        """
        Plan a flow. Requires confirmation before execution.
        Examples: "run loan flow", "run only pan and credit check", "change loan to 1 lakh"
        """
        return json.dumps(await _plan_flow(session_id, user_request), indent=2, default=str)

    @mcp.tool()
    async def confirm_and_execute(session_id: str, confirmed: bool = True) -> str:
        """Confirm and execute a pending plan."""
        return json.dumps(await _confirm_and_execute(session_id, confirmed), indent=2, default=str)

    @mcp.tool()
    async def handle_request(session_id: str, user_message: str) -> str:
        """
        Handle any request: data changes, custom flows, queries, confirmations.
        Examples: "change loan to 1 lakh", "what's my status?", "yes", "no"
        """
        return json.dumps(await _handle_request(session_id, user_message), indent=2, default=str)

    @mcp.tool()
    def get_api_logs(session_id: str) -> str:
        """Get current API_LOGS for inspection."""
        api_logs = store.load_session(session_id)
        if not api_logs:
            return json.dumps({"success": False, "error": "Session not found"})
        return json.dumps({"success": True, "api_logs": api_logs}, indent=2, default=str)

    @mcp.tool()
    def get_available_steps(partner: str = "CRED") -> str:
        """Get available steps for a partner."""
        config = PARTNER_CONFIGS.get(partner.upper())
        if not config:
            return json.dumps({"error": f"Partner not found", "available": list(PARTNER_CONFIGS.keys())})
        steps = [{"name": s, **STEP_CATALOG.get(s, {})} for s in config.get("default_flow", [])]
        return json.dumps({"partner": partner, "steps": steps}, indent=2)

    @mcp.tool()
    def get_modal_fields(modal_name: str) -> str:
        """Get field prompts for a modal. Options: LoanCreationModal, KYCModal, BankDetailsModal"""
        modals = {"LoanCreationModal": LoanCreationModal, "KYCModal": KYCModal, "BankDetailsModal": BankDetailsModal}
        modal_class = modals.get(modal_name)
        if not modal_class:
            return json.dumps({"error": "Modal not found", "available": list(modals.keys())})
        return json.dumps({"modal": modal_name, "fields": modal_class.get_field_prompts()}, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if mcp:
        mcp.run()
    else:
        print("FastMCP not available. Use implementation functions directly.")
