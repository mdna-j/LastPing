from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

for path in ['/', '/health']:
    r = client.get(path)
    print(path, r.status_code, r.json())
