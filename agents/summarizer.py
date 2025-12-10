import os
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

SUMMARY_PROMPT = "You're a tech watch expert who excels at the task of summarizing contents to extract and condense the most value out of them. You will be provided with a video transcript or a written article and your job will be to extract all the important and valuable information from it in a summary which will bring as much value and detail as the initial content. Don't simplify things, keep the same level of technical details as the original content especially the technical words, be factual and precise, following the original content style. Skip the part that doesn't provide valuable information like greeting and sponsorship segments (even if those segments are about tech stuff). Don't give your interpretation nor use narrative form. Your answer should be a structured and insightful markdown. Don't create too small sections. Use the same language as the original content."

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in the environment")

def summarize_content(content: str, temperature: float = 0.2) -> str:
    """Generate a high-quality, detailed markdown summary using OpenAI."""


    # llm = ChatOpenAI(
    #     api_key=OPENAI_API_KEY,
    #     model="gpt-5-nano",
    #     temperature=temperature,
    # )

    # messages = [
    #     SystemMessage(content=SUMMARY_PROMPT),
    #     HumanMessage(content=content),
    # ]

    # response = llm.invoke(messages)
    # return (response.content or "").strip()
    return "This is a test summary"