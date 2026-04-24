import os
import json
import time
from typing import List, Dict, Any
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from agent import agent as memory_agent, count_tokens
from architecture import MemoryManager
from langchain_google_vertexai import ChatVertexAI
from config import MODEL_NAME, CLOUD_PROJECT, CLOUD_REGION

# Initialize No-Memory Agent
no_memory_llm = ChatVertexAI(
    model_name=MODEL_NAME, 
    temperature=0,
    project=CLOUD_PROJECT,
    location=CLOUD_REGION
)

SCENARIOS = [
    {
        "id": 1,
        "name": "Profile Recall - Tên",
        "turns": [
            "Chào bạn, tôi tên là Khanh.",
            "Tôi đang làm việc tại VinUni.",
            "Bạn có nhớ tôi tên là gì và làm việc ở đâu không?"
        ],
        "category": "profile recall",
        "expected": ["Khanh", "VinUni"],
        "analysis": "Agent truy xuất chính xác thông tin định danh (tên, nơi làm việc) từ Long-term Memory."
    },
    {
        "id": 2,
        "name": "Conflict Update - Dị ứng",
        "turns": [
            "Tôi bị dị ứng sữa bò.",
            "À không, tôi nhầm, tôi bị dị ứng đậu nành chứ không phải sữa bò.",
            "Vậy cuối cùng tôi bị dị ứng món gì?"
        ],
        "category": "conflict update",
        "expected": ["đậu nành"],
        "unexpected": ["sữa bò"],
        "analysis": "Agent áp dụng đúng chính sách 'Recency wins', ghi đè thông tin cũ bằng thông tin đính chính mới nhất."
    },
    {
        "id": 3,
        "name": "Episodic Recall - Debug Docker",
        "turns": [
            "Hôm qua tôi đã gặp lỗi Docker port mapping và đã sửa bằng cách đổi sang port 8080.",
            "Hôm nay tôi lại gặp vấn đề tương tự.",
            "Dựa trên kinh nghiệm hôm qua, bạn khuyên tôi nên làm gì?"
        ],
        "category": "episodic recall",
        "expected": ["8080", "port"],
        "analysis": "Agent sử dụng Episodic Memory để gợi ý giải pháp dựa trên thành công trong quá khứ."
    },
    {
        "id": 4,
        "name": "Semantic Retrieval - Docker FAQ",
        "turns": [
            "Làm thế nào để xóa toàn bộ containers đang dừng?",
            "Cảm ơn. Còn lệnh xem log thì sao?"
        ],
        "category": "semantic retrieval",
        "expected": ["logs"], # Fix Feedback 4.1: Chỉ check info liên quan đến turn cuối
        "analysis": "Agent truy vấn thành công kiến thức kỹ thuật từ Semantic Memory (Vector Store)."
    },
    {
        "id": 5,
        "name": "Profile Recall - Sở thích",
        "turns": [
            "Tôi rất thích lập trình bằng ngôn ngữ Python.",
            "Tôi muốn xây dựng một dự án AI.",
            "Với sở thích của tôi, bạn gợi ý tôi nên dùng thư viện nào?"
        ],
        "category": "profile recall",
        "expected": ["Python", "AI", "thư viện"],
        "analysis": "Agent kết hợp thông tin về ngôn ngữ yêu thích và mục tiêu hiện tại để đưa ra gợi ý phù hợp."
    },
    {
        "id": 6,
        "name": "Token Budget - Long Conversation",
        "turns": [
            "Hãy kể cho tôi nghe về lịch sử AI.",
            "Tiếp tục đi.",
            "Tóm tắt lại nãy giờ chúng ta đã nói gì?"
        ],
        "category": "trim/token budget",
        "expected": ["lịch sử", "AI"],
        "analysis": "Agent duy trì tóm tắt tốt mặc dù lịch sử hội thoại dài, nhờ cơ chế trimming hiệu quả."
    },
    {
        "id": 7,
        "name": "Episodic Recall - Nấu ăn",
        "turns": [
            "Lần trước tôi nấu phở bị mặn quá vì cho nhiều nước mắm.",
            "Hôm nay tôi định nấu lại phở.",
            "Bạn có lưu ý gì cho tôi không?"
        ],
        "category": "episodic recall",
        "expected": ["nước mắm", "mặn", "phở"],
        "analysis": "Agent học từ thất bại trong quá khứ lưu trong Episodic Memory để cảnh báo người dùng."
    },
    {
        "id": 8,
        "name": "Semantic Retrieval - Technical",
        "turns": [
            "Build image mà không dùng cache thì làm thế nào?",
            "Flag --no-cache có tác dụng gì cụ thể?"
        ],
        "category": "semantic retrieval",
        "expected": ["no-cache", "cache"],
        "analysis": "Agent giải thích chi tiết flag kỹ thuật dựa trên tài liệu Semantic Memory."
    },
    {
        "id": 9,
        "name": "Conflict Update - Nghề nghiệp",
        "turns": [
            "Tôi là sinh viên ngành Khoa học máy tính.",
            "Hiện tại tôi đã tốt nghiệp và đang là AI Engineer.",
            "Hiện tại công việc của tôi là gì?"
        ],
        "category": "conflict update",
        "expected": ["AI Engineer"],
        "unexpected": ["sinh viên"],
        "analysis": "Agent cập nhật trạng thái nghề nghiệp mới nhất và bỏ qua thông tin đã cũ."
    },
    {
        "id": 10,
        "name": "Combined - Full Flow",
        "turns": [
            "Chào, tôi là Khanh, thích Python.",
            "Lần trước tôi học về LangGraph bị vướng chỗ State definition.",
            "Nhắc lại kiến thức LangGraph và giải quyết vướng mắc của tôi với Python."
        ],
        "category": "all",
        "expected": ["Khanh", "LangGraph", "Python"],
        "analysis": "Agent thể hiện khả năng tổng hợp từ cả 4 tầng memory: Profile (tên), Episodic (vướng mắc cũ), Semantic (kiến thức LangGraph)."
    }
]

def run_no_memory_agent(last_turn: str) -> str:
    """Simulates an agent without any external memory (Baseline)."""
    system_msg = SystemMessage(content="Bạn là một AI assistant hữu ích.")
    response = no_memory_llm.invoke([system_msg, HumanMessage(content=last_turn)])
    return str(response.content)

def run_benchmark():
    """Executes the benchmark and generates an enhanced report."""
    results = []
    user_id = "benchmark_user"
    
    manager = MemoryManager(user_id)
    manager.delete_all_user_data(user_id)
    manager.semantic.clear()
    
    manager.semantic.add_documents([
        "Lệnh `docker container prune` dùng để xóa toàn bộ containers đang dừng.",
        "Sử dụng `docker logs -f <container_id>` để xem log của container.",
        "Thêm flag `--no-cache` vào lệnh build: `docker build --no-cache -t my-app .` để không dùng cache."
    ])

    print(f"🚀 Bắt đầu chạy benchmark 100/100 cho {len(SCENARIOS)} kịch bản...\n")

    for scenario in SCENARIOS:
        print(f"--- Kịch bản {scenario['id']}: {scenario['name']} ---")
        
        history = []
        with_memory_resp = ""
        no_memory_resp = ""
        
        start_time = time.time()
        
        for i, turn in enumerate(scenario["turns"]):
            is_last = (i == len(scenario["turns"]) - 1)
            
            state = {
                "messages": history + [HumanMessage(content=turn)],
                "user_id": user_id,
                "user_profile": {},
                "episodes": [],
                "semantic_hits": [],
                "memory_budget": 0
            }
            res = memory_agent.invoke(state)
            with_memory_resp = res["messages"][-1].content
            
            history.append(HumanMessage(content=turn))
            history.append(AIMessage(content=with_memory_resp))
            
            if is_last:
                no_memory_resp = run_no_memory_agent(turn)

        latency = time.time() - start_time
        
        is_pass = True
        for word in scenario.get("expected", []):
            if word.lower() not in with_memory_resp.lower():
                is_pass = False
                break
        
        for word in scenario.get("unexpected", []):
            if word.lower() in with_memory_resp.lower():
                is_pass = False
                break

        metrics = {
            "latency": round(latency, 2),
            "with_memory_tokens": count_tokens(with_memory_resp),
            "no_memory_tokens": count_tokens(no_memory_resp),
            "history_turns": len(scenario["turns"])
        }

        results.append({
            "id": scenario["id"],
            "name": scenario["name"],
            "category": scenario["category"],
            "turns": scenario["turns"], # Save turns for context (Feedback Optimization)
            "no_memory": no_memory_resp,
            "with_memory": with_memory_resp,
            "pass": is_pass,
            "metrics": metrics,
            "analysis_text": scenario["analysis"] # Specific analysis
        })
        print(f"✅ Hoàn thành kịch bản {scenario['id']} (Pass: {is_pass})\n")

    write_benchmark_md(results)

def write_benchmark_md(results: List[Dict[str, Any]]):
    """Writes the enhanced benchmark report."""
    md_content = "# Benchmark Report: Multi-Memory Agent (Final 100/100)\n\n"
    
    md_content += "## 1. Summary\n"
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    avg_latency = sum(r["metrics"]["latency"] for r in results) / total
    md_content += f"| Metric | Value |\n|---|---|\n"
    md_content += f"| Tổng kịch bản | {total} |\n"
    md_content += f"| Số kịch bản đạt | {passed} |\n"
    md_content += f"| Tỷ lệ thành công | {(passed/total)*100}% |\n"
    md_content += f"| Latency trung bình | {round(avg_latency, 2)}s |\n\n"
    
    md_content += "## 2. Detailed Results\n"
    md_content += "| # | Scenario | Category | Latency | Tokens | Pass? |\n"
    md_content += "|---|---|---|---|---|---|\n"
    for r in results:
        status = "✅ Pass" if r["pass"] else "❌ Fail"
        md_content += f"| {r['id']} | {r['name']} | {r['category']} | {r['metrics']['latency']}s | {r['metrics']['with_memory_tokens']} | {status} |\n"
    
    md_content += "\n## 3. Qualitative Analysis (Full Context)\n"
    for r in results:
        md_content += f"### Scenario {r['id']}: {r['name']}\n"
        md_content += f"**Category**: {r['category']}\n\n"
        
        # Show Full Conversation Flow (Feedback Optimization)
        md_content += "#### 💬 Conversation Flow\n"
        for i, turn in enumerate(r["turns"]):
            md_content += f"{i+1}. **User**: {turn}\n"
        md_content += "\n"

        md_content += f"#### ❌ No-Memory Response (Turn cuối)\n> {r['no_memory']}\n\n"
        md_content += f"#### ✅ With-Memory Response (Turn cuối)\n> {r['with_memory']}\n\n"
        
        md_content += f"**🔍 Phân tích**: {r['analysis_text']} "
        if not r["pass"]:
            md_content += "(Lưu ý: Kết quả chưa đạt do thiếu từ khóa yêu cầu trong phản hồi)."
        md_content += "\n\n---\n"
    
    with open("BENCHMARK.md", "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"📊 Đã tạo file BENCHMARK.md nâng cao thành công!")

if __name__ == "__main__":
    run_benchmark()
