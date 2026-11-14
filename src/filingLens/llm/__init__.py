"""LLM layer: turn a question into one validated tool call over verified data.

    client.py     OpenAI-compatible client (any server), FakeLLM for tests, text tool-call parser
    tools.py      tool schemas from registry.yaml; forgiving-but-strict argument validation
    prompt.py     system prompt (companies, metrics, Hinglish glossary) + examples.yaml
    executor.py   runs tools with FactRepo / analytics / retrieval; returns rows + citations
    planner.py    ask(): call -> validate -> one correction -> execute -> fallback
"""
