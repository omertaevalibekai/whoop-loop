import uvicorn

from whoop_loop.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "whoop_loop.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )
