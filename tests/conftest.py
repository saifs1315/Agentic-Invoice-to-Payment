import os


# The fake runtime is explicitly test-only. Production defaults remain mandatory Ollama.
os.environ["APP_ENV"] = "test"
os.environ["AGENT_RUNTIME"] = "fake"
os.environ["DATABASE_URL"] = "memory://"
os.environ["ERP_MODE"] = "mock"
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = ""
