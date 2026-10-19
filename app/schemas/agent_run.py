from datetime import datetime
from pydantic import BaseModel, ConfigDict


class AgentRunResponse(BaseModel):
    model_config=ConfigDict(from_attributes=True)
    id: str
    agent_name: str
    agent_version: str
    engine: str
    status: str
    task: str
    tasks: list[dict]
    warnings: list[str]
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
