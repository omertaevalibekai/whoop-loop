import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )
