

from fastapi import FastAPI

app = FastAPI(
    title="SRX",
    description="Scenario and Evidence Analysis System",
    version="0.1.0",
)


@app.get("/")
def read_root():
    return {"message": "Hello SRX Backend"}


@app.get("/health")
def health_check():
    return {
        "system": "SRX",
        "status": "ok",
        "version": "0.1.0",
    }
