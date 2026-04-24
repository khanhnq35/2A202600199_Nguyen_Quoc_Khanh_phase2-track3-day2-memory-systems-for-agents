import os
from typing import List, Dict, Any, Annotated, TypedDict
import json

from langgraph.graph import StateGraph, END
from langchain_google_vertexai import ChatVertexAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph.message import add_messages

from architecture import MemoryManager, count_tokens, ShortTermMemory
from config import MODEL_NAME, TOKEN_BUDGET, CLOUD_PROJECT, CLOUD_REGION
from prompts.system_prompt import (
    SYSTEM_PROMPT_TEMPLATE, 
    COMBINED_MEMORY_PROMPT
)

# Fix Bug 2.2/2.4: Persistent Manager instances (để fallback dict không bị reset)
_manager_cache: Dict[str, MemoryManager] = {}

def get_manager(user_id: str) -> MemoryManager:
    """Gets or creates a persistent MemoryManager for a specific user.

    This ensures that memory persistence (especially for local dictionary fallbacks) 
    is maintained across multiple graph invocations in the same session.

    Args:
        user_id (str): The unique identifier for the user.

    Returns:
        MemoryManager: The persistent manager instance for the user.
    """
    if user_id not in _manager_cache:
        _manager_cache[user_id] = MemoryManager(user_id)
    return _manager_cache[user_id]

class MemoryState(TypedDict):
    """Represents the state of the memory-augmented agent in LangGraph.

    Attributes:
        messages (Annotated[list[BaseMessage], add_messages]): The conversation history.
        user_profile (Dict[str, Any]): Long-term facts and preferences.
        episodes (List[Dict[str, Any]]): Past relevant experiences.
        semantic_hits (List[str]): Domain knowledge chunks from vector store.
        memory_budget (int): Current estimated token usage.
        user_id (str): The unique ID of the user.
        extracted_facts (Dict[str, Any]): Facts extracted in the current turn.
        current_task (str): The inferred current task/intent.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    user_profile: Dict[str, Any]
    episodes: List[Dict[str, Any]]
    semantic_hits: List[str]
    memory_budget: int
    user_id: str
    extracted_facts: Dict[str, Any]
    current_task: str

# Initialize LLM
llm = ChatVertexAI(
    model_name=MODEL_NAME, 
    temperature=0,
    project=CLOUD_PROJECT,
    location=CLOUD_REGION
)

def retrieve_memory_node(state: MemoryState) -> Dict[str, Any]:
    """Classifies user intent and loads relevant context from memory layers.

    This node acts as a Memory Router, determining which memory layers (Semantic, 
    Episodic) are relevant based on the user's latest message.

    Args:
        state (MemoryState): The current graph state.

    Returns:
        Dict[str, Any]: Updated state keys (user_profile, episodes, semantic_hits, etc.).
    """
    user_id = state.get("user_id", "default_user")
    manager = get_manager(user_id)
    
    # Get last human message as query
    last_message = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            last_message = str(msg.content)
            break
    
    # Sync messages to ShortTermMemory for budget management
    manager.short_term.clear()
    for msg in state["messages"]:
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        manager.short_term.add(role, str(msg.content))
    
    # --- MEMORY ROUTER LOGIC (Feedback 2.1 Optimization) ---
    # Tại sao chọn Rule-based Router thay vì LLM Router?
    # 1. Tốc độ (Latency): Phân loại keyword gần như tức thì, không tốn thêm 1-2s gọi LLM.
    # 2. Chi phí (Cost): Không tốn token cho việc phân loại intent đơn giản.
    # 3. Độ tin cậy: Với các keyword kỹ thuật rõ ràng, rule-based cho kết quả nhất quán.
    # Nếu hệ thống phức tạp hơn, có thể chuyển sang LLM classifier hoặc Semantic Router.
    query = last_message.lower()
    user_profile = manager.long_term.get_profile(user_id)
    
    episodes = []
    semantic_hits = []
    
    # Rule-based router logic
    is_technical = any(word in query for word in ["docker", "python", "lệnh", "code", "lỗi", "how to", "thư viện"])
    is_asking_past = any(word in query for word in ["trước đây", "hôm qua", "lần trước", "đã nói", "kinh nghiệm", "nhớ", "đã làm"])
    
    if is_technical:
        print(f"🔍 Router: Technical intent detected -> Querying Semantic Memory")
        semantic_hits = manager.semantic.search(last_message)
    
    if is_asking_past:
        print(f"🔍 Router: Past recall intent detected -> Querying Episodic Memory")
        episodes = manager.episodic.recall(user_id)
    
    # Default fallback: Nạp tối thiểu context nếu không khớp rule nào
    if not episodes and not semantic_hits:
        episodes = manager.episodic.recall(user_id, k=1)
        semantic_hits = manager.semantic.search(last_message, k=1)

    return {
        "user_profile": user_profile,
        "episodes": episodes,
        "semantic_hits": semantic_hits,
        "current_task": last_message
    }

def format_sections_and_trim(state: MemoryState) -> str:
    """Formats and trims memory content to stay within the token budget.

    Args:
        state (MemoryState): The current graph state.

    Returns:
        str: The fully formatted system prompt with injected context.
    """
    total_max = TOKEN_BUDGET.get("total_max", 8000)
    user_id = state.get("user_id", "default_user")
    manager = get_manager(user_id)
    
    budgets = {
        "short_term": int(total_max * TOKEN_BUDGET["short_term"]),
        "long_term": int(total_max * TOKEN_BUDGET["long_term"]),
        "episodic": int(total_max * TOKEN_BUDGET["episodic"]),
        "semantic": int(total_max * TOKEN_BUDGET["semantic"]),
    }

    # Format Long-term Profile (Safe per-category trimming)
    profile = state.get("user_profile", {})
    profile_str = ""
    for cat, data in profile.items():
        cat_str = f"{cat}: {json.dumps(data, ensure_ascii=False)}\n"
        if count_tokens(profile_str + cat_str) > budgets["long_term"]:
            break
        profile_str += cat_str
    
    # Format Episodic
    episodes = state.get("episodes", [])
    episodic_str = ""
    for ep in episodes:
        ep_text = f"- Task: {ep['task']}\n  Outcome: {ep['outcome']}\n  Reflection: {ep['reflection']}\n"
        if count_tokens(episodic_str + ep_text) > budgets["episodic"]:
            break
        episodic_str += ep_text
    
    # Format Semantic
    semantic_hits = state.get("semantic_hits", [])
    semantic_str = ""
    for hit in semantic_hits:
        if count_tokens(semantic_str + hit) > budgets["semantic"]:
            break
        semantic_str += f"- {hit}\n"

    # Recent messages (Short-term)
    trimmed_msgs = manager.short_term.trim(budgets["short_term"])
    recent_msgs_str = ""
    for msg in trimmed_msgs:
        role = "User" if msg["role"] == "user" else "Agent"
        recent_msgs_str += f"{role}: {msg['content']}\n"

    return SYSTEM_PROMPT_TEMPLATE.format(
        user_profile_section=profile_str or "Chưa có thông tin.",
        episodic_section=episodic_str or "Chưa có trải nghiệm tương tự.",
        semantic_section=semantic_str or "Không tìm thấy kiến thức liên quan.",
        recent_messages_section=recent_msgs_str
    )

def generate_response_node(state: MemoryState) -> Dict[str, Any]:
    """Generates the final AI response using the memory-augmented prompt.

    Args:
        state (MemoryState): The current graph state.

    Returns:
        Dict[str, Any]: State update containing the AI message.
    """
    system_prompt = format_sections_and_trim(state)
    messages = [SystemMessage(content=system_prompt)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

def save_memory_node(state: MemoryState) -> Dict[str, Any]:
    """Extracts facts and saves episodic reflection in a single LLM call.

    Args:
        state (MemoryState): The current graph state.

    Returns:
        Dict[str, Any]: Extracted facts for the current turn.
    """
    user_id = state.get("user_id", "default_user")
    manager = get_manager(user_id)
    
    last_human_msg = ""
    last_ai_msg = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and not last_ai_msg:
            last_ai_msg = str(msg.content)
        if isinstance(msg, HumanMessage) and not last_human_msg:
            last_human_msg = str(msg.content)
    
    if not last_human_msg or not last_ai_msg:
        return {"extracted_facts": {}}

    conv_pair = f"User: {last_human_msg}\nAgent: {last_ai_msg}"
    
    content = ""
    try:
        res = llm.invoke(COMBINED_MEMORY_PROMPT.format(conversation=conv_pair))
        content = str(res.content).strip()
        
        if "```" in content:
            parts = content.split("```")
            content = parts[1]
            if content.startswith("json"):
                content = content[4:].strip()
            content = content.strip()
            
        result_json = json.loads(content)
        new_facts = result_json.get("facts", {})
        reflection = result_json.get("reflection", "Hoàn thành nhiệm vụ.")
        
        if isinstance(new_facts, dict) and new_facts:
            for k, v in new_facts.items():
                manager.long_term.update_fact(user_id, k, v)
            print(f"✅ Đã cập nhật {len(new_facts)} facts mới cho {user_id}")
            
        # 2. Lưu Episodic Memory
        # Xác định outcome dựa trên nội dung response (đơn giản)
        outcome = "success"
        fail_keywords = ["không biết", "không thể", "xin lỗi", "lỗi", "error", "không tìm thấy"]
        if any(kw in reflection.lower() or kw in last_ai_msg.lower() for kw in fail_keywords):
            outcome = "failure"

        manager.episodic.save_episode(
            user_id=user_id,
            task=last_human_msg,
            trajectory=[last_human_msg],
            outcome=outcome,
            reflection=reflection
        )
        print(f"✅ Đã lưu episodic memory ({outcome}) cho {user_id}")
        
        return {"extracted_facts": new_facts}

    except Exception as e:
        print(f"⚠️ Lỗi khi lưu memory: {e} | Content: {content[:100]}")
        return {"extracted_facts": {}}

def build_agent():
    """Builds the LangGraph state machine for the memory agent.

    Returns:
        CompiledGraph: The ready-to-use agent graph.
    """
    workflow = StateGraph(MemoryState)
    workflow.add_node("retrieve_memory", retrieve_memory_node)
    workflow.add_node("generate_response", generate_response_node)
    workflow.add_node("save_memory", save_memory_node)
    workflow.set_entry_point("retrieve_memory")
    workflow.add_edge("retrieve_memory", "generate_response")
    workflow.add_edge("generate_response", "save_memory")
    workflow.add_edge("save_memory", END)
    return workflow.compile()

agent = build_agent()
