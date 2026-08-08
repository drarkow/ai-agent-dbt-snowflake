"""
The agent loop: sends the user's question to Claude, lets Claude call
tools (list_tables / describe_table / run_query) as many times as it
needs, and returns the final natural-language answer.

This is the "agentic" pattern - Claude decides which tools to call and
in what order, rather than you hardcoding a fixed pipeline.
"""

from dotenv import load_dotenv
load_dotenv()  # reads .env in the current directory and sets env vars

import anthropic
from snowflake_tools import TOOLS, TOOL_FUNCTIONS

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

SYSTEM_PROMPT = """You are a marketing/sales data analyst assistant with
access to a Snowflake warehouse via tools.

Rules:
- ALWAYS call describe_table before writing SQL against a table you
  haven't already inspected in this conversation. Never guess column
  names or grain.
- Pay close attention to any notes about grain and about columns that
  repeat across rows (e.g. customer-level attributes on an order-grain
  table) - aggregating those incorrectly is a common and serious error.
- Only the table(s) returned by list_tables exist. If a question asks
  about data that isn't in any available table (e.g. products, when
  only orders/customers/nations exist), do NOT guess table or column
  names and do NOT retry with different guessed names. Stop after at
  most one failed attempt and tell the user plainly that this data
  isn't available in the current dataset.
- If a query fails due to a genuine syntax error (not a missing
  table/column), you may correct the syntax and retry once.
- When you give your final answer, briefly state which table(s) and
  any caveats (e.g. "excluding pending orders") so the user can sanity
  check it.
"""


def ask_agent(question: str, max_turns: int = 6) -> dict:
    """
    Returns a dict with:
      - answer: the agent's final natural-language response
      - sql_run: list of every SQL string passed to run_query, in order
      - tools_called: list of every tool name called, in order
    Returning this structure (not just the text answer) is what lets
    the eval script check HOW the agent got its answer, not just what
    it said.
    """
    messages = [{"role": "user", "content": question}]
    sql_run = []
    tools_called = []

    for _ in range(max_turns):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return {
                "answer": "\n".join(text_blocks),
                "sql_run": sql_run,
                "tools_called": tools_called,
            }

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tools_called.append(block.name)
            if block.name == "run_query":
                sql_run.append(block.input.get("sql", ""))

            func = TOOL_FUNCTIONS.get(block.name)
            if func is None:
                result = f"Unknown tool: {block.name}"
            else:
                result = func(**block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": "Reached max turns without a final answer.",
        "sql_run": sql_run,
        "tools_called": tools_called,
    }


if __name__ == "__main__":
    q = "What's the total order value by nation, excluding pending orders?"
    result = ask_agent(q)
    print(result["answer"])