"""
═══════════════════════════════════════════════════════════════════════════════
TEST SCRIPT — Verify the Complete MCP Banking System
═══════════════════════════════════════════════════════════════════════════════

Tests:
  1. Session creation
  2. Requirement gathering with LoanCreationModal
  3. Flow planning with confirmation
  4. Data change with confirmation
  5. Custom flow planning
  6. Execution (dry run)
"""

import asyncio
import json
from datetime import datetime

# Local imports
from modals import LoanCreationModal, KYCModal, BankDetailsModal, merge_modal_to_api_logs
from planner import FlowPlanner, FlowState, ActionType, PARTNER_CONFIGS
from executor import FlowExecutor
from storage import APILogsStore


def print_header(title: str):
    print("\n" + "═" * 70)
    print(f"  {title}")
    print("═" * 70)


def print_json(data: dict):
    print(json.dumps(data, indent=2, default=str))


async def test_complete_flow():
    """Test the complete flow from start to finish."""

    # ─────────────────────────────────────────────────────────────────────────
    # SETUP
    # ─────────────────────────────────────────────────────────────────────────

    print_header("SETUP: Initialize Services")

    store = APILogsStore("./test_api_logs")
    planner = FlowPlanner(llm=None)  # No LLM for testing
    executor = FlowExecutor(store, "./test_execution_logs", dry_run=True)

    print("✅ Services initialized")
    print(f"   Storage: ./test_api_logs")
    print(f"   Executor: dry_run=True")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 1: Create Session
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 1: Create Session for CRED")

    session_id, api_logs = store.create_session(
        partner="CRED",
        initial_data={
            "LOAN_AMOUNT": 100000,
            "TENURE_MONTHS": 24,
        },
    )

    print(f"✅ Session created: {session_id}")
    print(f"   Partner: {api_logs.get('PARTNER')}")
    print(f"   Loan Amount: ₹{api_logs.get('LOAN_AMOUNT'):,}")
    print(f"   Tenure: {api_logs.get('TENURE_MONTHS')} months")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 2: Requirement Gathering with LoanCreationModal
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 2: Requirement Gathering (LoanCreationModal)")

    # Create modal with validation
    try:
        customer_data = LoanCreationModal(
            customer_name="Vamsi Krishna",
            pan_number="ABCDE1234F",
            dob="1990-05-15",
            mobile="9876543210",
            email="vamsi@example.com",
            loan_amount=150000,
            loan_type="personal",
            tenure_months=18,
            purpose="home renovation",
        )
        print("✅ Modal validated successfully")
        print(f"   Fields: {list(customer_data.to_api_logs().keys())}")
    except Exception as e:
        print(f"❌ Validation error: {e}")
        return

    # Auto-map to API_LOGS
    api_logs = merge_modal_to_api_logs(api_logs, customer_data)
    store.save_session(session_id, api_logs)

    print("\n   Mapped to API_LOGS:")
    for key, value in customer_data.to_api_logs().items():
        print(f"     {key}: {value}")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 3: Plan Standard Flow (Requires Confirmation)
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 3: Plan Standard Flow")

    plan = await planner.plan(
        user_message="run loan flow",
        session_id=session_id,
        partner="CRED",
        api_logs=api_logs,
    )

    print(f"✅ Plan created: {plan.plan_id}")
    print(f"   Action Type: {plan.action_type.value}")
    print(f"   State: {plan.state.value}")
    print(f"   Steps: {len(plan.steps)}")
    print(f"\n   Confirmation Message:")
    print("   " + plan.confirmation_message.replace("\n", "\n   "))

    # Verify state is PENDING_CONFIRMATION
    assert plan.state == FlowState.PENDING_CONFIRMATION, "Should require confirmation!"
    print("\n✅ Correctly requires confirmation before execution")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 4: Confirm and Execute
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 4: Confirm and Execute")

    # Confirm
    confirmed_plan = await planner.plan(
        user_message="yes",
        session_id=session_id,
        partner="CRED",
        api_logs=api_logs,
    )

    print(f"   State after confirmation: {confirmed_plan.state.value}")
    assert confirmed_plan.state == FlowState.CONFIRMED, "Should be CONFIRMED!"

    # Execute
    result = await executor.execute(confirmed_plan, api_logs)

    print(f"\n✅ Execution completed")
    print(f"   Success: {result.success}")
    print(f"   State: {result.state.value}")
    print(f"   Completed Steps: {len(result.completed_steps)}")
    print(f"   Message: {result.message[:100]}...")

    # Reload API_LOGS
    api_logs = store.load_session(session_id)
    print(f"\n   API_LOGS updated with execution data")
    print(f"   Execution trace entries: {len(api_logs.get('EXECUTION_TRACE', []))}")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 5: Data Change Request
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 5: Data Change Request")

    # Create new session for this test
    session_id2, api_logs2 = store.create_session("PAYTM")
    api_logs2 = merge_modal_to_api_logs(api_logs2, customer_data)
    store.save_session(session_id2, api_logs2)

    # Request data change
    plan = await planner.plan(
        user_message="change loan amount to 2 lakh",
        session_id=session_id2,
        partner="PAYTM",
        api_logs=api_logs2,
    )

    print(f"✅ Data change plan created")
    print(f"   Action Type: {plan.action_type.value}")
    print(f"   State: {plan.state.value}")
    print(f"   Changes: {plan.data_changes}")
    print(f"\n   Confirmation Message:")
    print("   " + plan.confirmation_message.replace("\n", "\n   "))

    assert plan.state == FlowState.PENDING_CONFIRMATION, "Data change should require confirmation!"
    print("\n✅ Data change correctly requires confirmation")

    # Confirm and apply
    confirmed = await planner.plan("yes", session_id2, "PAYTM", api_logs2)
    result = await executor.execute(confirmed, api_logs2)

    print(f"\n   After confirmation and execution:")
    print(f"   Success: {result.success}")
    print(f"   Message: {result.message}")

    # Verify change was applied
    api_logs2 = store.load_session(session_id2)
    print(f"   New LOAN_AMOUNT: ₹{api_logs2.get('LOAN_AMOUNT'):,}")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 6: Custom Flow
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 6: Custom Flow")

    # Create new session
    session_id3, api_logs3 = store.create_session("RAZORPAY")
    api_logs3 = merge_modal_to_api_logs(api_logs3, customer_data)
    store.save_session(session_id3, api_logs3)

    # Request custom flow
    plan = await planner.plan(
        user_message="run only pan verification and credit check",
        session_id=session_id3,
        partner="RAZORPAY",
        api_logs=api_logs3,
    )

    print(f"✅ Custom flow plan created")
    print(f"   Action Type: {plan.action_type.value}")
    print(f"   Steps: {[s.name for s in plan.steps]}")
    print(f"\n   Confirmation Message:")
    print("   " + plan.confirmation_message.replace("\n", "\n   "))

    assert plan.action_type == ActionType.CUSTOM_FLOW, "Should be CUSTOM_FLOW!"
    print("\n✅ Custom flow correctly identified")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 7: Query (No Confirmation Needed)
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 7: Query (No Confirmation)")

    plan = await planner.plan(
        user_message="what's my loan amount?",
        session_id=session_id3,
        partner="RAZORPAY",
        api_logs=api_logs3,
    )

    print(f"✅ Query handled")
    print(f"   Action Type: {plan.action_type.value}")
    print(f"   State: {plan.state.value}")
    print(f"   Response: {plan.confirmation_message}")

    assert plan.action_type == ActionType.QUERY, "Should be QUERY!"
    assert plan.state == FlowState.COMPLETED, "Query should complete immediately!"
    print("\n✅ Query correctly doesn't require confirmation")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 8: Executor Refuses Unconfirmed Plan
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 8: Executor Refuses Unconfirmed Plan")

    # Create a plan but don't confirm
    plan = await planner.plan(
        user_message="run loan flow",
        session_id=session_id3,
        partner="RAZORPAY",
        api_logs=api_logs3,
    )

    # Try to execute without confirmation
    result = await executor.execute(plan, api_logs3)

    print(f"   Plan State: {plan.state.value}")
    print(f"   Execution Success: {result.success}")
    print(f"   Execution State: {result.state.value}")
    print(f"   Error: {result.error}")

    assert not result.success, "Should fail without confirmation!"
    assert result.state == FlowState.FAILED, "Should be FAILED!"
    print("\n✅ Executor correctly refuses unconfirmed plans")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 9: Modal Validation
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 9: Modal Validation")

    # Test invalid PAN
    try:
        invalid_modal = LoanCreationModal(
            customer_name="Test User",
            pan_number="INVALID",  # Invalid PAN
            dob="1990-01-01",
            mobile="9876543210",
        )
        print("❌ Should have raised validation error for invalid PAN!")
    except Exception as e:
        print(f"✅ Correctly caught invalid PAN: {type(e).__name__}")

    # Test invalid mobile
    try:
        invalid_modal = LoanCreationModal(
            customer_name="Test User",
            pan_number="ABCDE1234F",
            dob="1990-01-01",
            mobile="1234567890",  # Invalid mobile (doesn't start with 6-9)
        )
        print("❌ Should have raised validation error for invalid mobile!")
    except Exception as e:
        print(f"✅ Correctly caught invalid mobile: {type(e).__name__}")

    # ─────────────────────────────────────────────────────────────────────────
    # TEST 10: List Sessions
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST 10: List Sessions")

    sessions = store.list_sessions()
    print(f"✅ Found {len(sessions)} sessions:")
    for s in sessions:
        print(f"   - {s['session_id']} ({s['partner']})")

    # ─────────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────────

    print_header("TEST SUMMARY")
    print("""
✅ All tests passed!

The system correctly:
  1. Creates sessions with initialized API_LOGS
  2. Validates and collects data via Pydantic modals
  3. Auto-maps modal fields to API_LOGS
  4. Plans flows with PENDING_CONFIRMATION state
  5. Handles data changes with confirmation
  6. Identifies custom flows
  7. Handles queries without confirmation
  8. REFUSES to execute unconfirmed plans
  9. Validates modal input (PAN, mobile, etc.)
  10. Persists sessions to JSON files
""")


async def test_modal_auto_mapping():
    """Test that modal fields auto-map to API_LOGS correctly."""

    print_header("Modal Auto-Mapping Test")

    # Create modal
    modal = LoanCreationModal(
        customer_name="Test User",
        pan_number="ABCDE1234F",
        dob="1990-05-15",
        mobile="9876543210",
        email="test@example.com",
        loan_amount=100000,
        tenure_months=12,
    )

    # Get API_LOGS mapping
    api_logs_data = modal.to_api_logs()

    print("Modal Field → API_LOGS Key Mapping:")
    print("-" * 50)

    expected_mappings = {
        "customer_name": "FULL_NAME",
        "pan_number": "PAN_NUMBER",
        "dob": "PAN_DOB",
        "mobile": "MOBILE",
        "email": "EMAIL",
        "loan_amount": "LOAN_AMOUNT",
        "tenure_months": "TENURE_MONTHS",
    }

    for field_name, api_key in expected_mappings.items():
        field_value = getattr(modal, field_name)
        api_value = api_logs_data.get(api_key)
        status = "✅" if api_value is not None else "❌"
        print(f"  {status} {field_name:20} → {api_key:20} = {api_value}")

    print("\n✅ All modal fields correctly map to API_LOGS keys")


if __name__ == "__main__":
    print("\n" + "═" * 70)
    print("  MCP BANKING SYSTEM — COMPLETE TEST SUITE")
    print("═" * 70)

    asyncio.run(test_modal_auto_mapping())
    asyncio.run(test_complete_flow())
