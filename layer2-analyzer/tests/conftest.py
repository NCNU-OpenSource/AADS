"""
Test harness shim (degrade-gracefully).

main.py wires up the full Layer 2 service (LangChain/LangGraph agent, LLM
clients, consumers, notification hub). The runner-spec unit tests only exercise
pure deterministic methods on RootCauseAnalyzer (``_ensure_lab_node_agent_steps``,
``_to_fixing_plan`` and helpers).

To keep those tests runnable without the full agent stack installed, we stub the
heavy dependencies — but ONLY the ones that are not actually importable in the
current environment. In Docker/CI where the real stack is present, nothing is
stubbed, so tests like test_map_reduce / test_agent_tools still exercise the real
modules.
"""
import importlib
import sys
import types


class _Stub:
    def __init__(self, *args, **kwargs):
        pass


def _stub_if_missing(name: str, **attrs):
    try:
        importlib.import_module(name)
        return  # real module available — never shadow it
    except Exception:
        pass
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    # Register parent packages as stub packages so `from a.b import c` does not
    # execute the real (heavy) a/__init__.py before reaching the leaf stub.
    parts = name.split(".")
    for depth in range(1, len(parts)):
        parent_name = ".".join(parts[:depth])
        parent = sys.modules.get(parent_name)
        if parent is None or not isinstance(parent, types.ModuleType):
            parent = types.ModuleType(parent_name)
            parent.__path__ = []  # mark as package
            sys.modules[parent_name] = parent
        setattr(sys.modules[parent_name], parts[depth], sys.modules[".".join(parts[: depth + 1])])


# Only stubbed when genuinely absent (e.g. local dev without the agent stack).
_stub_if_missing("uvicorn", run=lambda *a, **k: None)
_stub_if_missing("anomaly_consumer", AnomalyConsumer=_Stub)
_stub_if_missing("raw_log_fetcher", RawLogFetcher=_Stub)
_stub_if_missing(
    "root_cause_analyzer",
    AnomalyAggregator=_Stub,
    MetricsCorrelator=_Stub,
    KnowledgeBase=_Stub,
    LLMReasoner=_Stub,
)
_stub_if_missing("llm.openai_compatible", OpenAICompatibleClient=_Stub)
_stub_if_missing("llm.ollama", OllamaClient=_Stub)
_stub_if_missing("suggestion_generator", SuggestionGenerator=_Stub)
_stub_if_missing("notification_hub", NotificationHub=_Stub)
_stub_if_missing(
    "aggregator.map_reduce",
    deduplicate_and_summarize=lambda *a, **k: None,
    format_summary_for_prompt=lambda *a, **k: "",
)
_stub_if_missing("agent.graph", run_agent_analysis=lambda *a, **k: None, create_agent_graph=lambda *a, **k: None)
