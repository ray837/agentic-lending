"""
═══════════════════════════════════════════════════════════════════════════════
MCP PARAMETER MODALS — Pydantic Models for User Input Collection
═══════════════════════════════════════════════════════════════════════════════

These models are used as MCP tool parameters and automatically map to API_LOGS.

Usage:
    @mcp.tool()
    def requirement_gathering(customer_data: LoanCreationModal) -> dict:
        api_logs = customer_data.to_api_logs()  # Auto-maps to API_LOGS format
        return {"collected": api_logs}
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any, Dict, List
from enum import Enum
from datetime import date, datetime
import re


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class LoanType(str, Enum):
    PERSONAL = "personal"
    BUSINESS = "business"
    HOME = "home"
    VEHICLE = "vehicle"
    EDUCATION = "education"
    CREDIT_LINE = "credit_line"


class Partner(str, Enum):
    CRED = "CRED"
    PAYTM = "PAYTM"
    PHONEPE = "PHONEPE"
    RAZORPAY = "RAZORPAY"
    SLICE = "SLICE"


# ═══════════════════════════════════════════════════════════════════════════════
# BASE CLASS FOR API_LOGS MAPPING
# ═══════════════════════════════════════════════════════════════════════════════

class APILogsModal(BaseModel):
    """
    Base class for all modals that map to API_LOGS.

    Features:
      - Auto-mapping from modal fields to API_LOGS keys via aliases
      - Validation with Pydantic
      - Field prompts for LLM/user interaction
    """

    class Config:
        populate_by_name = True  # Allow population by field name or alias
        extra = "ignore"
        str_strip_whitespace = True

    def to_api_logs(self) -> Dict[str, Any]:
        """
        Convert modal to API_LOGS format.
        Uses field aliases (uppercase) as keys.
        Only includes fields with non-None values.
        """
        result = {}
        for field_name, field_info in self.model_fields.items():
            value = getattr(self, field_name)
            if value is not None:
                # Use alias if defined, otherwise uppercase field name
                key = field_info.alias or field_name.upper()

                # Convert special types
                if isinstance(value, date):
                    value = value.isoformat()
                elif isinstance(value, datetime):
                    value = value.isoformat()
                elif isinstance(value, Enum):
                    value = value.value

                result[key] = value
        return result

    @classmethod
    def get_field_prompts(cls) -> List[Dict[str, Any]]:
        """
        Get prompts for each field to ask user.
        Useful for LLM to know what questions to ask.
        """
        prompts = []
        for field_name, field_info in cls.model_fields.items():
            is_required = field_info.is_required()

            # Get type annotation as string
            annotation = field_info.annotation
            type_str = getattr(annotation, "__name__", str(annotation))

            prompts.append({
                "field": field_name,
                "api_logs_key": field_info.alias or field_name.upper(),
                "prompt": field_info.description or f"Enter {field_name.replace('_', ' ')}",
                "required": is_required,
                "type": type_str,
                "default": None if is_required else field_info.default,
            })
        return prompts

    @classmethod
    def get_required_fields(cls) -> List[str]:
        """Get list of required field names."""
        return [
            field_name
            for field_name, field_info in cls.model_fields.items()
            if field_info.is_required()
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# LOAN CREATION MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class LoanCreationModal(APILogsModal):
    """
    Modal for collecting loan creation requirements.

    Used by: requirement_gathering tool

    Example:
        @mcp.tool()
        def requirement_gathering(customer_data: LoanCreationModal) -> dict:
            api_logs = customer_data.to_api_logs()
            return {"status": "collected", "data": api_logs}
    """

    # ─── Customer Identity ───────────────────────────────────────────────────

    customer_name: str = Field(
        ...,  # Required
        alias="FULL_NAME",
        description="Customer's full name as per PAN/Aadhaar documents",
        min_length=2,
        max_length=100,
    )

    pan_number: str = Field(
        ...,  # Required
        alias="PAN_NUMBER",
        description="PAN card number (10 characters, format: ABCDE1234F)",
    )

    dob: str = Field(
        ...,  # Required
        alias="PAN_DOB",
        description="Date of birth in YYYY-MM-DD format (e.g., 1990-01-15)",
    )

    mobile: str = Field(
        ...,  # Required
        alias="MOBILE",
        description="10-digit Indian mobile number starting with 6/7/8/9",
    )

    email: Optional[str] = Field(
        default=None,
        alias="EMAIL",
        description="Email address for communication (optional)",
    )

    # ─── Loan Details ────────────────────────────────────────────────────────

    loan_amount: int = Field(
        default=50000,
        alias="LOAN_AMOUNT",
        description="Loan amount in INR (e.g., 50000, 100000, 500000)",
        ge=10000,
        le=10000000,
    )

    loan_type: LoanType = Field(
        default=LoanType.PERSONAL,
        alias="LOAN_TYPE",
        description="Type of loan: personal, business, home, vehicle, education, credit_line",
    )

    tenure_months: int = Field(
        default=12,
        alias="TENURE_MONTHS",
        description="Loan tenure in months (e.g., 6, 12, 24, 36, 48)",
        ge=3,
        le=84,
    )

    purpose: Optional[str] = Field(
        default=None,
        alias="LOAN_PURPOSE",
        description="Purpose of loan (optional): medical, travel, wedding, home renovation, etc.",
    )

    # ─── Validators ──────────────────────────────────────────────────────────

    @field_validator("pan_number")
    @classmethod
    def validate_pan(cls, v: str) -> str:
        v = v.upper().strip()
        if not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v):
            raise ValueError("PAN must be 10 characters: 5 letters, 4 digits, 1 letter (e.g., ABCDE1234F)")
        return v

    @field_validator("mobile")
    @classmethod
    def validate_mobile(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if not re.match(r"^[6-9][0-9]{9}$", v):
            raise ValueError("Mobile must be 10 digits starting with 6/7/8/9")
        return v

    @field_validator("dob")
    @classmethod
    def validate_dob(cls, v: str) -> str:
        v = v.strip()
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("DOB must be in YYYY-MM-DD format (e.g., 1990-01-15)")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().lower()
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", v):
            raise ValueError("Invalid email format")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# KYC MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class KYCModal(APILogsModal):
    """
    Modal for KYC data collection.
    Used after identity verification.
    """

    aadhaar_number: str = Field(
        ...,
        alias="AADHAAR_NUMBER",
        description="12-digit Aadhaar number",
    )

    address: str = Field(
        ...,
        alias="ADDRESS",
        description="Full residential address",
        min_length=10,
        max_length=500,
    )

    city: str = Field(
        ...,
        alias="CITY",
        description="City name",
    )

    state: str = Field(
        ...,
        alias="STATE",
        description="State name",
    )

    pincode: str = Field(
        ...,
        alias="PINCODE",
        description="6-digit PIN code",
    )

    @field_validator("aadhaar_number")
    @classmethod
    def validate_aadhaar(cls, v: str) -> str:
        v = v.strip().replace(" ", "")
        if not re.match(r"^[0-9]{12}$", v):
            raise ValueError("Aadhaar must be 12 digits")
        return v

    @field_validator("pincode")
    @classmethod
    def validate_pincode(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[0-9]{6}$", v):
            raise ValueError("Pincode must be 6 digits")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# BANK DETAILS MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class BankDetailsModal(APILogsModal):
    """
    Modal for bank account details.
    Used for disbursement setup.
    """

    bank_account: str = Field(
        ...,
        alias="BANK_ACCOUNT",
        description="Bank account number for disbursement",
        min_length=9,
        max_length=18,
    )

    ifsc: str = Field(
        ...,
        alias="IFSC",
        description="IFSC code of the bank branch (11 characters)",
    )

    account_holder_name: Optional[str] = Field(
        default=None,
        alias="ACCOUNT_HOLDER_NAME",
        description="Account holder name (if different from customer name)",
    )

    bank_name: Optional[str] = Field(
        default=None,
        alias="BANK_NAME",
        description="Bank name (optional, auto-detected from IFSC)",
    )

    @field_validator("ifsc")
    @classmethod
    def validate_ifsc(cls, v: str) -> str:
        v = v.upper().strip()
        if not re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$", v):
            raise ValueError("IFSC must be 11 characters: 4 letters, 0, 6 alphanumeric (e.g., SBIN0001234)")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# OTP VERIFICATION MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class OTPVerificationModal(APILogsModal):
    """
    Modal for OTP verification.
    """

    otp: str = Field(
        ...,
        alias="OTP",
        description="6-digit OTP received on mobile/email",
    )

    otp_reference: Optional[str] = Field(
        default=None,
        alias="OTP_REF",
        description="OTP reference ID (usually auto-filled from previous step)",
    )

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[0-9]{4,6}$", v):
            raise ValueError("OTP must be 4-6 digits")
        return v


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CHANGE MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class DataChangeModal(APILogsModal):
    """
    Modal for changing specific data fields in API_LOGS.
    All fields optional - only changed fields are updated.

    Used by Planner for data change requests.
    """

    loan_amount: Optional[int] = Field(
        default=None,
        alias="LOAN_AMOUNT",
        description="New loan amount in INR",
    )

    tenure_months: Optional[int] = Field(
        default=None,
        alias="TENURE_MONTHS",
        description="New tenure in months",
    )

    mobile: Optional[str] = Field(
        default=None,
        alias="MOBILE",
        description="New mobile number",
    )

    email: Optional[str] = Field(
        default=None,
        alias="EMAIL",
        description="New email address",
    )

    bank_account: Optional[str] = Field(
        default=None,
        alias="BANK_ACCOUNT",
        description="New bank account number",
    )

    ifsc: Optional[str] = Field(
        default=None,
        alias="IFSC",
        description="New IFSC code",
    )

    address: Optional[str] = Field(
        default=None,
        alias="ADDRESS",
        description="New address",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM FLOW MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class CustomFlowModal(BaseModel):
    """
    Modal for defining a custom flow.
    """

    flow_name: str = Field(
        ...,
        description="Name for this custom flow",
    )

    steps: List[str] = Field(
        ...,
        description="List of step names to execute in order",
        min_length=1,
    )

    description: Optional[str] = Field(
        default=None,
        description="Description of what this flow does",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FLOW CONFIRMATION MODAL
# ═══════════════════════════════════════════════════════════════════════════════

class FlowConfirmationModal(BaseModel):
    """
    Modal for confirming or rejecting a planned flow/change.
    """

    confirmed: bool = Field(
        ...,
        description="True to confirm and proceed, False to cancel",
    )

    modifications: Optional[List[str]] = Field(
        default=None,
        description="Optional: steps to skip or modify before execution",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def merge_modal_to_api_logs(api_logs: Dict[str, Any], modal: APILogsModal) -> Dict[str, Any]:
    """
    Merge modal data into API_LOGS.
    Only updates fields that have non-None values in the modal.

    Args:
        api_logs: Existing API_LOGS dict
        modal: Modal with new data

    Returns:
        Updated API_LOGS dict
    """
    modal_data = modal.to_api_logs()
    for key, value in modal_data.items():
        if value is not None:
            api_logs[key] = value
    return api_logs


def get_modal_for_step(step_name: str) -> Optional[type]:
    """
    Get the appropriate modal class for a step.

    Returns None for steps that don't need user input.
    """
    STEP_MODAL_MAP = {
        "requirement_gathering": LoanCreationModal,
        "kyc_collection": KYCModal,
        "aadhaar_verification": KYCModal,
        "bank_verification": BankDetailsModal,
        "otp_verification": OTPVerificationModal,
    }
    return STEP_MODAL_MAP.get(step_name)


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Base
    "APILogsModal",

    # Modals
    "LoanCreationModal",
    "KYCModal",
    "BankDetailsModal",
    "OTPVerificationModal",
    "DataChangeModal",
    "CustomFlowModal",
    "FlowConfirmationModal",

    # Enums
    "LoanType",
    "Partner",

    # Helpers
    "merge_modal_to_api_logs",
    "get_modal_for_step",
]
