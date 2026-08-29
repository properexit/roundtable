"""
The Roundtable graph -- wires all four agents into one run.

    START ─┬─> fundamentals ─┬─> draft ─> risk ─> final ─┬─(hold)──> END
           └─> news ─────────┘                           └─(trade)─> propose
                                                                        │
                                                                        v
                                                              human_approval (interrupt)
                                                                        │
                                                                        v
                                                                     execute ─> END

Fundamentals and News run as two independent branches from START (a real
parallel fan-out, not a sequential loop pretending to be one) and join at
`draft`. `human_approval` calls LangGraph's `interrupt()`, which pauses
the graph entirely -- state is checkpointed, execution stops, and nothing
resumes until the caller invokes the graph again with a Command(resume=...)
carrying the human's decision. That pause is the fail-closed gate: there
is no code path from `final` to `execute` that does not pass through it.
"""
from __future__ import annotations

import json
from typing import Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from src.agents import fundamentals_analyst, news_analyst, portfolio_manager, risk_manager
from src.agents.mcp_client import get_mcp_tools


class RoundtableState(TypedDict, total=False):
    ticker: str
    company_name: str
    fundamentals: str
    news: str
    draft_action: str
    draft_quantity: int
    draft_rationale: str
    risk: str
    final_action: str
    final_quantity: int
    final_rationale: str
    final_confidence: str
    proposal_id: Optional[str]
    human_decision: Optional[dict]
    execution_result: Optional[dict]


def _tool_result_to_dict(raw) -> dict:
    """MCP tool results come back as a JSON string via the LangChain adapter."""
    return json.loads(raw) if isinstance(raw, str) else raw


async def _fundamentals_node(state: RoundtableState) -> dict:
    return {"fundamentals": await fundamentals_analyst.analyze(state["ticker"])}


async def _news_node(state: RoundtableState) -> dict:
    return {"news": await news_analyst.analyze(state["company_name"])}


async def _draft_node(state: RoundtableState) -> dict:
    draft = await portfolio_manager.draft_recommendation(
        state["ticker"], state["fundamentals"], state["news"]
    )
    return {
        "draft_action": draft.action,
        "draft_quantity": draft.quantity,
        "draft_rationale": draft.rationale,
    }


async def _risk_node(state: RoundtableState) -> dict:
    if state["draft_action"] == "hold" or state["draft_quantity"] == 0:
        return {"risk": "No trade proposed in the draft; risk assessment not applicable."}
    risk_text = await risk_manager.analyze(state["ticker"], state["draft_action"], state["draft_quantity"])
    return {"risk": risk_text}


async def _final_node(state: RoundtableState) -> dict:
    draft = portfolio_manager.DraftRecommendation(
        action=state["draft_action"],
        quantity=state["draft_quantity"],
        rationale=state["draft_rationale"],
    )
    final = await portfolio_manager.finalize_recommendation(
        state["ticker"], state["fundamentals"], state["news"], state["risk"], draft
    )
    return {
        "final_action": final.action,
        "final_quantity": final.quantity,
        "final_rationale": final.rationale,
        "final_confidence": final.confidence,
    }


def _route_after_final(state: RoundtableState) -> str:
    if state["final_action"] == "hold" or state["final_quantity"] == 0:
        return END
    return "propose"


async def _propose_node(state: RoundtableState) -> dict:
    (propose_tool,) = await get_mcp_tools(names=["propose_trade"])
    raw = await propose_tool.ainvoke({
        "ticker": state["ticker"],
        "action": state["final_action"],
        "quantity": state["final_quantity"],
        "rationale": state["final_rationale"],
    })
    result = _tool_result_to_dict(raw)
    return {"proposal_id": result["proposal_id"]}


def _human_approval_node(state: RoundtableState) -> dict:
    decision = interrupt({
        "message": "Human approval required before this trade touches the simulated portfolio.",
        "ticker": state["ticker"],
        "action": state["final_action"],
        "quantity": state["final_quantity"],
        "rationale": state["final_rationale"],
        "confidence": state["final_confidence"],
        "proposal_id": state["proposal_id"],
    })
    return {"human_decision": decision}


async def _execute_node(state: RoundtableState) -> dict:
    (execute_tool,) = await get_mcp_tools(names=["execute_simulated_trade"])
    decision = state["human_decision"] or {}
    raw = await execute_tool.ainvoke({
        "proposal_id": state["proposal_id"],
        "approved": bool(decision.get("approved", False)),
        "approved_by": decision.get("approved_by", ""),
    })
    return {"execution_result": _tool_result_to_dict(raw)}


def build_graph():
    graph = StateGraph(RoundtableState)
    graph.add_node("fundamentals", _fundamentals_node)
    graph.add_node("news", _news_node)
    graph.add_node("draft", _draft_node)
    graph.add_node("risk", _risk_node)
    graph.add_node("final", _final_node)
    graph.add_node("propose", _propose_node)
    graph.add_node("human_approval", _human_approval_node)
    graph.add_node("execute", _execute_node)

    graph.add_edge(START, "fundamentals")
    graph.add_edge(START, "news")
    graph.add_edge("fundamentals", "draft")
    graph.add_edge("news", "draft")
    graph.add_edge("draft", "risk")
    graph.add_edge("risk", "final")
    graph.add_conditional_edges("final", _route_after_final, {"propose": "propose", END: END})
    graph.add_edge("propose", "human_approval")
    graph.add_edge("human_approval", "execute")
    graph.add_edge("execute", END)

    return graph.compile(checkpointer=MemorySaver())
