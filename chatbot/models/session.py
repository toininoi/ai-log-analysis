from pydantic import BaseModel
from typing import Optional, List

class UserSession(BaseModel):
    session_id: str
    last_query: Optional[str] = None
    recent_logs: List[str] = []
    active_filter: Optional[str] = None
    last_response: Optional[str] = None