"""
Tool layer: defines what the agent can DO, and executes those actions
against Snowflake. Keep this separate from the agent loop so you can
unit-test it independently of the LLM.
"""

import snowflake.connector
import os

# --- Connection -------------------------------------------------------
# Use a read-only role/user if at all possible. An LLM-driven agent
# should never run with write access to your warehouse.

def get_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        role=os.environ.get("SNOWFLAKE_ROLE", "READONLY_ROLE"),
    )


# --- Tool implementations ---------------------------------------------
# Each function here corresponds 1:1 to a tool definition the LLM sees.
# Keep them narrow and safe.

def list_tables() -> str:
    """Return the names of tables/models available to query."""
    # For a small project, hardcoding this from your dbt models is fine
    # and avoids exposing your whole raw schema to the agent.
    return "fct_customer_orders_ex"


def describe_table(table_name: str) -> str:
    """
    Return the column-level metadata for a table, pulled straight from
    your dbt schema.yml. This is where all that metadata work pays off:
    the agent reads the SAME descriptions you wrote for humans.
    """
    # Simplest approach for a portfolio project: hand-copy the relevant
    # schema.yml content into a dict. A more advanced version would
    # parse schema.yml directly or query dbt's generated catalog.json.
    descriptions = {
        "fct_customer_orders_ex": """
Grain: one row per order (order_key). Joins orders -> customers -> nations.
IMPORTANT: account_balance and nation are CUSTOMER-level attributes and
repeat across every order from the same customer - do not sum/average
them without deduplicating by cust_key first.

Columns:
- order_key (unique, not null): primary key, one order
- cust_key: FK to customer, repeats per order
- order_date: date order was placed
- order_status: single-letter code, exact mapping not yet verified -
  use `select distinct order_status` if you need to filter by status
- total_price: order total as recorded in source; not verified whether
  it's pre- or post-discount
- account_balance: CUSTOMER-level, repeats per order, do not aggregate
  without deduping
- nation: CUSTOMER-level (nation of the customer, not fulfillment
  location), repeats per order
"""
    }
    return descriptions.get(table_name, f"Unknown table: {table_name}")


def run_query(sql: str) -> str:
    """
    Execute a SQL query against Snowflake and return results as text.
    SAFETY: only allow SELECT statements. This is a portfolio project,
    but this guardrail is exactly the kind of thing an interviewer will
    ask about, so don't skip it even here.
    """
    normalized = sql.strip().lower()
    if not normalized.startswith("select"):
        return "Error: only SELECT statements are permitted."

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        # Cap rows returned to the LLM to avoid blowing the context window
        preview = rows[:50]
        result_lines = [", ".join(columns)]
        result_lines += [", ".join(str(v) for v in row) for row in preview]
        suffix = f"\n... ({len(rows)} total rows, showing first 50)" if len(rows) > 50 else ""
        return "\n".join(result_lines) + suffix
    except Exception as e:
        # Return the error TO THE AGENT so it can see what went wrong
        # and retry with corrected SQL - this is the "agentic" part.
        return f"Query failed: {e}"
    finally:
        conn.close()


# --- Tool schema for the Anthropic API ---------------------------------
# This is what tells Claude what tools exist and how to call them.

TOOLS = [
    {
        "name": "list_tables",
        "description": "List the names of tables available to query.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "describe_table",
        "description": (
            "Get column names, grain, and business-context notes for a "
            "table. ALWAYS call this before writing SQL against a table "
            "you haven't already described in this conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "Name of the table to describe"}
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "run_query",
        "description": "Execute a read-only SQL SELECT query against Snowflake and return the results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A SELECT statement to run"}
            },
            "required": ["sql"],
        },
    },
]

# Dispatch table so the agent loop can call the right Python function
# for whatever tool name Claude asks for.
TOOL_FUNCTIONS = {
    "list_tables": lambda **kwargs: list_tables(),
    "describe_table": lambda **kwargs: describe_table(**kwargs),
    "run_query": lambda **kwargs: run_query(**kwargs),
}