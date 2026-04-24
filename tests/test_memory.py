import sys
import os
import unittest

# Thêm thư mục hiện tại vào sys.path để import được architecture
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from architecture import MemoryManager

class TestMemoryStack(unittest.TestCase):
    def setUp(self):
        self.user_id = "test_user_123"
        self.manager = MemoryManager(self.user_id)
        # Clear data trước khi test
        self.manager.delete_all_user_data(self.user_id)

    def test_short_term(self):
        self.manager.short_term.add("user", "Hello")
        self.manager.short_term.add("assistant", "Hi there!")
        recent = self.manager.short_term.get_recent(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["role"], "user")

    def test_long_term_conflict(self):
        # Test Rubric Item 3: Conflict handling
        self.manager.long_term.set_fact(self.user_id, "allergy", "sữa bò")
        self.manager.long_term.update_fact(self.user_id, "allergy", "đậu nành")
        
        profile = self.manager.long_term.get_profile(self.user_id)
        self.assertEqual(profile["facts"]["allergy"], "đậu nành")
        print(f"✅ Conflict Resolution Test: {profile['facts']['allergy']}")

    def test_episodic(self):
        self.manager.episodic.save_episode(
            self.user_id, 
            task="debug docker", 
            trajectory=["check logs", "restart container"],
            outcome="success", 
            reflection="Cần kiểm tra port mapping trước"
        )
        episodes = self.manager.episodic.recall(self.user_id, k=1)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["task"], "debug docker")

    def test_semantic(self):
        self.manager.semantic.clear()
        self.manager.semantic.add_documents(
            ["Docker là công cụ containerization.", "Python là ngôn ngữ lập trình."],
            metadatas=[{"topic": "docker"}, {"topic": "python"}]
        )
        results = self.manager.semantic.search("Công cụ container là gì?")
        self.assertTrue(len(results) > 0)
        self.assertIn("Docker", results[0])

if __name__ == "__main__":
    unittest.main()
