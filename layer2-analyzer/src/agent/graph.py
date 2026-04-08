"""
LangGraph Agent for Layer 2 Root Cause Analysis

Implements a ReAct (Reasoning + Acting) loop where the Agent can:
1. Investigate anomalies by calling tools (query_loki, query_prometheus, execute_diagnostic_command)
2. Reason about collected information
3. Generate structured ActionPlan output

Graph Structure:
    investigator_node → tools_node → investigator_node (loop)
                      ↓
                  planner_node → END

Related: Phase 4.4
"""
import os
import logging
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.tools import AGENT_TOOLS
from schemas.action_plan import ActionPlan

logger = logging.getLogger(__name__)


# ============================================================
# Agent State Definition
# ============================================================
class AgentState(TypedDict):
    """
    State maintained throughout the Agent execution

    Attributes:
        messages: Conversation history (user prompts, agent responses, tool calls)
                  Uses official add_messages reducer for proper ToolMessage handling
        action_plan: Final structured output (populated by planner_node)
        iteration: Current iteration count (prevents infinite loops)
    """
    messages: Annotated[list, add_messages]
    action_plan: ActionPlan | None
    iteration: int


# ============================================================
# LLM Configuration
# ============================================================
def create_llm():
    """Create LLM instance with tools bound"""
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        temperature=0.3
    )
    return llm.bind_tools(AGENT_TOOLS)


# ============================================================
# Node: Tools Executor (using official ToolNode)
# ============================================================
# Create ToolNode instance - handles tool execution and ToolMessage formatting
tools_node = ToolNode(tools=AGENT_TOOLS)


# ============================================================
# Node: Investigator (Reasoning)
# ============================================================
def investigator_node(state: AgentState) -> dict:
    """
    Investigator decides what to do next:
    1. Call tools to gather more information, OR
    2. Signal that enough information has been collected

    Returns:
        Partial state update (messages are auto-merged by add_messages reducer)
    """
    messages = state["messages"]
    iteration = state.get("iteration", 0) + 1

    logger.info(f"[Investigator] Iteration {iteration}, messages count: {len(messages)}")

    # Safety: Prevent infinite loops
    if iteration > 10:
        logger.warning("[Investigator] Max iterations reached, forcing conclusion")
        return {
            "messages": [AIMessage(content="Maximum investigation depth reached. Proceeding to generate action plan.")],
            "iteration": iteration
        }

    # Call LLM to decide next action
    llm = create_llm()
    response = llm.invoke(messages)

    logger.info(f"[Investigator] Response: {response.content[:100] if response.content else '(no content)'}...")
    if response.tool_calls:
        logger.info(f"[Investigator] Tool calls requested: {[tc['name'] for tc in response.tool_calls]}")

    # Return only new messages - add_messages reducer handles merging
    return {
        "messages": [response],
        "iteration": iteration
    }


# ============================================================
# Node: Planner (Structured Output)
# ============================================================
def planner_node(state: AgentState) -> dict:
    """
    Planner generates final ActionPlan using structured output

    This node is called when investigator decides investigation is complete.
    Uses llm.with_structured_output() to force JSON conforming to ActionPlan schema.

    Returns:
        Partial state update with populated action_plan
    """
    messages = state["messages"]
    logger.info("[Planner] Generating structured action plan...")

    # Create LLM with structured output
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        temperature=0.1  # Lower temperature for more deterministic output
    )
    structured_llm = llm.with_structured_output(ActionPlan)

    # Build final prompt for planning
    planning_prompt = """
Based on the investigation above, generate a comprehensive action plan.

Your output MUST include:
1. root_cause: A clear, concise description of the root cause
2. confidence_score: Your confidence level (0.0 to 1.0)
3. actions: List of ordered action steps

For each action step:
- step_id: Sequential number starting from 1
- description: What this step does
- action_type: One of "query", "verify", or "k8s_exec"
- target: Target system/service
- command: Actual command to run
- is_destructive: true if this modifies system state (default: false)

Example action types:
- "query": Read-only queries (Loki, Prometheus, logs)
- "verify": Validation checks (health endpoints, connectivity)
- "k8s_exec": System commands (restart, reload, scale)

Only mark is_destructive=true for commands that MODIFY state.
"""

    # Generate action plan
    try:
        action_plan = structured_llm.invoke(messages + [HumanMessage(content=planning_prompt)])
        logger.info(f"[Planner] Action plan generated: {action_plan.root_cause[:100]}...")
        logger.info(f"[Planner] Confidence: {action_plan.confidence_score}, Actions: {len(action_plan.actions)}")

        return {"action_plan": action_plan}

    except Exception as e:
        logger.error(f"[Planner] Error generating action plan: {e}")
        # Fallback: Generate safe action plan
        fallback_plan = ActionPlan(
            root_cause="Unable to determine root cause due to planning error",
            confidence_score=0.0,
            actions=[]
        )
        return {"action_plan": fallback_plan}


# ============================================================
# Routing Logic
# ============================================================
def should_continue(state: AgentState) -> str:
    """
    Determine next node based on agent's last response

    Returns:
        "tools" if agent wants to use tools
        "planner" if investigation is complete
    """
    messages = state["messages"]
    last_message = messages[-1]

    # If agent made tool calls, route to tools_node
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"

    # Otherwise, investigation complete → go to planner
    return "planner"


# ============================================================
# Build Graph
# ============================================================
def create_agent_graph() -> StateGraph:
    """
    Build and compile the LangGraph agent

    Returns:
        Compiled StateGraph ready for invocation
    """
    # Initialize graph
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("investigator", investigator_node)
    workflow.add_node("tools", tools_node)
    workflow.add_node("planner", planner_node)

    # Set entry point
    workflow.set_entry_point("investigator")

    # Add conditional edges
    workflow.add_conditional_edges(
        "investigator",
        should_continue,
        {
            "tools": "tools",
            "planner": "planner"
        }
    )

    # After tools, loop back to investigator
    workflow.add_edge("tools", "investigator")

    # After planner, end
    workflow.add_edge("planner", END)

    # Compile
    app = workflow.compile()

    logger.info("[Graph] LangGraph agent compiled successfully")
    return app


# ============================================================
# Convenience function
# ============================================================
async def run_agent_analysis(initial_prompt: str) -> ActionPlan:
    """
    Run the agent analysis with an initial prompt

    Args:
        initial_prompt: Initial investigation prompt (e.g., Map-Reduce summary)

    Returns:
        ActionPlan generated by the agent
    """
    graph = create_agent_graph()

    initial_state = {
        "messages": [HumanMessage(content=initial_prompt)],
        "action_plan": None,
        "iteration": 0
    }

    logger.info("[Agent] Starting analysis...")
    result = await graph.ainvoke(initial_state)

    action_plan = result.get("action_plan")
    if not action_plan:
        logger.warning("[Agent] No action plan generated, using fallback")
        action_plan = ActionPlan(
            root_cause="Analysis completed without generating action plan",
            confidence_score=0.0,
            actions=[]
        )

    return action_plan
