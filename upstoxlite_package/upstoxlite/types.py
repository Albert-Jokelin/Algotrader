from pydantic import BaseModel
from typing import Optional, Dict, Any

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: Optional[int]
    refresh_token: Optional[str]
    scope: Optional[str]
    user: Optional[Dict[str, Any]]

class Profile(BaseModel):
    client_id: str
    name: Optional[str]
    email: Optional[str]
    mobile: Optional[str]

class OrderResponse(BaseModel):
    order_id: str
    status: str
    message: Optional[str]
    data: Optional[Dict[str, Any]]

class Quote(BaseModel):
    symbol: str
    last_price: float
    timestamp: int
    raw: Optional[Dict[str, Any]]
