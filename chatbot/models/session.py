from pydantic import BaseModel
from typing import Optional, List, Literal
from datetime import datetime

class UserSession(BaseModel):
    session_id: str
    last_query: Optional[str] = None
    recent_logs: List[str] = []
    active_filter: Optional[str] = None
    last_response: Optional[str] = None
    last_strategy: Optional[str] = None  # RL strategy used
    feedback: Optional[Literal["👍", "👎"]] = None
    updated_at: Optional[datetime] = None
