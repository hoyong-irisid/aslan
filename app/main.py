from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.handlers import handle_chat
from config.settings import get_settings


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    region_hint: str | None = Field(
        default=None,
        description="Optional ISO-3166 alpha-2 country for regional contacts (e.g. AE, ID).",
    )


class ChatResponse(BaseModel):
    reply: str


app = FastAPI(title="ASLAN Chat API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    reply = handle_chat(body.message, body.region_hint)
    return ChatResponse(reply=reply)


def run() -> None:
    settings = get_settings()
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.aslan_api_host,
        port=settings.aslan_api_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
