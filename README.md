# Marketing Analytics Agent

A natural-language query agent over a Snowflake-based marketing/sales
data warehouse, built to explore what it takes to make a data
warehouse genuinely usable by an LLM agent — not just queryable by
humans who already know the schema.

This project pairs with [dbt-interview-prep](https://github.com/drarkow/dbt-playground),
which builds and documents the underlying data models. This repo is
the AI/agent layer that sits on top.

## What this is

Ask a plain-English question about the data (e.g. *"What's the average
account balance per nation?"*) and the agent:

1. Looks up which tables are available
2. Reads business-context metadata for the relevant table(s) — grain,
   column meanings, and known gotchas — sourced from the dbt project's
   `schema.yml`
3. Writes and executes SQL against Snowflake
4. Returns a plain-English answer, stating any caveats

The interesting part isn't the SQL generation itself — LLMs are
reasonably good at that already. It's making sure the agent has the
*business context* to generate SQL that's not just syntactically valid
but semantically correct, and having a repeatable way to check that it
actually does.

## Why this exists

This was built as a hands-on exploration of what "AI-ready data
infrastructure" actually requires in practice: structured metadata an
agent can consume, a tool-use architecture instead of one-shot prompt
stuffing, and automated quality checks that catch regressions the same
way unit tests catch code regressions.

## Architecture

```
User question
     │
     ▼
Claude (tool-use loop)
     │
     ├── list_tables         → which tables exist
     ├── describe_table      → grain, column meanings, gotchas
     │                         (sourced from dbt schema.yml)
     └── run_query           → executes SELECT-only SQL on Snowflake
     │
     ▼
Plain-English answer + caveats
```

The agent doesn't get the schema dumped into its prompt upfront.
Instead it's given tools and decides what to look up and in what
order — calling `describe_table` before writing SQL against a table
it hasn't inspected yet, and re-reading a query error to retry with
corrected SQL rather than guessing blind. This is the "agentic" part:
the control flow is decided by the model, not hardcoded by me.

### Why metadata matters more than the SQL generation

The data model this agent queries (`fct_customer_orders_ex`) has a
subtle trap: it's order-grain, but two columns (`account_balance`,
`nation`) are customer-level attributes that repeat once per order.
Naively averaging `account_balance` over the raw table silently
overweights customers with more orders.

The dbt model's metadata explicitly documents this trap in plain
language. The agent reads that description via `describe_table` before
writing SQL — and correctly deduplicates by `cust_key` before
aggregating as a result. Without that documentation, nothing stops the
agent from writing a technically valid, silently wrong query.

**Known limitation:** table metadata is currently hardcoded as a
Python string in `snowflake_tools.py`, manually copied from the dbt
`schema.yml`. It will drift out of sync if the dbt model changes.
Next step: parse dbt's generated `catalog.json` directly instead of
duplicating the descriptions by hand.

## Tech stack

- **Claude (Anthropic API)** — tool-use / agentic loop
- **Snowflake** — data warehouse (TPCH sample data, modeled via dbt)
- **Python** — `anthropic`, `snowflake-connector-python`, `python-dotenv`

## Setup

```bash
python -m venv venv
venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r requirements.txt
cp .env.example .env        # then fill in real credentials
python agent.py
```

Runs under a dedicated `agent_readonly` Snowflake role, scoped to
`SELECT` only on the relevant schema — created specifically for this
project rather than reusing a broader default role. The `run_query`
tool also rejects any non-SELECT statement at the application level,
as a second layer of protection.

## Evaluation / quality assurance

Rather than eyeballing a few test questions, the agent is checked
against a small fixed eval suite (`eval_agent.py`) that asserts on
*how* the agent reached its answer, not just what it said — e.g.
whether it called `describe_table` before querying, and whether the
generated SQL shows a deduplication step before aggregating a
customer-level column.

- Eval suite automated via GitHub Actions, currently manual-trigger (workflow_dispatch) while iterating
- Link to a passing Actions run as evidence: https://github.com/drarkow/ai-agent-dbt-snowflake/actions/runs/31282673871
- Current eval cases: dedup trap, out-of-scope question handling,
  ambiguous natural-language phrasing
- Notable bug caught by this suite: the agent initially entered a
  retry loop and returned no answer at all when asked about data that
  doesn't exist in this model (product-level details), instead of
  recognizing the limitation and saying so. Root cause was overly
  aggressive retry instructions in the system prompt; fixed and
  re-validated.

## Roadmap

- [ ] Pull table metadata from dbt's `catalog.json` instead of a
      hardcoded string
- [ ] Expand the eval suite beyond the current 3 cases