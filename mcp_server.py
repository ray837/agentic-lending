import asyncio

from mcp.server.fastmcp import FastMCP,Context
import requests

# Initialize MCP server
mcp = FastMCP("loan_queries")


# -------------------------
# TOOL 1: Stock Price
# -------------------------
@mcp.tool()
async def get_stock_price(symbol: str) -> dict:
    """
    Get latest stock price for a symbol (e.g. AAPL, TSLA)
    """
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey=C9PE94QUEW9VWGFM"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return data
    except Exception as e:
        return {"error": str(e)}


# -------------------------
# TOOL 2: Simple Weather (Mock)
# -------------------------

from pydantic import BaseModel, Field

class LoanResponse(BaseModel):
    loanid: str = Field(description="Unique loan identifier")
    status: str = Field(description="Loan status: ACTIVE, CLOSED, PENDING")
    disbursal_date: str = Field(description="Date of disbursal in YYYY-MM-DD")

@mcp.tool()
async def get_loan_details(loanid: str,ctx: Context) -> dict:
    """
    Fetches loan details for given loanid/lanid/applicationid
    """ +str(LoanResponse.model_json_schema())

    await ctx.info("Fetching loan details...")
    await asyncio.sleep(12)
    # await ctx.debug(f"Looking up loan_id: {loanid}")
    # await ctx.warning("Rate limit approaching")
    # await ctx.error("Something failed")

    return {
        "loanid": loanid,
        "status": "ACTIVE",
        "disbursal_date": "2026-02-01"
    }


from pydantic import BaseModel, Field
from typing import List, Optional

class Charge(BaseModel):
    type: str = Field(description="Type of charge (processing_fee, late_fee, etc.)")
    amount: float = Field(description="Amount of the charge")
    description: Optional[str] = Field(default=None, description="Details about the charge")


class RepaymentScheduleItem(BaseModel):
    installment_no: int = Field(description="Installment number")
    due_date: str = Field(description="Due date in YYYY-MM-DD")
    principal: float = Field(description="Principal component")
    interest: float = Field(description="Interest component")
    total_due: float = Field(description="Total payable amount")


class LoanKFS(BaseModel):
    loan_id: str = Field(description="Unique loan identifier")
    borrower_name: str = Field(description="Name of the borrower")

    loan_amount: float = Field(description="Total sanctioned loan amount")
    disbursed_amount: float = Field(description="Amount actually disbursed")

    interest_rate: float = Field(description="Annual interest rate (in %)")
    tenure_months: int = Field(description="Loan tenure in months")

    emi_amount: float = Field(description="Monthly EMI amount")

    total_interest_payable: float = Field(description="Total interest over tenure")
    total_amount_payable: float = Field(description="Total repayment amount (principal + interest)")

    disbursal_date: str = Field(description="Date of disbursal (YYYY-MM-DD)")
    maturity_date: str = Field(description="Loan end date (YYYY-MM-DD)")

    charges: List[Charge] = Field(description="List of applicable charges")

    repayment_schedule: List[RepaymentScheduleItem] = Field(
        description="Detailed EMI schedule"
    )
@mcp.tool()
async def get_repay_details(loanid: str) -> dict:
    """
    Fetches repay details which includes Key Fact Statement for given loanid/lanid/applicationid
    """ +str(LoanKFS.model_json_schema())
    return {
    "loan_id": loanid,
    "borrower_name": "Rahul Sharma",

    "loan_amount": 100000,
    "disbursed_amount": 98000,

    "interest_rate": 14.5,
    "tenure_months": 12,

    "emi_amount": 9000,

    "total_interest_payable": 8000,
    "total_amount_payable": 108000,

    "disbursal_date": "2026-02-01",
    "maturity_date": "2027-01-01",

    "charges": [
        {
            "type": "processing_fee",
            "amount": 2000,
            "description": "Loan processing fee deducted upfront"
        },
        {
            "type": "late_fee",
            "amount": 500,
            "description": "Penalty for delayed EMI payment"
        }
    ],

    "repayment_schedule": [
        {
            "installment_no": 1,
            "due_date": "2026-03-01",
            "principal": 7500,
            "interest": 1500,
            "total_due": 9000
        },
        {
            "installment_no": 2,
            "due_date": "2026-04-01",
            "principal": 7600,
            "interest": 1400,
            "total_due": 9000
        }
    ]
}



# -------------------------
# RUN SERVER
# -------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
