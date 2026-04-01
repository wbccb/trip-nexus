import sys
import os
sys.path.append(os.getcwd())
from src.config import Config
from src.frontend.context.storage.test_storage import TestConversationStorage

config = Config()
storage = TestConversationStorage(config)

user_id = "test_user"
session_id = storage.generate_session_id(user_id, "device_1")
print(f"Generated session_id: {session_id}")
storage.store_session(user_id, session_id)

sessions = storage.get_session_list(user_id)
print(f"Sessions for {user_id}: {list(map(dict, sessions))}")