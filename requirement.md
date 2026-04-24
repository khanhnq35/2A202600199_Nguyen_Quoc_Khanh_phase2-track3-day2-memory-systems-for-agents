Xây dựng Hệ thống Multi-Memory với LangGraph

1. Mục tiêu và bối cảnh

Bạn là một AI Engineer chuyên sâu về Agentic Workflow. Hãy hỗ trợ tôi thực hiện Lab 17: Build Multi-Memory Agent dựa trên kiến thức của VinUniversity. Mục tiêu chính là khắc phục nhược điểm stateless của LLM bằng cách xây dựng bộ nhớ ngoài bền vững qua 4 tầng phân cấp.

2. Kiến trúc 4 tầng Memory cần thực hiện

Short-term (Working Memory):

Sử dụng ConversationBufferMemory hoặc Sliding Window.

Giới hạn trong context window (~128K tokens).

Truy cập nhanh, mang tính tạm thời cho session hiện tại.

Long-term (Declarative Memory):

Sử dụng Redis làm kho lưu trữ chính.

Lưu trữ User preferences (ngôn ngữ, style) và Facts (kiến thức người dùng đã chia sẻ).

Thiết lập TTL: prefs 90 ngày, facts 30 ngày, sessions 7 ngày.

Episodic Memory:

Lưu trữ dưới dạng log trải nghiệm có thứ tự (tuple).

Cấu trúc: (task, trajectory, outcome, reflection).

Giúp agent học từ các lỗi sai hoặc thành công trong quá khứ.

Semantic Memory:

Sử dụng ChromaDB (Vector DB).

Lưu trữ Domain Knowledge thông qua Embeddings.

Thực hiện Top-K retrieval dựa trên cosine similarity.

3. Triển khai với LangGraph

Định nghĩa MemoryState cho Graph:

messages: list[BaseMessage] (Short-term)

user_profile: dict (Long-term từ Redis)

episodes: list[dict] (Episodic)

semantic_hits: list[str] (Semantic từ Chroma)

memory_budget: int (Token count)

Các Node cần code:

Node load_memory: Đọc song song dữ liệu từ Redis, Chroma và Episodic log khi bắt đầu.

Memory Router: Phân loại ý định của user để tìm kiếm trong kho dữ liệu phù hợp.

Context Injection: Chèn thông tin vào System Prompt theo thứ tự ưu tiên: Short-term > Long-term > Episodic > Semantic.

Node save_memory: Trích xuất Key Facts bằng LLM và lưu vào Redis/Chroma sau khi task hoàn thành.

4. Ràng buộc kỹ thuật (Constraints)

Context Engineering: Áp dụng 7 lớp (System, Task, User, Memory, Retrieval, Tool, Policy).

Token Budget: Phân bổ Short-term (10%), Long-term (4%), Episodic (3%), Semantic (3%).

Trimming Logic: Khi chạm giới hạn token, thực hiện trim từ dưới lên. Policy Context luôn được giữ lại cuối cùng.

Conflict Resolution: Nếu có mâu thuẫn giữa thông tin cũ và mới, ưu tiên tính cập nhật (Recency wins).

5. Quyền riêng tư & Bảo mật

Thiết kế theo nguyên tắc Privacy-by-Design.

Không lưu trữ thông tin định danh (PII) mặc định.

Triển khai tính năng "Right to be Forgotten": Xóa toàn bộ dữ liệu liên quan khi user yêu cầu.

6. Yêu cầu đầu ra (Deliverables)

File architecture.py: Định nghĩa các class quản lý Redis, Chroma và Log.

File agent.py: Chứa LangGraph workflow hoàn chỉnh.

File benchmark.py: Script chạy thử 10 kịch bản hội thoại để so sánh Agent có memory và Agent không có memory dựa trên:

Response relevance (độ liên quan).

Context utilization (hiệu quả sử dụng ngữ cảnh).

Token efficiency (tối ưu hóa token).