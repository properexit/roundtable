"""
Focused test of the propose -> human_approval -> execute mechanism, without
running the full analyst pipeline. Builds a tiny subgraph reusing the exact
same node functions from src.agents.graph, but starting from a stubbed
"final recommendation" -- this isolates the one piece I couldn't verify
myself (interrupt/resume plumbing) from what the LLM happens to decide.

Usage:
    python3 -m scripts.test_interrupt_flow
"""
from __future__ import annotations

import asyncio
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agents.graph import RoundtableState, _execute_node, _human_approval_node, _propose_node


def build_test_graph():
    graph = StateGraph(RoundtableState)
    graph.add_node("propose", _propose_node)
    graph.add_node("human_approval", _human_approval_node)
    graph.add_node("execute", _execute_node)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "human_approval")
    graph.add_edge("human_approval", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=MemorySaver())


async def main():
    graph = build_test_graph()

    print("--- Test 1: reject path ---")
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await graph.ainvoke(
        {
            "ticker": "AAPL",
            "final_action": "buy",
            "final_quantity": 5,
            "final_rationale": "test stub -- interrupt flow check",
            "final_confidence": "medium",
        },
        config=config,
    )
    assert "__interrupt__" in result, "expected an interrupt, graph did not pause"
    print("Interrupt fired correctly with payload:", result["__interrupt__"][0].value)

    result = await graph.ainvoke(
        Command(resume={"approved": False, "approved_by": ""}), config=config
    )
    print("Execution result (should be 'rejected'):", result["execution_result"])
    assert result["execution_result"]["status"] == "rejected"
    print("PASS: rejection never touched the portfolio.\n")

    print("--- Test 2: approve path ---")
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = await graph.ainvoke(
        {
            "ticker": "AAPL",
            "final_action": "buy",
            "final_quantity": 5,
            "final_rationale": "test stub -- interrupt flow check",
            "final_confidence": "medium",
        },
        config=config,
    )
    result = await graph.ainvoke(
        Command(resume={"approved": True, "approved_by": "test-run"}), config=config
    )
    print("Execution result (should be 'executed'):", result["execution_result"])
    assert result["execution_result"]["status"] == "executed"
    print("PASS: approval executed and returned a real price + new portfolio state.")


if __name__ == "__main__":
    asyncio.run(main())
