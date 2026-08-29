"""
Manual smoke test -- not a pytest test, because it needs real network access
and a real API key, which my sandboxed shells don't have. Run this yourself:

    source .venv/bin/activate
    python3 scripts/smoke_test_llm.py
"""
from src.llm import get_llm

if __name__ == "__main__":
    llm = get_llm()
    response = llm.invoke("In one sentence, what is a P/E ratio?")
    print(response.content)
