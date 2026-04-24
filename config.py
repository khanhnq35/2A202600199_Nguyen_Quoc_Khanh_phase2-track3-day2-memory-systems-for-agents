import os
from dotenv import load_dotenv

load_dotenv()

# Memory Storage Paths
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CHROMA_PERSIST_DIR = "./data/chroma_db"
EPISODES_DIR = "./data/episodes"

# Model Config
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash-lite") 
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")
CLOUD_PROJECT = os.getenv("CLOUD_PROJECT")
CLOUD_REGION = os.getenv("CLOUD_REGION", "us-central1")

# Token budget allocation (phần trăm context window)
TOKEN_BUDGET = {
    "short_term": 0.10,   # 10%
    "long_term": 0.04,    # 4%
    "episodic": 0.03,     # 3%
    "semantic": 0.03,     # 3%
    "total_max": 8000,    # max tokens cho memory injection (safety limit)
}

# TTL settings (seconds)
TTL = {
    "prefs": 90 * 86400,    # 90 ngày
    "facts": 30 * 86400,    # 30 ngày
    "sessions": 7 * 86400,  # 7 ngày
}

# Ensure directories exist
os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
os.makedirs(EPISODES_DIR, exist_ok=True)
os.makedirs("./data/knowledge_base", exist_ok=True)
