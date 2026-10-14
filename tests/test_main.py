from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_read_root():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Hello SRX Backend"
    }
    
def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "system": "SRX",
        "status": "ok",
        "version": "0.1.0",
    }
