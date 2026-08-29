"""
Single place that constructs the LLM client every agent uses. Keeping this
separate from agent code means swapping providers (which we already had to
consider seriously, see docs/decisions.md) never touches agent logic.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI

load_dotenv()


def get_llm(temperature: float = 0.2) -> AzureChatOpenAI:
    """
    Returns the shared Azure OpenAI chat client. temperature is kept low by
    default -- these agents are producing analysis, not creative writing,
    and low temperature makes their reasoning more reproducible for eval.
    """
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        azure_deployment=deployment,
        api_version="2024-10-21",
        temperature=temperature,
    )
