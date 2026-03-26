"""Flow Registry — partner flows and step catalog."""
from dataclasses import dataclass, field
from typing import Any

@dataclass
class FlowStep:
    name: str
    api_endpoint: str = ""
    required_inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    description: str = ""

PARTNER_FLOWS: dict[str, list[str]] = {
    "CRED": ["pan", "kyc", "loanonboarding"],
    "PAYTM": ["aadhaar", "pan", "kyc", "creditcheck", "loanonboarding"],
    "PHONEPE": ["pan", "kyc", "bankverification", "loanonboarding"],
    "RAZORPAY": ["pan", "kyc", "merchantverification", "loanonboarding", "disbursement"],
    "SLICE": ["pan", "kyc", "creditcheck", "loanonboarding", "emandate"],
}

STEP_CATALOG: dict[str, FlowStep] = {
    "pan": FlowStep(name="pan", api_endpoint="/api/v1/verify/pan",
        required_inputs=["pan_number"], outputs=["pan_verified", "pan_name"],
        description="PAN card verification"),
    "aadhaar": FlowStep(name="aadhaar", api_endpoint="/api/v1/verify/aadhaar",
        required_inputs=["aadhaar_number"], outputs=["aadhaar_verified"],
        description="Aadhaar verification via OTP"),
    "kyc": FlowStep(name="kyc", api_endpoint="/api/v1/kyc/complete",
        required_inputs=["pan_verified", "user_details"], outputs=["kyc_status", "kyc_id"],
        description="Full KYC completion"),
    "creditcheck": FlowStep(name="creditcheck", api_endpoint="/api/v1/credit/check",
        required_inputs=["pan_number", "kyc_id"], outputs=["credit_score", "credit_eligible"],
        description="Credit bureau check"),
    "bankverification": FlowStep(name="bankverification", api_endpoint="/api/v1/bank/verify",
        required_inputs=["account_number", "ifsc"], outputs=["bank_verified"],
        description="Bank account penny-drop verification"),
    "merchantverification": FlowStep(name="merchantverification", api_endpoint="/api/v1/merchant/verify",
        required_inputs=["merchant_id", "gstin"], outputs=["merchant_verified"],
        description="Merchant identity verification"),
    "loanonboarding": FlowStep(name="loanonboarding", api_endpoint="/api/v1/loan/onboard",
        required_inputs=["kyc_id", "loan_amount", "loan_id"], outputs=["loan_status", "loan_reference"],
        description="Loan application onboarding"),
    "disbursement": FlowStep(name="disbursement", api_endpoint="/api/v1/loan/disburse",
        required_inputs=["loan_reference", "bank_verified"], outputs=["disbursement_status", "utr"],
        description="Loan amount disbursement"),
    "emandate": FlowStep(name="emandate", api_endpoint="/api/v1/mandate/register",
        required_inputs=["loan_reference", "bank_verified"], outputs=["mandate_id", "mandate_status"],
        description="E-mandate registration for EMI auto-debit"),
}

def get_partner_flow(partner: str) -> list[str]:
    p = partner.upper().strip()
    if p not in PARTNER_FLOWS:
        raise ValueError(f"Unknown partner '{partner}'. Available: {list(PARTNER_FLOWS.keys())}")
    return PARTNER_FLOWS[p].copy()

def get_step_metadata(step_name: str) -> FlowStep | None:
    return STEP_CATALOG.get(step_name.lower().strip())

def list_partners() -> list[str]:
    return list(PARTNER_FLOWS.keys())
