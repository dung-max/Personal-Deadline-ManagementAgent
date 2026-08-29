from fastapi import FastAPI

app = FastAPI(
    title="Personal Deadline Management Agent",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}