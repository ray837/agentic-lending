"""
═══════════════════════════════════════════════════════════════════════════════
FLOW PLANNER — Handles Flow Planning, Custom Flows, AND Data Changes
═══════════════════════════════════════════════════════════════════════════════

The Planner is responsible for:
  1. Planning standard flows (fetches partner configs)
  2. Creating custom flows from user requests
  3. Handling data change requests (updates API_LOGS)
  4. Managing confirmation state (nothing executes without user confirmation)

The Planner outputs a FlowPlan that the Executor consumes.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Dict, List, Callable
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# FLOW STATE & ACTION TYPE
# ═══════════════════════════════════════════════════════════════════════════════

class FlowState(str, Enum):
    """State of a flow/plan."""
    DRAFT = "draft"                          # Just created, not sent to user
    PENDING_CONFIRMATION = "pending_confirmation"  # Waiting for user yes/no
    CONFIRMED = "confirmed"                  # User said yes, ready to execute
    EXECUTING = "executing"                  # Currently running
    PAUSED = "paused"                        # Paused mid-execution (waiting for input)
    COMPLETED = "completed"                  # All done
    FAILED = "failed"                        # Something went wrong
    CANCELLED = "cancelled"                  # User said no


class ActionType(str, Enum):
    """Type of action the planner identified from user message."""
    FLOW_EXECUTION = "flow_execution"      # Run a standard flow
    CUSTOM_FLOW = "custom_flow"            # User-defined step sequence
    DATA_CHANGE = "data_change"            # Change API_LOGS fields
    QUERY = "query"                        # Information query (no confirmation needed)
    CONFIRMATION = "confirmation"          # User confirming/rejecting previous plan
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# PLANNED STEP
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlannedStep:
    """A single step in a planned flow."""
    name: str
    description: str = ""
    required_inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    requires_user_input: bool = False
    status: str = "pending"  # pending, executing, completed, failed, skipped
    result: Dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "required_inputs": self.required_inputs,
            "outputs": self.outputs,
            "requires_user_input": self.requires_user_input,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FLOW PLAN
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FlowPlan:
    """
    Complete flow plan output by the Planner.
    This is what gets passed to the Executor.
    """
    plan_id: str
    session_id: str
    partner: str
    action_type: ActionType
    state: FlowState

    # Flow details
    flow_name: str = ""
    steps: List[PlannedStep] = field(default_factory=list)
    current_step_index: int = 0

    # Data changes (for DATA_CHANGE action type)
    data_changes: Dict[str, Any] = field(default_factory=dict)
    old_values: Dict[str, Any] = field(default_factory=dict)  # For display

    # User interaction
    confirmation_message: str = ""
    requires_input: List[str] = field(default_factory=list)

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    user_request: str = ""

    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "partner": self.partner,
            "action_type": self.action_type.value,
            "state": self.state.value,
            "flow_name": self.flow_name,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "data_changes": self.data_changes,
            "old_values": self.old_values,
            "confirmation_message": self.confirmation_message,
            "requires_input": self.requires_input,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_request": self.user_request,
        }

    def get_pending_steps(self) -> List[PlannedStep]:
        return [s for s in self.steps if s.status == "pending"]

    def get_step_names(self) -> List[str]:
        return [s.name for s in self.steps]

    def mark_step_completed(self, step_name: str, result: Dict = None):
        for step in self.steps:
            if step.name == step_name:
                step.status = "completed"
                step.result = result or {}
                break
        self.updated_at = datetime.now().isoformat()

    def mark_step_failed(self, step_name: str, error: str):
        for step in self.steps:
            if step.name == step_name:
                step.status = "failed"
                step.error = error
                break
        self.state = FlowState.FAILED
        self.updated_at = datetime.now().isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# PARTNER & STEP REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

STEP_CATALOG = {
    "requirement_gathering": {
        "description": "Collect loan requirements from customer",
        "required_inputs": [],
        "outputs": ["FULL_NAME", "PAN_NUMBER", "PAN_DOB", "MOBILE", "LOAN_AMOUNT", "TENURE_MONTHS"],
        "requires_user_input": True,
    },
    "pan_verification": {
        "description": "Verify PAN card details with NSDL",
        "required_inputs": ["PAN_NUMBER", "PAN_DOB"],
        "outputs": ["PAN_VERIFIED", "PAN_NAME", "PAN_STATUS"],
        "requires_user_input": False,
    },
    "aadhaar_send_otp": {
        "description": "Send OTP for Aadhaar verification",
        "required_inputs": ["AADHAAR_NUMBER"],
        "outputs": ["AADHAAR_OTP_REF"],
        "requires_user_input": False,
    },
    "aadhaar_verify_otp": {
        "description": "Verify Aadhaar with OTP",
        "required_inputs": ["AADHAAR_OTP_REF", "AADHAAR_OTP"],
        "outputs": ["AADHAAR_VERIFIED", "AADHAAR_NAME", "ADDRESS"],
        "requires_user_input": True,  # Needs OTP from user
    },
    "kyc_completion": {
        "description": "Submit KYC documents and complete verification",
        "required_inputs": ["PAN_NUMBER", "AADHAAR_NUMBER", "FULL_NAME", "MOBILE", "ADDRESS"],
        "outputs": ["KYC_ID", "KYC_STATUS"],
        "requires_user_input": False,
    },
    "credit_check": {
        "description": "Check credit bureau score and eligibility",
        "required_inputs": ["PAN_NUMBER", "FULL_NAME", "MOBILE"],
        "outputs": ["CREDIT_SCORE", "CREDIT_ELIGIBLE", "CREDIT_LIMIT", "BUREAU_ID"],
        "requires_user_input": False,
    },
    "bank_verification": {
        "description": "Verify bank account via penny drop",
        "required_inputs": ["BANK_ACCOUNT", "IFSC"],
        "outputs": ["BANK_VERIFIED", "ACCOUNT_HOLDER_NAME", "BANK_NAME"],
        "requires_user_input": True,  # Needs bank details from user
    },
    "loan_creation": {
        "description": "Create loan application with approved terms",
        "required_inputs": ["KYC_ID", "LOAN_AMOUNT", "TENURE_MONTHS", "BANK_ACCOUNT", "IFSC"],
        "outputs": ["LOAN_ID", "LOAN_REFERENCE", "LOAN_STATUS", "EMI_AMOUNT", "INTEREST_RATE"],
        "requires_user_input": False,
    },
    "loan_agreement": {
        "description": "Generate and e-sign loan agreement",
        "required_inputs": ["LOAN_ID"],
        "outputs": ["AGREEMENT_ID", "AGREEMENT_STATUS", "AGREEMENT_URL"],
        "requires_user_input": True,  # Needs user e-signature consent
    },
    "disbursement": {
        "description": "Initiate loan disbursement to bank account",
        "required_inputs": ["LOAN_ID", "BANK_ACCOUNT", "IFSC"],
        "outputs": ["DISBURSEMENT_STATUS", "UTR", "DISBURSED_AMOUNT"],
        "requires_user_input": False,
    },
    "emandate_registration": {
        "description": "Register e-mandate for EMI auto-debit",
        "required_inputs": ["LOAN_ID", "BANK_ACCOUNT", "IFSC", "EMI_AMOUNT"],
        "outputs": ["MANDATE_ID", "MANDATE_STATUS", "MANDATE_URL"],
        "requires_user_input": True,  # Needs user consent for mandate
    },
}


PARTNER_CONFIGS = {
    "CRED": {
        "name": "CRED",
        "base_url": "https://api.cred.club/v1",
        "default_flow": [
            "requirement_gathering",
            "pan_verification",
            "aadhaar_send_otp",
            "aadhaar_verify_otp",
            "kyc_completion",
            "credit_check",
            "bank_verification",
            "loan_creation",
            "loan_agreement",
            "disbursement",
            "emandate_registration",
        ],
        "quick_flow": [
            "requirement_gathering",
            "pan_verification",
            "credit_check",
            "loan_creation",
            "disbursement",
        ],
    },
    "PAYTM": {
        "name": "PAYTM",
        "base_url": "https://api.paytm.com/lending/v1",
        "default_flow": [
            "requirement_gathering",
            "pan_verification",
            "kyc_completion",
            "credit_check",
            "bank_verification",
            "loan_creation",
            "disbursement",
        ],
    },
    "PHONEPE": {
        "name": "PHONEPE",
        "base_url": "https://api.phonepe.com/v1/lending",
        "default_flow": [
            "requirement_gathering",
            "pan_verification",
            "aadhaar_send_otp",
            "aadhaar_verify_otp",
            "credit_check",
            "bank_verification",
            "loan_creation",
            "emandate_registration",
            "disbursement",
        ],
    },
    "RAZORPAY": {
        "name": "RAZORPAY",
        "base_url": "https://api.razorpay.com/v1",
        "default_flow": [
            "requirement_gathering",
            "pan_verification",
            "credit_check",
            "loan_creation",
            "disbursement",
        ],
    },
    "SLICE": {
        "name": "SLICE",
        "base_url": "https://api.sliceit.com/v1",
        "default_flow": [
            "requirement_gathering",
            "pan_verification",
            "aadhaar_verify_otp",
            "credit_check",
            "loan_creation",
            "emandate_registration",
            "disbursement",
        ],
    },
}


def get_partner_config(partner: str) -> Dict:
    """Get partner configuration."""
    return PARTNER_CONFIGS.get(partner.upper(), {})


def get_partner_default_flow(partner: str) -> List[str]:
    """Get default flow for a partner."""
    config = get_partner_config(partner)
    return config.get("default_flow", [])


def get_step_metadata(step_name: str) -> Dict:
    """Get metadata for a step."""
    return STEP_CATALOG.get(step_name, {})


def get_all_available_steps() -> List[str]:
    """Get all available step names."""
    return list(STEP_CATALOG.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# THE PLANNER
# ═══════════════════════════════════════════════════════════════════════════════

class FlowPlanner:
    """
    The Planner handles:
      1. Parsing user requests to determine action type
      2. Planning standard flows (fetches partner configs)
      3. Creating custom flows from user requests
      4. Handling data change requests
      5. Managing pending plans (nothing executes without confirmation)
    """

    def __init__(self, llm: Optional[Any] = None):
        """
        Args:
            llm: Optional LLM for better intent understanding (langchain compatible)
        """
        self.llm = llm
        self._pending_plans: Dict[str, FlowPlan] = {}  # session_id -> pending plan

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    async def plan(
        self,
        user_message: str,
        session_id: str,
        partner: str,
        api_logs: Dict[str, Any],
    ) -> FlowPlan:
        """
        Main entry point: Parse user request and create a plan.

        The plan will have state=PENDING_CONFIRMATION and must be confirmed
        before execution (except for QUERY which completes immediately).

        Args:
            user_message: What the user said
            session_id: Current session ID
            partner: Partner name (CRED, PAYTM, etc.)
            api_logs: Current API_LOGS state

        Returns:
            FlowPlan with confirmation_message (user must confirm before execution)
        """
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        # Step 1: Classify the request
        action_type = await self._classify_request(user_message, api_logs)

        # Step 2: Handle based on action type
        if action_type == ActionType.CONFIRMATION:
            return self._handle_confirmation(plan_id, user_message, session_id, partner)

        elif action_type == ActionType.DATA_CHANGE:
            return await self._plan_data_change(plan_id, user_message, session_id, partner, api_logs)

        elif action_type == ActionType.CUSTOM_FLOW:
            return await self._plan_custom_flow(plan_id, user_message, session_id, partner, api_logs)

        elif action_type == ActionType.FLOW_EXECUTION:
            return await self._plan_flow_execution(plan_id, user_message, session_id, partner, api_logs)

        elif action_type == ActionType.QUERY:
            return self._handle_query(plan_id, user_message, session_id, partner, api_logs)

        else:
            return self._handle_unknown(plan_id, session_id, partner, user_message)

    # ─────────────────────────────────────────────────────────────────────────
    # REQUEST CLASSIFICATION
    # ─────────────────────────────────────────────────────────────────────────

    async def _classify_request(self, message: str, api_logs: Dict) -> ActionType:
        """Classify what type of request this is."""
        msg = message.lower().strip()

        # 1. Check for confirmation/rejection patterns
        confirm_words = ["yes", "confirm", "proceed", "go ahead", "execute", "approve", "ok", "sure", "do it"]
        reject_words = ["no", "cancel", "stop", "reject", "don't", "abort", "nevermind"]

        if any(word in msg for word in confirm_words) or any(word in msg for word in reject_words):
            # Only if we have a pending plan
            if any(self._pending_plans.values()):
                return ActionType.CONFIRMATION

        # 2. Check for data change patterns
        change_patterns = [
            r"change\s+(\w+)",
            r"update\s+(\w+)",
            r"set\s+(\w+)",
            r"modify\s+(\w+)",
            r"my\s+(pan|aadhaar|mobile|email|account|name|address)\s+(is|number|:)",
            r"(pan|aadhaar|mobile|email)\s*[:=]?\s*[A-Z0-9@]+",
        ]
        for pattern in change_patterns:
            if re.search(pattern, msg):
                return ActionType.DATA_CHANGE

        # 3. Check for custom flow patterns
        custom_patterns = [
            "custom flow",
            "create flow",
            "define flow",
            "my own flow",
            "only run",
            "just run",
            "only do",
            "just do",
            "skip",
            "run only",
            "execute only",
        ]
        if any(phrase in msg for phrase in custom_patterns):
            return ActionType.CUSTOM_FLOW

        # 4. Check for flow execution patterns
        flow_patterns = [
            "run",
            "execute",
            "start",
            "begin",
            "flow",
            "onboarding",
            "loan process",
            "complete",
        ]
        if any(phrase in msg for phrase in flow_patterns):
            return ActionType.FLOW_EXECUTION

        # 5. Check for query patterns
        query_patterns = [
            "what is",
            "what's",
            "show me",
            "tell me",
            "how",
            "status",
            "where",
            "list",
            "?",
        ]
        if any(phrase in msg for phrase in query_patterns):
            return ActionType.QUERY

        # 6. Use LLM if available for better classification
        if self.llm:
            return await self._llm_classify(message)

        return ActionType.UNKNOWN

    async def _llm_classify(self, message: str) -> ActionType:
        """Use LLM for classification (if available)."""
        prompt = f"""Classify this user request into exactly ONE category:

FLOW_EXECUTION - User wants to run/start a loan/onboarding flow
CUSTOM_FLOW - User wants to run specific steps or create custom flow
DATA_CHANGE - User wants to update/change some data (amount, PAN, mobile, etc.)
CONFIRMATION - User is saying yes/no to a previous question
QUERY - User is asking a question or requesting information
UNKNOWN - Cannot determine

User request: "{message}"

Reply with ONLY the category name, nothing else."""

        try:
            response = await self.llm.ainvoke(prompt)
            category = response.content.strip().upper()
            return ActionType(category.lower())
        except Exception:
            return ActionType.UNKNOWN

    # ─────────────────────────────────────────────────────────────────────────
    # CONFIRMATION HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_confirmation(
        self,
        plan_id: str,
        message: str,
        session_id: str,
        partner: str,
    ) -> FlowPlan:
        """Handle user confirmation/rejection of pending plan."""
        msg = message.lower()

        # Get pending plan for this session
        pending = self._pending_plans.get(session_id)

        if not pending:
            return FlowPlan(
                plan_id=plan_id,
                session_id=session_id,
                partner=partner,
                action_type=ActionType.CONFIRMATION,
                state=FlowState.FAILED,
                confirmation_message="❌ No pending plan to confirm. Please make a request first.",
                user_request=message,
            )

        # Check if confirmed
        confirm_words = ["yes", "confirm", "proceed", "go ahead", "execute", "approve", "ok", "sure", "do it"]
        if any(word in msg for word in confirm_words):
            pending.state = FlowState.CONFIRMED
            del self._pending_plans[session_id]
            return pending

        # User rejected
        pending.state = FlowState.CANCELLED
        del self._pending_plans[session_id]
        return pending

    # ─────────────────────────────────────────────────────────────────────────
    # DATA CHANGE PLANNING
    # ─────────────────────────────────────────────────────────────────────────

    async def _plan_data_change(
        self,
        plan_id: str,
        message: str,
        session_id: str,
        partner: str,
        api_logs: Dict[str, Any],
    ) -> FlowPlan:
        """Plan data changes to API_LOGS."""

        # Extract what fields to change
        changes = await self._extract_data_changes(message, api_logs)

        if not changes:
            return FlowPlan(
                plan_id=plan_id,
                session_id=session_id,
                partner=partner,
                action_type=ActionType.DATA_CHANGE,
                state=FlowState.FAILED,
                confirmation_message="❌ Could not determine which fields to change.\n\n"
                    "Please specify clearly like:\n"
                    "  • 'change loan amount to 1 lakh'\n"
                    "  • 'my PAN is ABCDE1234F'\n"
                    "  • 'update mobile to 9876543210'",
                user_request=message,
            )

        # Get old values for display
        old_values = {k: api_logs.get(k, "not set") for k in changes.keys()}

        # Format confirmation message
        lines = []
        for key, new_val in changes.items():
            old_val = old_values[key]
            lines.append(f"  • {key}: {old_val} → {new_val}")

        confirmation = "📝 **Data Change Request**\n\n"
        confirmation += "I'll update the following fields:\n"
        confirmation += "\n".join(lines)
        confirmation += "\n\n✅ Confirm? (yes/no)"

        plan = FlowPlan(
            plan_id=plan_id,
            session_id=session_id,
            partner=partner,
            action_type=ActionType.DATA_CHANGE,
            state=FlowState.PENDING_CONFIRMATION,
            data_changes=changes,
            old_values=old_values,
            confirmation_message=confirmation,
            user_request=message,
        )

        # Store as pending
        self._pending_plans[session_id] = plan

        return plan

    async def _extract_data_changes(self, message: str, api_logs: Dict) -> Dict[str, Any]:
        """Extract field changes from user message using pattern matching."""
        changes = {}
        msg = message.lower()
        msg_orig = message  # Keep original case for PAN, IFSC

        # ─── Amount patterns ───
        amount_match = re.search(r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(lakh|lac|l|crore|cr|k|thousand)?", msg)
        if amount_match:
            amount = float(amount_match.group(1).replace(",", ""))
            multiplier = amount_match.group(2)

            if multiplier in ("lakh", "lac", "l"):
                amount *= 100000
            elif multiplier in ("crore", "cr"):
                amount *= 10000000
            elif multiplier in ("k", "thousand"):
                amount *= 1000

            amount = int(amount)

            # Determine which amount field based on context
            if any(x in msg for x in ("loan", "amount", "borrow", "need", "want")):
                changes["LOAN_AMOUNT"] = amount
            elif "emi" in msg:
                changes["EMI_AMOUNT"] = amount

        # ─── Tenure patterns ───
        tenure_match = re.search(r"(\d+)\s*(month|months|year|years|yr|yrs)", msg)
        if tenure_match:
            tenure = int(tenure_match.group(1))
            unit = tenure_match.group(2)
            if "year" in unit or "yr" in unit:
                tenure *= 12
            changes["TENURE_MONTHS"] = tenure

        # ─── PAN pattern ───
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", msg_orig.upper())
        if pan_match:
            changes["PAN_NUMBER"] = pan_match.group(1)

        # ─── Aadhaar pattern ───
        aadhaar_match = re.search(r"\b(\d{4}\s?\d{4}\s?\d{4})\b", message)
        if aadhaar_match:
            changes["AADHAAR_NUMBER"] = aadhaar_match.group(1).replace(" ", "")

        # ─── Mobile pattern ───
        mobile_match = re.search(r"\b([6-9]\d{9})\b", message)
        if mobile_match:
            changes["MOBILE"] = mobile_match.group(1)

        # ─── Email pattern ───
        email_match = re.search(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b", message)
        if email_match:
            changes["EMAIL"] = email_match.group(1).lower()

        # ─── IFSC pattern ───
        ifsc_match = re.search(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", msg_orig.upper())
        if ifsc_match:
            changes["IFSC"] = ifsc_match.group(1)

        # ─── Name pattern ───
        name_match = re.search(r"(?:name|customer)\s+(?:is|to|:)\s*([A-Za-z\s]+)", msg)
        if name_match:
            name = name_match.group(1).strip().title()
            if len(name) > 2:
                changes["FULL_NAME"] = name

        # ─── Account number pattern ───
        account_match = re.search(r"account\s*(?:number|no|#)?\s*(?:is|to|:)?\s*(\d{9,18})", msg)
        if account_match:
            changes["BANK_ACCOUNT"] = account_match.group(1)

        # ─── DOB pattern ───
        dob_match = re.search(r"(?:dob|date of birth|birth)\s*(?:is|to|:)?\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}|\d{2}-\d{2}-\d{4})", msg)
        if dob_match:
            dob = dob_match.group(1)
            # Normalize to YYYY-MM-DD
            if "/" in dob or (dob.count("-") == 2 and not dob.startswith("19") and not dob.startswith("20")):
                parts = re.split(r"[/-]", dob)
                if len(parts[2]) == 4:
                    dob = f"{parts[2]}-{parts[1]}-{parts[0]}"
            changes["PAN_DOB"] = dob

        # Use LLM for extraction if available and no patterns matched
        if not changes and self.llm:
            changes = await self._llm_extract_changes(message, api_logs)

        return changes

    async def _llm_extract_changes(self, message: str, api_logs: Dict) -> Dict:
        """Use LLM to extract data changes."""
        fields = ", ".join([
            "LOAN_AMOUNT", "TENURE_MONTHS", "PAN_NUMBER", "PAN_DOB", "MOBILE",
            "EMAIL", "AADHAAR_NUMBER", "BANK_ACCOUNT", "IFSC", "FULL_NAME", "ADDRESS"
        ])

        prompt = f"""Extract data field changes from this user message.

Available fields: {fields}

User message: "{message}"

Current values: {json.dumps({k: v for k, v in api_logs.items() if k in fields.split(", ")}, indent=2)}

Respond with ONLY a JSON object of field changes.
Example: {{"LOAN_AMOUNT": 100000, "TENURE_MONTHS": 24}}
If no changes found, respond with: {{}}

JSON:"""

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content.strip()
            # Clean up markdown
            if "```" in content:
                content = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
                content = content.group(1) if content else "{}"
            return json.loads(content.strip())
        except Exception:
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # CUSTOM FLOW PLANNING
    # ─────────────────────────────────────────────────────────────────────────

    async def _plan_custom_flow(
        self,
        plan_id: str,
        message: str,
        session_id: str,
        partner: str,
        api_logs: Dict[str, Any],
    ) -> FlowPlan:
        """Plan a custom flow based on user specification."""

        # Extract step names from message
        steps = self._extract_steps_from_message(message)

        if not steps:
            # Show available steps and ask user to specify
            available = get_all_available_steps()
            step_list = "\n".join(f"  • {s}: {STEP_CATALOG[s]['description']}" for s in available)

            return FlowPlan(
                plan_id=plan_id,
                session_id=session_id,
                partner=partner,
                action_type=ActionType.CUSTOM_FLOW,
                state=FlowState.DRAFT,
                confirmation_message=f"🔧 **Custom Flow Request**\n\n"
                    f"Please specify which steps to include.\n\n"
                    f"**Available Steps:**\n{step_list}\n\n"
                    f"Example: 'run only pan_verification and credit_check'",
                user_request=message,
            )

        # Build planned steps
        planned_steps = []
        all_required = set()

        for step_name in steps:
            meta = get_step_metadata(step_name)
            if not meta:
                continue

            step = PlannedStep(
                name=step_name,
                description=meta.get("description", ""),
                required_inputs=meta.get("required_inputs", []),
                outputs=meta.get("outputs", []),
                requires_user_input=meta.get("requires_user_input", False),
            )
            planned_steps.append(step)
            all_required.update(meta.get("required_inputs", []))

        # Check which required inputs are missing
        missing_inputs = [f for f in all_required if not api_logs.get(f)]

        # Format confirmation message
        step_list = "\n".join(f"  {i+1}. **{s.name}**: {s.description}" for i, s in enumerate(planned_steps))

        confirmation = f"🔧 **Custom Flow: {len(planned_steps)} Steps**\n\n"
        confirmation += f"Partner: {partner}\n\n"
        confirmation += f"**Steps to Execute:**\n{step_list}"

        if missing_inputs:
            confirmation += f"\n\n⚠️ **Missing Inputs:** {', '.join(missing_inputs)}"
            confirmation += "\n(Will be collected during execution)"

        confirmation += "\n\n✅ Proceed? (yes/no)"

        plan = FlowPlan(
            plan_id=plan_id,
            session_id=session_id,
            partner=partner,
            action_type=ActionType.CUSTOM_FLOW,
            state=FlowState.PENDING_CONFIRMATION,
            flow_name=f"custom_{plan_id}",
            steps=planned_steps,
            requires_input=missing_inputs,
            confirmation_message=confirmation,
            user_request=message,
        )

        self._pending_plans[session_id] = plan
        return plan

    def _extract_steps_from_message(self, message: str) -> List[str]:
        """Extract step names from user message."""
        msg = message.lower()
        found_steps = []

        # Check for exact step names
        for step_name in STEP_CATALOG:
            step_words = step_name.replace("_", " ")
            if step_name in msg or step_words in msg:
                if step_name not in found_steps:
                    found_steps.append(step_name)

        # Check common aliases
        aliases = {
            "pan": "pan_verification",
            "pan check": "pan_verification",
            "pan verify": "pan_verification",
            "aadhaar": "aadhaar_verify_otp",
            "aadhaar otp": "aadhaar_send_otp",
            "kyc": "kyc_completion",
            "credit": "credit_check",
            "credit score": "credit_check",
            "bureau": "credit_check",
            "bank": "bank_verification",
            "bank verify": "bank_verification",
            "loan": "loan_creation",
            "create loan": "loan_creation",
            "agreement": "loan_agreement",
            "sign": "loan_agreement",
            "disburse": "disbursement",
            "payout": "disbursement",
            "mandate": "emandate_registration",
            "nach": "emandate_registration",
            "autopay": "emandate_registration",
        }

        for alias, step_name in aliases.items():
            if alias in msg and step_name not in found_steps:
                found_steps.append(step_name)

        return found_steps

    # ─────────────────────────────────────────────────────────────────────────
    # STANDARD FLOW EXECUTION PLANNING
    # ─────────────────────────────────────────────────────────────────────────

    async def _plan_flow_execution(
        self,
        plan_id: str,
        message: str,
        session_id: str,
        partner: str,
        api_logs: Dict[str, Any],
    ) -> FlowPlan:
        """Plan standard flow execution using partner config."""

        # Get partner config
        config = get_partner_config(partner)
        if not config:
            available = ", ".join(PARTNER_CONFIGS.keys())
            return FlowPlan(
                plan_id=plan_id,
                session_id=session_id,
                partner=partner,
                action_type=ActionType.FLOW_EXECUTION,
                state=FlowState.FAILED,
                confirmation_message=f"❌ Partner '{partner}' not found.\n\n"
                    f"Available partners: {available}",
                user_request=message,
            )

        # Determine which flow to use
        flow_steps = config.get("default_flow", [])
        flow_name = f"{partner.lower()}_default"

        if "quick" in message.lower():
            quick_flow = config.get("quick_flow")
            if quick_flow:
                flow_steps = quick_flow
                flow_name = f"{partner.lower()}_quick"

        if not flow_steps:
            return FlowPlan(
                plan_id=plan_id,
                session_id=session_id,
                partner=partner,
                action_type=ActionType.FLOW_EXECUTION,
                state=FlowState.FAILED,
                confirmation_message=f"❌ No flow configured for partner: {partner}",
                user_request=message,
            )

        # Build planned steps
        planned_steps = []
        initial_missing = set()

        for step_name in flow_steps:
            meta = get_step_metadata(step_name)
            if not meta:
                continue

            step = PlannedStep(
                name=step_name,
                description=meta.get("description", ""),
                required_inputs=meta.get("required_inputs", []),
                outputs=meta.get("outputs", []),
                requires_user_input=meta.get("requires_user_input", False),
            )
            planned_steps.append(step)

            # Check initial missing inputs (first 2 steps)
            if len(planned_steps) <= 2:
                for inp in meta.get("required_inputs", []):
                    if not api_logs.get(inp):
                        initial_missing.add(inp)

        # Format confirmation message
        step_summary = "\n".join(f"  {i+1}. {s.name}" for i, s in enumerate(planned_steps))

        confirmation = f"🚀 **{partner} Loan Flow**\n\n"
        confirmation += f"Flow: {flow_name} ({len(planned_steps)} steps)\n"
        confirmation += f"Base URL: {config.get('base_url', 'N/A')}\n\n"
        confirmation += f"**Steps:**\n{step_summary}"

        if initial_missing:
            confirmation += f"\n\n📝 **Initial data needed:** {', '.join(initial_missing)}"

        confirmation += "\n\n✅ Start execution? (yes/no)"

        plan = FlowPlan(
            plan_id=plan_id,
            session_id=session_id,
            partner=partner,
            action_type=ActionType.FLOW_EXECUTION,
            state=FlowState.PENDING_CONFIRMATION,
            flow_name=flow_name,
            steps=planned_steps,
            requires_input=list(initial_missing),
            confirmation_message=confirmation,
            user_request=message,
        )

        self._pending_plans[session_id] = plan
        return plan

    # ─────────────────────────────────────────────────────────────────────────
    # QUERY HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_query(
        self,
        plan_id: str,
        message: str,
        session_id: str,
        partner: str,
        api_logs: Dict[str, Any],
    ) -> FlowPlan:
        """Handle information queries (no confirmation needed)."""
        msg = message.lower()
        response = ""

        if "loan amount" in msg or "amount" in msg:
            amt = api_logs.get("LOAN_AMOUNT", "not set")
            response = f"💰 Current loan amount: ₹{amt:,}" if isinstance(amt, int) else f"💰 Loan amount: {amt}"

        elif "tenure" in msg or "month" in msg:
            tenure = api_logs.get("TENURE_MONTHS", "not set")
            response = f"📅 Current tenure: {tenure} months"

        elif "status" in msg:
            loan_status = api_logs.get("LOAN_STATUS", "not started")
            kyc_status = api_logs.get("KYC_STATUS", "not started")
            response = f"📊 **Status**\n  • Loan: {loan_status}\n  • KYC: {kyc_status}"

        elif "pan" in msg:
            pan = api_logs.get("PAN_NUMBER", "not set")
            verified = api_logs.get("PAN_VERIFIED", False)
            response = f"🪪 PAN: {pan} (Verified: {'✅' if verified else '❌'})"

        elif "step" in msg or "available" in msg:
            steps = get_all_available_steps()
            step_list = "\n".join(f"  • {s}" for s in steps)
            response = f"📋 **Available Steps:**\n{step_list}"

        elif "partner" in msg:
            partners = "\n".join(f"  • {p}" for p in PARTNER_CONFIGS.keys())
            response = f"🏢 **Available Partners:**\n{partners}"

        else:
            # General summary
            key_fields = ["FULL_NAME", "PAN_NUMBER", "MOBILE", "LOAN_AMOUNT", "TENURE_MONTHS", "LOAN_STATUS", "KYC_STATUS"]
            summary = []
            for field in key_fields:
                value = api_logs.get(field)
                if value is not None:
                    summary.append(f"  • {field}: {value}")

            response = "📋 **Current Data:**\n" + ("\n".join(summary) if summary else "  No data collected yet")

        return FlowPlan(
            plan_id=plan_id,
            session_id=session_id,
            partner=partner,
            action_type=ActionType.QUERY,
            state=FlowState.COMPLETED,
            confirmation_message=response,
            user_request=message,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # UNKNOWN HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_unknown(
        self,
        plan_id: str,
        session_id: str,
        partner: str,
        message: str,
    ) -> FlowPlan:
        """Handle unrecognized requests."""
        return FlowPlan(
            plan_id=plan_id,
            session_id=session_id,
            partner=partner,
            action_type=ActionType.UNKNOWN,
            state=FlowState.DRAFT,
            confirmation_message="❓ I couldn't understand your request.\n\n"
                "**Try:**\n"
                "  • 'run loan flow' - Execute standard flow\n"
                "  • 'run only pan and credit check' - Custom flow\n"
                "  • 'change loan amount to 1 lakh' - Update data\n"
                "  • 'what's my status?' - Query information",
            user_request=message,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────────────────────

    def get_pending_plan(self, session_id: str) -> Optional[FlowPlan]:
        """Get pending plan for a session."""
        return self._pending_plans.get(session_id)

    def clear_pending_plan(self, session_id: str):
        """Clear pending plan for a session."""
        self._pending_plans.pop(session_id, None)

    def has_pending_plan(self, session_id: str) -> bool:
        """Check if session has a pending plan."""
        return session_id in self._pending_plans


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Core classes
    "FlowPlanner",
    "FlowPlan",
    "PlannedStep",

    # Enums
    "FlowState",
    "ActionType",

    # Registry
    "STEP_CATALOG",
    "PARTNER_CONFIGS",

    # Helper functions
    "get_partner_config",
    "get_partner_default_flow",
    "get_step_metadata",
    "get_all_available_steps",
]
