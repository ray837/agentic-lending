"""
Test Suite for Flow Planner
Run: python test_planner.py
"""

import sys
import json

from planner_mcp_server import FlowPlanner
from flow_planner import get_partner_flow, list_partners


planner = FlowPlanner(use_llm=False)


def test(name: str, partner: str, query: str, expected_names: list[str]):
    plan = planner.plan_sync(partner, query)
    actual = plan.get_step_names()
    status = "PASS" if actual == expected_names else "FAIL"
    print(f"\n  [{status}] {name}")
    print(f"    Query:    \"{query}\"")
    print(f"    Original: {plan.original_flow}")
    print(f"    Expected: {expected_names}")
    print(f"    Actual:   {actual}")
    for m in plan.modifications:
        print(f"    Mod: {m}")
    return actual == expected_names


print("=" * 60)
print("FLOW PLANNER TEST SUITE")
print("=" * 60)

results = []

# Test 1: Default flow
results.append(test(
    "Default CRED flow",
    "CRED", "run the default flow",
    ["pan", "kyc", "loanonboarding"],
))

# Test 2: Reorder — kyc after loanonboarding
results.append(test(
    "Reorder KYC after loanonboarding",
    "CRED", "do kyc after loanonboarding",
    ["pan", "loanonboarding", "kyc"],
))

# Test 3: Data change
results.append(test(
    "Data change before loanonboarding",
    "CRED", "change loanid before loanonboarding",
    ["pan", "kyc", "change(loanid)", "loanonboarding"],
))

# Test 4: Combined
results.append(test(
    "Combined: data change + reorder",
    "CRED",
    "change loanid before loanonboarding and do kyc after loanonboarding",
    ["pan", "change(loanid)", "loanonboarding", "kyc"],
))

# Test 5: Remove step
results.append(test(
    "Skip creditcheck from PAYTM",
    "PAYTM", "skip creditcheck",
    ["aadhaar", "pan", "kyc", "loanonboarding"],
))

# Test 6: Move before
results.append(test(
    "Move kyc before pan",
    "CRED", "put kyc before pan",
    ["kyc", "pan", "loanonboarding"],
))

# Test 7: Add step
results.append(test(
    "Add creditcheck after pan for CRED",
    "CRED", "add creditcheck after pan",
    ["pan", "creditcheck", "kyc", "loanonboarding"],
))

# Test 8: Full JSON output
print(f"\n{'=' * 60}")
print("EXECUTION PLAN JSON OUTPUT:")
plan = planner.plan_sync(
    "CRED",
    "change loanid before loanonboarding and do kyc after loanonboarding",
)
print(plan.to_json())

# Test 9: List partners
print(f"\n{'=' * 60}")
print("AVAILABLE PARTNERS:")
for p in list_partners():
    flow = get_partner_flow(p)
    print(f"  {p}: {' -> '.join(flow)}")

# Summary
print(f"\n{'=' * 60}")
passed = sum(results)
total = len(results)
print(f"RESULTS: {passed}/{total} passed")

if passed == total:
    print("All tests passed!")
else:
    print(f"{total - passed} test(s) failed.")
    sys.exit(1)
