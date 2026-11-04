"""hr — stdio demo upstream for the B01 walking skeleton.

8 tools across employee / leave / payroll domains, plus:
- a ``search`` tool that shares its name with itops (namespace test)
- one destructive tool (``approve_leave``)
- "region" planted in one tool definition (over-threshold demo)
"""

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer("hr")


@mcp.tool()
def get_employee(employee_id: str) -> str:
    """Fetch an employee record by employee id."""
    return f"employee {employee_id}: Ada Lovelace, Engineering"


@mcp.tool()
def search(query: str) -> str:
    """Search employee records (directory, teams, contacts) by keyword."""
    return f"hr search results for {query!r}: 3 records"


@mcp.tool()
def get_leave_balance(employee_id: str) -> str:
    """Query the remaining annual leave balance for an employee."""
    return f"employee {employee_id}: 12 days of leave remaining"


@mcp.tool()
def apply_leave(employee_id: str, days: int, reason: str) -> str:
    """Submit a leave request for an employee."""
    return f"leave request created: {employee_id}, {days} day(s), reason={reason!r}"


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
def approve_leave(request_id: str) -> str:
    """Approve a pending leave request (irreversible)."""
    return f"leave request {request_id} approved"


@mcp.tool()
def list_payroll(period: str) -> str:
    """List payroll entries for a pay period."""
    return f"payroll entries for {period}: 42 rows"


@mcp.tool()
def get_compensation(employee_id: str) -> str:
    """Fetch compensation details (salary, bonus) for an employee."""
    return f"employee {employee_id}: base=88000, bonus=12000"


@mcp.tool()
def region_hr_report(region_id: str) -> str:
    """Generate the HR headcount report for a region."""
    return f"headcount report for region {region_id}: 120 employees"


if __name__ == "__main__":
    mcp.run(transport="stdio")
