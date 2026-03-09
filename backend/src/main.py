from fastapi import FastAPI

app = FastAPI(title="YNAB Flow API")

@app.get("/health")
def health_check():
    return {"status": "ok"}
