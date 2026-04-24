import os
import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

import tiktoken
import redis
import chromadb
from chromadb.utils import embedding_functions
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from config import (
    REDIS_URL, CHROMA_PERSIST_DIR, EPISODES_DIR, 
    MODEL_NAME, EMBEDDING_MODEL, TTL
)

def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    """Counts the number of tokens in a text string.

    Args:
        text (str): The text to count tokens for.
        model (str): The model encoding to use. Defaults to "gpt-3.5-turbo".

    Returns:
        int: The number of tokens.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))

class ShortTermMemory:
    """Sliding window conversation buffer (Working Memory).
    
    Attributes:
        max_messages (int): Maximum number of messages to keep in the buffer.
        messages (List[Dict[str, str]]): List of messages in the buffer.
    """
    def __init__(self, max_messages: int = 20):
        """Initializes ShortTermMemory with a max message limit."""
        self.messages: List[Dict[str, str]] = []
        self.max_messages = max_messages

    def add(self, role: str, content: str) -> None:
        """Adds a message to the short-term memory buffer.

        Args:
            role (str): The role of the speaker (user, assistant, system).
            content (str): The message content.
        """
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self.max_messages:
            self.messages.pop(0)

    def get_recent(self, k: int = 10) -> List[Dict[str, str]]:
        """Retrieves the k most recent messages.

        Args:
            k (int): Number of recent messages to retrieve.

        Returns:
            List[Dict[str, str]]: List of recent messages.
        """
        return self.messages[-k:]

    def trim(self, token_budget: int) -> List[Dict[str, str]]:
        """Trims messages to fit within a specific token budget.

        Args:
            token_budget (int): Maximum number of tokens allowed.

        Returns:
            List[Dict[str, str]]: A list of messages that fit the budget.
        """
        current_tokens = 0
        trimmed_messages = []
        for msg in reversed(self.messages):
            msg_tokens = count_tokens(msg["content"])
            if current_tokens + msg_tokens > token_budget:
                break
            trimmed_messages.insert(0, msg)
            current_tokens += msg_tokens
        return trimmed_messages

    def clear(self) -> None:
        """Clears all messages from the buffer."""
        self.messages = []

    def to_langchain_messages(self) -> List[BaseMessage]:
        """Converts internal message representation to LangChain message objects.

        Returns:
            List[BaseMessage]: List of LangChain messages.
        """
        lc_msgs = []
        for msg in self.messages:
            if msg["role"] == "user":
                lc_msgs.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                lc_msgs.append(AIMessage(content=msg["content"]))
            elif msg["role"] == "system":
                lc_msgs.append(SystemMessage(content=msg["content"]))
        return lc_msgs

class LongTermMemory:
    """Redis-backed user profile store (Declarative Memory).
    
    Handles persistent storage of facts and preferences with a fallback to local dictionary.
    
    Attributes:
        redis_url (str): URL for the Redis instance.
        is_connected (bool): Whether the system is successfully connected to Redis.
    """
    def __init__(self, redis_url: str = REDIS_URL):
        """Initializes LongTermMemory and attempts connection to Redis."""
        try:
            self.redis = redis.from_url(redis_url, decode_responses=True)
            self.redis.ping()
            self.is_connected = True
        except Exception:
            print("⚠️ Redis không khả dụng. Chuyển sang dùng Dict fallback.")
            self.redis_fallback: Dict[str, Dict[str, Any]] = {}
            self.is_connected = False

    def _get_key(self, user_id: str, category: str) -> str:
        """Generates a Redis key for a given user and category."""
        return f"user:{user_id}:{category}"

    def set_fact(self, user_id: str, key: str, value: Any, category: str = "facts") -> None:
        """Stores a fact or preference for a user.

        Args:
            user_id (str): The unique identifier for the user.
            key (str): The key for the fact (e.g., 'name', 'allergy').
            value (Any): The value to store.
            category (str): Category (facts, prefs). Defaults to "facts".
        """
        if self.is_connected:
            redis_key = self._get_key(user_id, category)
            self.redis.hset(redis_key, key, json.dumps(value))
            ttl_seconds = TTL.get(category, 30 * 86400)
            self.redis.expire(redis_key, ttl_seconds)
        else:
            if user_id not in self.redis_fallback:
                self.redis_fallback[user_id] = {}
            if category not in self.redis_fallback[user_id]:
                self.redis_fallback[user_id][category] = {}
            self.redis_fallback[user_id][category][key] = value

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        """Retrieves the full profile (facts + preferences) for a user.

        Args:
            user_id (str): The unique identifier for the user.

        Returns:
            Dict[str, Any]: A dictionary containing facts and preferences.
        """
        profile = {}
        for cat in ["facts", "prefs"]:
            if self.is_connected:
                data = self.redis.hgetall(self._get_key(user_id, cat))
                profile[cat] = {k: json.loads(v) for k, v in data.items()}
            else:
                profile[cat] = self.redis_fallback.get(user_id, {}).get(cat, {})
        return profile

    def update_fact(self, user_id: str, key: str, new_value: Any) -> None:
        """Updates a fact, applying 'Recency Wins' conflict resolution.

        Args:
            user_id (str): The unique identifier for the user.
            key (str): The key for the fact.
            new_value (Any): The new value to store.
        """
        self.set_fact(user_id, key, new_value, category="facts")

    def delete_user(self, user_id: str) -> None:
        """Deletes all data for a specific user (Right to be Forgotten).

        Args:
            user_id (str): The unique identifier for the user.
        """
        if self.is_connected:
            for cat in ["facts", "prefs", "sessions"]:
                self.redis.delete(self._get_key(user_id, cat))
        else:
            self.redis_fallback.pop(user_id, None)

@dataclass
class Episode:
    """Represents a single episodic memory entry."""
    task: str
    trajectory: List[str]
    outcome: str
    reflection: str
    timestamp: float = time.time()

class EpisodicMemory:
    """JSON-based episodic memory store.
    
    Attributes:
        storage_path (str): Directory path where episode logs are stored.
    """
    def __init__(self, storage_path: str = EPISODES_DIR):
        """Initializes EpisodicMemory and ensures storage directory exists."""
        self.storage_path = storage_path
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)

    def _get_file_path(self, user_id: str) -> str:
        """Returns the file path for a user's episodic logs."""
        return os.path.join(self.storage_path, f"{user_id}_episodes.json")

    def save_episode(self, user_id: str, task: str, trajectory: List[str], 
                     outcome: str, reflection: str) -> None:
        """Saves a new episode for a user.

        Args:
            user_id (str): The unique identifier for the user.
            task (str): The task description.
            trajectory (List[str]): List of actions taken.
            outcome (str): The result of the task.
            reflection (str): AI's reflection on the task.
        """
        episode = Episode(task=task, trajectory=trajectory, outcome=outcome, reflection=reflection)
        file_path = self._get_file_path(user_id)
        
        episodes = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                episodes = json.load(f)
        
        episodes.append(asdict(episode))
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(episodes, f, ensure_ascii=False, indent=2)

    def recall(self, user_id: str, k: int = 3) -> List[Dict[str, Any]]:
        """Recalls the k most recent episodes for a user.

        Args:
            user_id (str): The unique identifier for the user.
            k (int): Number of episodes to recall.

        Returns:
            List[Dict[str, Any]]: List of recalled episodes.
        """
        file_path = self._get_file_path(user_id)
        if not os.path.exists(file_path):
            return []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            episodes = json.load(f)
        
        return episodes[-k:]

    def clear_user(self, user_id: str) -> None:
        """Clears all episodic memory for a specific user.

        Args:
            user_id (str): The unique identifier for the user.
        """
        file_path = self._get_file_path(user_id)
        if os.path.exists(file_path):
            os.remove(file_path)

class SemanticMemory:
    """ChromaDB-backed vector store for domain knowledge (Semantic Memory).
    
    Attributes:
        client (chromadb.PersistentClient): The ChromaDB client.
        collection (chromadb.Collection): The collection for knowledge storage.
    """
    def __init__(self, persist_dir: str = CHROMA_PERSIST_DIR, collection_name: str = "knowledge"):
        """Initializes SemanticMemory and ensures collection exists."""
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_documents(self, texts: List[str], metadatas: Optional[List[Dict[str, Any]]] = None) -> None:
        """Adds documents to the semantic memory.

        Args:
            texts (List[str]): List of text documents.
            metadatas (Optional[List[Dict[str, Any]]]): Optional metadata for each document.
        """
        ids = [f"id_{int(time.time()*1000)}_{i}" for i in range(len(texts))]
        self.collection.add(
            documents=texts,
            metadatas=metadatas,
            ids=ids
        )

    def search(self, query: str, k: int = 3) -> List[str]:
        """Searches for relevant documents based on a query string.

        Args:
            query (str): The search query.
            k (int): Number of top results to return.

        Returns:
            List[str]: List of relevant documents.
        """
        results = self.collection.query(
            query_texts=[query],
            n_results=k
        )
        return results['documents'][0] if results['documents'] else []

    def clear(self) -> None:
        """Clears all documents from the semantic memory collection."""
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(name=self.collection.name)

class MemoryManager:
    """Facade for managing all four layers of the memory stack.
    
    Attributes:
        user_id (str): The unique identifier for the user.
        short_term (ShortTermMemory): Managed short-term memory buffer.
        long_term (LongTermMemory): Managed long-term declarative store.
        episodic (EpisodicMemory): Managed episodic memory store.
        semantic (SemanticMemory): Managed semantic knowledge store.
    """
    def __init__(self, user_id: str):
        """Initializes MemoryManager and all sub-memory systems."""
        self.user_id = user_id
        self.short_term_mem = ShortTermMemory()
        self.long_term_mem = LongTermMemory()
        self.episodic_mem = EpisodicMemory()
        self.semantic_mem = SemanticMemory()

    @property
    def short_term(self) -> ShortTermMemory:
        return self.short_term_mem

    @property
    def long_term(self) -> LongTermMemory:
        return self.long_term_mem

    @property
    def episodic(self) -> EpisodicMemory:
        return self.episodic_mem

    @property
    def semantic(self) -> SemanticMemory:
        return self.semantic_mem

    def load_all(self, query: str) -> Dict[str, Any]:
        """Loads data from all backends for context injection.

        Args:
            query (str): The current user query to search semantic memory.

        Returns:
            Dict[str, Any]: A dictionary containing data from all memory layers.
        """
        return {
            "user_profile": self.long_term_mem.get_profile(self.user_id),
            "episodes": self.episodic_mem.recall(self.user_id),
            "semantic_hits": self.semantic_mem.search(query),
            "recent_messages": self.short_term_mem.get_recent()
        }

    def delete_all_user_data(self, user_id: str) -> None:
        """Implements 'Right to be Forgotten' by clearing all user-related data.

        Args:
            user_id (str): The unique identifier for the user.
        """
        self.long_term_mem.delete_user(user_id)
        self.episodic_mem.clear_user(user_id)
        print(f"✅ Đã xóa toàn bộ dữ liệu của user: {user_id}")
