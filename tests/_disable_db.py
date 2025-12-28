# tests/_disable_db.py
import sys
from unittest.mock import MagicMock

mock_db_module = MagicMock()
mock_db_module.database = MagicMock()
mock_db_module.worker_api_keys = MagicMock()
mock_db_module.workers = MagicMock()

sys.modules["api.database"] = mock_db_module
