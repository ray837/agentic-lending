"""
═══════════════════════════════════════════════════════════════════════════════
FLOW EXECUTOR — Executes Plans from the Planner
═══════════════════════════════════════════════════════════════════════════════

The Executor is responsible for:
  1. Executing flow plans step by step (ONLY after confirmation)
  2. Applying data changes to API_LOGS (ONLY after confirmation)
  3. Making API calls with {{FIELD}} template substitution
  4. Updating API_LOGS with response data
  5. Handling pauses when user input is needed
  6. Tracking execution state and providing resume capability
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Dict, List

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from planner import FlowPlan, PlannedStep, FlowState, ActionType


# ═══════════════════════════════════════════════════════════════════════════════
# API CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class APIConfig:
    """Configuration for an API endpoint."""
    name: str
    endpoint: str  # Path only, e.g., "/kyc/pan/verify"
    method: str = "POST"
    payload_template: Dict = field(default_factory=dict)
    headers_template: Dict = field(default_factory=dict)
    response_mappings: Dict = field(default_factory=dict)  # response_path -> API_LOGS_KEY

    def get_required_fields(self) -> set:
        """Extract required fields from payload and endpoint templates."""
        pattern = re.compile(r"\{\{([A-Z_]+)\}\}")
        fields = set()

        def extract(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    extract(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract(item)
            elif isinstance(obj, str):
                fields.update(pattern.findall(obj))

        extract(self.payload_template)
        extract(self.endpoint)
        return fields


# Partner base URLs
PARTNER_BASE_URLS = {
    "CRED": "https://api.cred.club/v1",
    "PAYTM": "https://api.paytm.com/lending/v1",
    "PHONEPE": "https://api.phonepe.com/v1/lending",
    "RAZORPAY": "https://api.razorpay.com/v1",
    "SLICE": "https://api.sliceit.com/v1",
}


# Step API configurations with {{FIELD}} templates
STEP_API_CONFIGS: Dict[str, APIConfig] = {
    "pan_verification": APIConfig(
        name="pan_verification",
        endpoint="/kyc/pan/verify",
        method="POST",
        payload_template={
            "pan": "{{PAN_NUMBER}}",
            "dob": "{{PAN_DOB}}",
            "full_name": "{{FULL_NAME}}",
            "consent": True,
            "request_id": "{{REQID}}",
        },
        response_mappings={
            "data.verified": "PAN_VERIFIED",
            "data.name_match": "PAN_NAME_MATCH",
            "data.name": "PAN_NAME",
            "data.status": "PAN_STATUS",
        },
    ),

    "aadhaar_send_otp": APIConfig(
        name="aadhaar_send_otp",
        endpoint="/kyc/aadhaar/otp/send",
        method="POST",
        payload_template={
            "aadhaar_number": "{{AADHAAR_NUMBER}}",
            "consent": True,
            "request_id": "{{REQID}}",
        },
        response_mappings={
            "data.otp_reference": "AADHAAR_OTP_REF",
            "data.mobile_masked": "AADHAAR_MOBILE_MASKED",
        },
    ),

    "aadhaar_verify_otp": APIConfig(
        name="aadhaar_verify_otp",
        endpoint="/kyc/aadhaar/otp/verify",
        method="POST",
        payload_template={
            "otp_reference": "{{AADHAAR_OTP_REF}}",
            "otp": "{{AADHAAR_OTP}}",
            "consent": True,
        },
        response_mappings={
            "data.verified": "AADHAAR_VERIFIED",
            "data.name": "AADHAAR_NAME",
            "data.address.full": "ADDRESS",
            "data.address.city": "CITY",
            "data.address.state": "STATE",
            "data.address.pincode": "PINCODE",
            "data.dob": "AADHAAR_DOB",
        },
    ),

    "kyc_completion": APIConfig(
        name="kyc_completion",
        endpoint="/kyc/submit",
        method="POST",
        payload_template={
            "pan": "{{PAN_NUMBER}}",
            "aadhaar": "{{AADHAAR_NUMBER}}",
            "name": "{{FULL_NAME}}",
            "dob": "{{PAN_DOB}}",
            "mobile": "{{MOBILE}}",
            "email": "{{EMAIL}}",
            "address": "{{ADDRESS}}",
            "city": "{{CITY}}",
            "state": "{{STATE}}",
            "pincode": "{{PINCODE}}",
            "customer_reference": "{{CRN}}",
        },
        response_mappings={
            "data.kyc_id": "KYC_ID",
            "data.status": "KYC_STATUS",
            "data.kyc_reference": "KYC_REFERENCE",
        },
    ),

    "credit_check": APIConfig(
        name="credit_check",
        endpoint="/credit/bureau/check",
        method="POST",
        payload_template={
            "pan": "{{PAN_NUMBER}}",
            "name": "{{FULL_NAME}}",
            "dob": "{{PAN_DOB}}",
            "mobile": "{{MOBILE}}",
            "loan_amount": "{{LOAN_AMOUNT}}",
            "consent": True,
            "bureau": "CIBIL",
        },
        response_mappings={
            "data.score": "CREDIT_SCORE",
            "data.eligible": "CREDIT_ELIGIBLE",
            "data.max_limit": "CREDIT_LIMIT",
            "data.bureau_id": "BUREAU_ID",
            "data.report_date": "BUREAU_DATE",
        },
    ),

    "bank_verification": APIConfig(
        name="bank_verification",
        endpoint="/bank/verify/penny-drop",
        method="POST",
        payload_template={
            "account_number": "{{BANK_ACCOUNT}}",
            "ifsc": "{{IFSC}}",
            "account_holder_name": "{{FULL_NAME}}",
            "request_id": "{{REQID}}",
        },
        response_mappings={
            "data.verified": "BANK_VERIFIED",
            "data.account_holder": "ACCOUNT_HOLDER_NAME",
            "data.bank_name": "BANK_NAME",
            "data.branch": "BANK_BRANCH",
            "data.micr": "MICR",
        },
    ),

    "loan_creation": APIConfig(
        name="loan_creation",
        endpoint="/loans/create",
        method="POST",
        payload_template={
            "customer_reference": "{{CRN}}",
            "kyc_id": "{{KYC_ID}}",
            "bureau_id": "{{BUREAU_ID}}",
            "loan_amount": "{{LOAN_AMOUNT}}",
            "tenure_months": "{{TENURE_MONTHS}}",
            "loan_type": "{{LOAN_TYPE}}",
            "purpose": "{{LOAN_PURPOSE}}",
            "bank_account": "{{BANK_ACCOUNT}}",
            "ifsc": "{{IFSC}}",
        },
        response_mappings={
            "data.loan_id": "LOAN_ID",
            "data.reference": "LOAN_REFERENCE",
            "data.status": "LOAN_STATUS",
            "data.interest_rate": "INTEREST_RATE",
            "data.emi_amount": "EMI_AMOUNT",
            "data.processing_fee": "PROCESSING_FEE",
            "data.total_repayment": "TOTAL_REPAYMENT",
        },
    ),

    "loan_agreement": APIConfig(
        name="loan_agreement",
        endpoint="/loans/{{LOAN_ID}}/agreement",
        method="POST",
        payload_template={
            "loan_id": "{{LOAN_ID}}",
            "consent": True,
            "esign_consent": True,
            "estamp_consent": True,
        },
        response_mappings={
            "data.agreement_id": "AGREEMENT_ID",
            "data.status": "AGREEMENT_STATUS",
            "data.agreement_url": "AGREEMENT_URL",
            "data.signed_at": "AGREEMENT_SIGNED_AT",
        },
    ),

    "disbursement": APIConfig(
        name="disbursement",
        endpoint="/loans/{{LOAN_ID}}/disburse",
        method="POST",
        payload_template={
            "loan_id": "{{LOAN_ID}}",
            "amount": "{{LOAN_AMOUNT}}",
            "bank_account": "{{BANK_ACCOUNT}}",
            "ifsc": "{{IFSC}}",
            "request_id": "{{REQID}}",
            "mode": "IMPS",
        },
        response_mappings={
            "data.status": "DISBURSEMENT_STATUS",
            "data.utr": "UTR",
            "data.disbursed_amount": "DISBURSED_AMOUNT",
            "data.disbursed_at": "DISBURSED_AT",
        },
    ),

    "emandate_registration": APIConfig(
        name="emandate_registration",
        endpoint="/mandate/register",
        method="POST",
        payload_template={
            "loan_id": "{{LOAN_ID}}",
            "account_number": "{{BANK_ACCOUNT}}",
            "ifsc": "{{IFSC}}",
            "account_holder": "{{ACCOUNT_HOLDER_NAME}}",
            "emi_amount": "{{EMI_AMOUNT}}",
            "frequency": "monthly",
            "start_date": "{{MANDATE_START_DATE}}",
            "end_date": "{{MANDATE_END_DATE}}",
        },
        response_mappings={
            "data.mandate_id": "MANDATE_ID",
            "data.status": "MANDATE_STATUS",
            "data.mandate_url": "MANDATE_URL",
            "data.umrn": "UMRN",
        },
    ),
}


def get_api_config(step_name: str) -> Optional[APIConfig]:
    """Get API config for a step."""
    return STEP_API_CONFIGS.get(step_name)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StepResult:
    """Result of executing a single step."""
    step_name: str
    success: bool
    status_code: int = 0
    response_data: Dict = field(default_factory=dict)
    extracted_data: Dict = field(default_factory=dict)
    error: str = ""
    elapsed_ms: float = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {
            "step_name": self.step_name,
            "success": self.success,
            "status_code": self.status_code,
            "response_data": self.response_data,
            "extracted_data": self.extracted_data,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class ExecutionResult:
    """Result of flow execution."""
    plan_id: str
    session_id: str
    success: bool
    completed_steps: List[StepResult] = field(default_factory=list)
    current_step: str = ""
    current_step_index: int = 0
    state: FlowState = FlowState.EXECUTING
    error: str = ""
    waiting_for_input: List[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "success": self.success,
            "completed_steps": [s.to_dict() for s in self.completed_steps],
            "current_step": self.current_step,
            "current_step_index": self.current_step_index,
            "state": self.state.value,
            "error": self.error,
            "waiting_for_input": self.waiting_for_input,
            "message": self.message,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# THE EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════

class FlowExecutor:
    """
    The Executor handles:
      1. Executing confirmed plans from the Planner
      2. Making API calls with {{FIELD}} template substitution
      3. Applying data changes to API_LOGS
      4. Updating API_LOGS with response data
      5. Pausing when user input is needed

    IMPORTANT: The Executor will REFUSE to execute plans that are not CONFIRMED.
    """

    def __init__(
        self,
        api_logs_store,
        debug_log_dir: str = "./execution_logs",
        dry_run: bool = False,
    ):
        """
        Args:
            api_logs_store: Storage for API_LOGS (must have save_session/load_session)
            debug_log_dir: Directory for execution debug logs
            dry_run: If True, don't make actual API calls (for testing)
        """
        self.store = api_logs_store
        self.debug_log_dir = Path(debug_log_dir)
        self.debug_log_dir.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run

        # Track execution state per session for resume
        self._execution_state: Dict[str, ExecutionResult] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    async def execute(
        self,
        plan: FlowPlan,
        api_logs: Dict[str, Any],
    ) -> ExecutionResult:
        """
        Execute a flow plan.

        IMPORTANT: Plan MUST be in CONFIRMED state. If not, execution is refused.

        Args:
            plan: The FlowPlan from Planner (must be CONFIRMED)
            api_logs: Current API_LOGS state

        Returns:
            ExecutionResult with success status, completed steps, etc.
        """
        session_id = plan.session_id

        # Initialize result
        result = ExecutionResult(
            plan_id=plan.plan_id,
            session_id=session_id,
            success=False,
        )

        # ─── CHECK STATE ───
        if plan.state != FlowState.CONFIRMED:
            result.error = f"Cannot execute: Plan state is {plan.state.value}, must be CONFIRMED"
            result.state = FlowState.FAILED
            result.message = "❌ Plan not confirmed. Please confirm first."
            return result

        # ─── ROUTE BY ACTION TYPE ───
        if plan.action_type == ActionType.DATA_CHANGE:
            return await self._execute_data_change(plan, api_logs, result)

        elif plan.action_type in (ActionType.FLOW_EXECUTION, ActionType.CUSTOM_FLOW):
            return await self._execute_flow(plan, api_logs, result)

        else:
            result.error = f"Cannot execute action type: {plan.action_type.value}"
            result.state = FlowState.FAILED
            return result

    # ─────────────────────────────────────────────────────────────────────────
    # DATA CHANGE EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_data_change(
        self,
        plan: FlowPlan,
        api_logs: Dict[str, Any],
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Apply confirmed data changes to API_LOGS."""

        session_id = plan.session_id

        # Apply changes
        changes_made = []
        for key, value in plan.data_changes.items():
            old_value = api_logs.get(key, "not set")
            api_logs[key] = value
            changes_made.append(f"  • {key}: {old_value} → {value}")

        # Track in execution trace
        if "EXECUTION_TRACE" not in api_logs:
            api_logs["EXECUTION_TRACE"] = []

        api_logs["EXECUTION_TRACE"].append({
            "type": "data_change",
            "action": "update",
            "changes": plan.data_changes,
            "old_values": plan.old_values,
            "timestamp": datetime.now().isoformat(),
            "plan_id": plan.plan_id,
        })

        # Save
        self.store.save_session(session_id, api_logs)

        # Update result
        result.success = True
        result.state = FlowState.COMPLETED
        result.message = f"✅ Data updated successfully:\n" + "\n".join(changes_made)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # FLOW EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_flow(
        self,
        plan: FlowPlan,
        api_logs: Dict[str, Any],
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Execute flow steps one by one."""

        session_id = plan.session_id
        partner = plan.partner
        base_url = PARTNER_BASE_URLS.get(partner, "")

        # Initialize execution trace if needed
        if "EXECUTION_TRACE" not in api_logs:
            api_logs["EXECUTION_TRACE"] = []

        # Mark flow as executing
        plan.state = FlowState.EXECUTING
        result.state = FlowState.EXECUTING

        # ─── Execute Steps ───
        for i, step in enumerate(plan.steps):
            # Skip completed/skipped steps
            if step.status in ("completed", "skipped"):
                continue

            result.current_step = step.name
            result.current_step_index = i

            # ─── Special handling for requirement_gathering ───
            if step.name == "requirement_gathering":
                # Check if we have all required data
                required = ["FULL_NAME", "PAN_NUMBER", "PAN_DOB", "MOBILE"]
                missing = [f for f in required if not api_logs.get(f)]

                if missing:
                    result.waiting_for_input = missing
                    result.state = FlowState.PAUSED
                    result.message = f"⏸️ Paused: Need input for {', '.join(missing)}\n\nUse collect_requirements tool to provide data."
                    self._execution_state[session_id] = result
                    return result

                # Data already collected
                step.status = "completed"
                step.result = {"collected": True}
                plan.mark_step_completed(step.name, step.result)
                continue

            # ─── Get API config ───
            api_config = get_api_config(step.name)

            if not api_config:
                # No API for this step, skip
                step.status = "skipped"
                continue

            # ─── Check required inputs ───
            required_fields = api_config.get_required_fields()
            missing = self._get_missing_inputs(list(required_fields), api_logs)

            if missing:
                # Pause execution - need user input
                result.waiting_for_input = missing
                result.current_step_index = i
                result.state = FlowState.PAUSED
                result.message = f"⏸️ Paused at step '{step.name}': Missing {', '.join(missing)}"
                self._execution_state[session_id] = result
                self.store.save_session(session_id, api_logs)
                return result

            # ─── Execute API ───
            step_result = await self._execute_api(
                api_config=api_config,
                api_logs=api_logs,
                base_url=base_url,
                session_id=session_id,
            )

            result.completed_steps.append(step_result)

            if step_result.success:
                # Update step status
                step.status = "completed"
                step.result = step_result.extracted_data

                # Update API_LOGS with extracted data
                for key, value in step_result.extracted_data.items():
                    api_logs[key] = value

                # Track in execution trace
                api_logs["EXECUTION_TRACE"].append({
                    "type": "api_call",
                    "step": step.name,
                    "success": True,
                    "status_code": step_result.status_code,
                    "extracted": step_result.extracted_data,
                    "elapsed_ms": step_result.elapsed_ms,
                    "timestamp": step_result.timestamp,
                    "plan_id": plan.plan_id,
                })

                # Save after each successful step
                self.store.save_session(session_id, api_logs)

            else:
                # Step failed
                step.status = "failed"
                step.error = step_result.error

                api_logs["EXECUTION_TRACE"].append({
                    "type": "api_call",
                    "step": step.name,
                    "success": False,
                    "error": step_result.error,
                    "status_code": step_result.status_code,
                    "timestamp": step_result.timestamp,
                    "plan_id": plan.plan_id,
                })

                self.store.save_session(session_id, api_logs)

                # Stop execution on failure
                result.error = f"Step '{step.name}' failed: {step_result.error}"
                result.state = FlowState.FAILED
                result.message = f"❌ Execution failed at step '{step.name}':\n{step_result.error}"
                return result

        # ─── All Steps Completed ───
        result.success = True
        result.state = FlowState.COMPLETED

        completed_names = [s.step_name for s in result.completed_steps]
        result.message = f"✅ Flow completed successfully!\n\nCompleted steps: {', '.join(completed_names)}"

        # Clear execution state
        self._execution_state.pop(session_id, None)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # API EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    async def _execute_api(
        self,
        api_config: APIConfig,
        api_logs: Dict[str, Any],
        base_url: str,
        session_id: str,
    ) -> StepResult:
        """Execute a single API call."""

        # Build URL
        url = self._substitute(api_config.endpoint, api_logs)
        full_url = f"{base_url}{url}"

        # Build payload
        payload = self._substitute_dict(api_config.payload_template, api_logs)

        # Build headers
        headers = {"Content-Type": "application/json"}
        if api_config.headers_template:
            headers.update(self._substitute_dict(api_config.headers_template, api_logs))

        # Log request
        log_entry = {
            "step": api_config.name,
            "timestamp": datetime.now().isoformat(),
            "request": {
                "url": full_url,
                "method": api_config.method,
                "payload": payload,
                "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
            },
        }

        # ─── DRY RUN MODE ───
        if self.dry_run:
            # Return mock success
            mock_extracted = {}
            for response_path, api_key in api_config.response_mappings.items():
                # Generate mock values
                if "verified" in api_key.lower():
                    mock_extracted[api_key] = True
                elif "score" in api_key.lower():
                    mock_extracted[api_key] = 750
                elif "id" in api_key.lower():
                    mock_extracted[api_key] = f"mock_{uuid.uuid4().hex[:8]}"
                elif "status" in api_key.lower():
                    mock_extracted[api_key] = "success"
                elif "amount" in api_key.lower():
                    mock_extracted[api_key] = api_logs.get("LOAN_AMOUNT", 50000)
                else:
                    mock_extracted[api_key] = f"mock_{api_key.lower()}"

            log_entry["response"] = {"mock": True, "data": mock_extracted}
            log_entry["extracted"] = mock_extracted
            self._write_log(session_id, log_entry)

            return StepResult(
                step_name=api_config.name,
                success=True,
                status_code=200,
                response_data={"mock": True, "data": mock_extracted},
                extracted_data=mock_extracted,
                elapsed_ms=50,
            )

        # ─── REAL API CALL ───
        if not HTTPX_AVAILABLE:
            return StepResult(
                step_name=api_config.name,
                success=False,
                error="httpx not installed. Install with: pip install httpx",
            )

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                start_time = datetime.now()

                response = await client.request(
                    method=api_config.method,
                    url=full_url,
                    json=payload,
                    headers=headers,
                )

                elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

                # Parse response
                try:
                    response_data = response.json()
                except Exception:
                    response_data = {"raw": response.text}

                log_entry["response"] = {
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "body": response_data,
                }

                # Extract data from response
                extracted = {}
                if response.status_code < 400:
                    for response_path, api_key in api_config.response_mappings.items():
                        value = self._extract_path(response_data, response_path)
                        if value is not None:
                            extracted[api_key] = value

                log_entry["extracted"] = extracted
                self._write_log(session_id, log_entry)

                success = response.status_code < 400
                error = "" if success else response_data.get("error", {}).get("message", f"HTTP {response.status_code}")

                return StepResult(
                    step_name=api_config.name,
                    success=success,
                    status_code=response.status_code,
                    response_data=response_data,
                    extracted_data=extracted,
                    error=error,
                    elapsed_ms=elapsed_ms,
                )

        except Exception as e:
            log_entry["error"] = str(e)
            self._write_log(session_id, log_entry)

            return StepResult(
                step_name=api_config.name,
                success=False,
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # RESUME EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    async def resume(
        self,
        session_id: str,
        plan: FlowPlan,
        api_logs: Dict[str, Any],
    ) -> ExecutionResult:
        """
        Resume a paused execution after user provides missing input.

        Args:
            session_id: Session to resume
            plan: The original plan
            api_logs: Updated API_LOGS (should now have the missing fields)

        Returns:
            ExecutionResult (continues from where it paused)
        """
        prev_result = self._execution_state.get(session_id)

        if not prev_result:
            # No previous state, start fresh
            plan.state = FlowState.CONFIRMED
            return await self.execute(plan, api_logs)

        # Check if missing inputs are now available
        still_missing = self._get_missing_inputs(prev_result.waiting_for_input, api_logs)

        if still_missing:
            prev_result.waiting_for_input = still_missing
            prev_result.message = f"⏸️ Still waiting for: {', '.join(still_missing)}"
            return prev_result

        # Clear waiting state and continue
        prev_result.waiting_for_input = []
        plan.state = FlowState.CONFIRMED

        return await self.execute(plan, api_logs)

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER METHODS
    # ─────────────────────────────────────────────────────────────────────────

    def _get_missing_inputs(self, required: List[str], api_logs: Dict) -> List[str]:
        """Get list of missing required inputs."""
        missing = []
        for field in required:
            value = api_logs.get(field)
            if value is None or value == "":
                missing.append(field)
        return missing

    def _substitute(self, template: str, api_logs: Dict) -> str:
        """Substitute {{FIELD}} placeholders in string."""
        pattern = re.compile(r"\{\{([A-Z_]+)\}\}")

        def replace(match):
            field = match.group(1)
            value = api_logs.get(field)
            if value is None:
                return ""
            return str(value)

        return pattern.sub(replace, template)

    def _substitute_dict(self, template: Dict, api_logs: Dict) -> Dict:
        """Recursively substitute placeholders in dict."""
        result = {}
        for key, value in template.items():
            if isinstance(value, dict):
                result[key] = self._substitute_dict(value, api_logs)
            elif isinstance(value, list):
                result[key] = [
                    self._substitute_dict(v, api_logs) if isinstance(v, dict)
                    else self._substitute(v, api_logs) if isinstance(v, str)
                    else v
                    for v in value
                ]
            elif isinstance(value, str):
                result[key] = self._substitute(value, api_logs)
            else:
                result[key] = value
        return result

    def _extract_path(self, data: Dict, path: str) -> Any:
        """Extract value from nested dict using dot notation."""
        keys = path.split(".")
        value = data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit():
                idx = int(key)
                value = value[idx] if idx < len(value) else None
            else:
                return None
            if value is None:
                return None
        return value

    def _write_log(self, session_id: str, entry: Dict):
        """Write entry to debug log file."""
        log_file = self.debug_log_dir / f"{session_id}_execution.log"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 70 + "\n")
                f.write(json.dumps(entry, indent=2, default=str))
                f.write("\n")
        except Exception:
            pass  # Don't fail execution due to logging errors

    def get_execution_state(self, session_id: str) -> Optional[ExecutionResult]:
        """Get current execution state for a session."""
        return self._execution_state.get(session_id)

    def clear_execution_state(self, session_id: str):
        """Clear execution state for a session."""
        self._execution_state.pop(session_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Core classes
    "FlowExecutor",
    "ExecutionResult",
    "StepResult",

    # Config
    "APIConfig",
    "STEP_API_CONFIGS",
    "PARTNER_BASE_URLS",

    # Helper
    "get_api_config",
]
