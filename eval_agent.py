"""
Eval suite for the marketing analytics agent.

Each case defines automatic checks against:
  - tools_called: did the agent call describe_table before querying?
  - sql_run: does the generated SQL show correct/incorrect patterns
    (e.g. deduplication before aggregating a customer-level column)?
  - answer: does the final text contain/avoid certain phrases?

Run with: python eval_agent.py
Exits non-zero if any case fails, so this can gate a CI step later.
"""

import re
import sys
from agent import ask_agent


def called_describe_before_query(tools_called: list) -> bool:
    """True if describe_table appears before the first run_query call."""
    try:
        describe_idx = tools_called.index("describe_table")
        query_idx = tools_called.index("run_query")
        return describe_idx < query_idx
    except ValueError:
        return False


def sql_dedupes_before_aggregating(sql_run: list) -> bool:
    """
    Heuristic check: for the account_balance/nation dedup trap, correct
    SQL typically either (a) selects DISTINCT cust_key + the repeated
    column before aggregating, or (b) uses a subquery/CTE that groups
    by cust_key first. This won't catch every valid approach, but it
    catches the naive wrong one: a flat AVG(account_balance) over the
    raw order-grain table with no dedup step anywhere.
    """
    combined = " ".join(sql_run).lower()
    has_distinct = "distinct" in combined
    has_groupby_custkey = bool(re.search(r"group by\s+.*cust_key", combined))
    return has_distinct or has_groupby_custkey


def sql_declined_or_scoped_correctly(sql_run: list, answer: str) -> bool:
    """
    For the out-of-scope product question: passing means EITHER no SQL
    was run (agent recognized it couldn't answer) OR the answer text
    itself says the data isn't available. Failing would be inventing a
    products/lineitem table that doesn't exist in this project.
    """
    if not sql_run:
        return True  # declined without even trying a query - good
    combined_sql = " ".join(sql_run).lower()
    if "lineitem" in combined_sql or "product" in combined_sql:
        return False  # hallucinated a table that isn't in this model
    answer_lower = answer.lower()
    return any(phrase in answer_lower for phrase in [
        "don't have", "doesn't have", "not available", "no product",
        "not in this", "can't answer", "cannot answer"
    ])


EVAL_CASES = [
    {
        "name": "dedup_trap",
        "question": "What's the average account balance per nation?",
        "checks": {
            "called_describe_first": lambda tools, sql, answer: called_describe_before_query(tools),
            "deduped_before_aggregating": lambda tools, sql, answer: sql_dedupes_before_aggregating(sql),
        },
    },
    {
        "name": "out_of_scope_product_question",
        "question": "What were the top selling products last quarter?",
        "checks": {
            "declined_or_scoped_correctly": lambda tools, sql, answer: sql_declined_or_scoped_correctly(sql, answer),
        },
    },
    {
        "name": "ambiguous_balance_question",
        "question": "How many clients have more than 6500 balance? And how many have negative money?",
        "checks": {
            "called_describe_first": lambda tools, sql, answer: called_describe_before_query(tools),
            "ran_a_query": lambda tools, sql, answer: len(sql) > 0,
        },
    },
]


def run_eval():
    results = []
    for case in EVAL_CASES:
        print(f"\n=== {case['name']} ===")
        print(f"Q: {case['question']}")

        result = ask_agent(case["question"])
        tools, sql, answer = result["tools_called"], result["sql_run"], result["answer"]

        case_passed = True
        for check_name, check_fn in case["checks"].items():
            passed = check_fn(tools, sql, answer)
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {check_name}")
            if not passed:
                case_passed = False

        print(f"  Answer: {answer[:200]}")
        if not case_passed:
            print(f"  --- DEBUG: tools_called = {tools} ---")
            print(f"  --- DEBUG: sql_run = {sql} ---")
        results.append((case["name"], case_passed))

    print("\n=== Summary ===")
    all_passed = True
    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'} - {name}")
        if not passed:
            all_passed = False

    return all_passed


if __name__ == "__main__":
    success = run_eval()
    sys.exit(0 if success else 1)