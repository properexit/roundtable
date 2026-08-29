# Roundtable — Multi-Agent Investment Research Copilot

A multi-agent system that researches a stock the way an investment desk would: a
news analyst, a fundamentals analyst, and a risk manager each produce an
independent view, and a portfolio-manager agent synthesizes them into a
recommendation — which is only ever written to a **simulated** portfolio after
an explicit human approval step.

> **This is a research / decision-support simulator, not a trading system.**
> It does not place real orders, does not connect to a brokerage, and nothing
> it outputs is financial advice. The approval gate exists specifically so a
> human is always in the loop before any state-changing action.

## Why this exists

Built as a focused project to demonstrate production-shaped AI engineering:
multi-agent orchestration, a custom MCP tool server, cloud-native AI services
(not just cloud hosting), and an MLOps layer that actually checks whether the
system's calls are any good — rather than a single LLM-wraps-an-API demo.

## Architecture

```
                         ┌─────────────────────┐
                         │  Portfolio Manager   │  (supervisor)
                         │  synthesizes + gates │
                         └──────────┬───────────┘
                 ┌──────────────────┼──────────────────┐
                 ▼                  ▼                  ▼
        ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
        │  News/Sentiment │ │  Fundamentals   │ │  Risk Manager   │
        │     Analyst     │ │    Analyst      │ │                 │
        └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
                  │                   │                   │
                  └───────────────────┴───────────────────┘
                                      │
                            MCP tool server
              ┌──────────────┬───────────────┬────────────────┐
              ▼              ▼               ▼                ▼
         get_news      get_price/      get_portfolio    propose/execute_
        (+ Azure AI   fundamentals        _state          simulated_trade
         Language      (yfinance)      (Azure Table)      (HUMAN-GATED,
         sentiment)                                        audit-logged)
```

Orchestration: LangGraph. Tools: a custom [MCP](https://modelcontextprotocol.io)
server — agents don't call APIs directly, they call MCP tools, which is what
lets the tool layer be swapped, audited, and permissioned independently of
the agent logic.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Agent orchestration | LangGraph | explicit state machine over agent handoffs, not a black-box "agent" |
| Tool layer | Custom MCP server | standardized tool interface, auditable, decoupled from agent code |
| LLM | Azure OpenAI (fallback: Groq) | see `docs/decisions.md` |
| Market data | yfinance | free, sufficient for research-grade signals |
| News sentiment | Azure AI Language | real Azure AI service, not just infra |
| Portfolio/audit state | Azure Table Storage | cheap, simple, real Azure data service |
| Secrets | Azure Key Vault | no secrets in code or env files in prod |
| Hosting | Azure Container Apps | containerized, autoscaling |
| Observability | Azure Application Insights | trace every agent decision + tool call |
| CI/CD | GitHub Actions | lint/test → build → **eval gate** → deploy |
| Eval | `eval/backtest.py` | replays historical windows, compares system calls to buy-and-hold |

## Repo layout

```
src/
  mcp_server/     MCP server exposing all tools
  tools/          the actual tool implementations (market data, news, sentiment, portfolio, risk)
  agents/         one file per agent + the LangGraph graph wiring them together
  app.py          Streamlit UI — shows the roundtable discussion + approval step
eval/
  backtest.py     historical replay + benchmark comparison, run in CI
infra/            Azure resource setup (Bicep/CLI scripts)
.github/workflows deploy.yml — lint, test, eval-gate, build, deploy
```

## Status

Scaffolding in progress — see `docs/decisions.md` for build log and open decisions.

## Disclaimer

Educational/portfolio project. Not investment advice. Do not use for real
trading decisions.
