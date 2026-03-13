from pydantic import BaseModel
from typing import Optional, List, Dict, Any


# API Request Models 

class CallRequest(BaseModel):
    """Request to trigger a single outbound call."""
    user_id: str

class BulkCallRequest(BaseModel):
    """Request to trigger multiple outbound calls."""
    user_ids: List[str]

# VAPI Webhook Models 

class ToolCallFunction(BaseModel):
    """Function details within a VAPI tool call."""
    name: str
    arguments: Any  # JSON string or dict depending on VAPI version


class ToolCall(BaseModel):
    """Individual tool call from VAPI."""
    id: str
    type: str = "function"
    function: ToolCallFunction


class VAPIMessage(BaseModel):
    """The message payload from VAPI webhooks."""
    type: str
    toolCallList: Optional[List[ToolCall]] = None
    functionCall: Optional[Dict[str, Any]] = None
    call: Optional[Dict[str, Any]] = None
    artifact: Optional[Dict[str, Any]] = None
    transcript: Optional[str] = None
    summary: Optional[str] = None
    endedReason: Optional[str] = None
    status: Optional[str] = None

    class Config:
        extra = "allow"


class VAPIWebhookPayload(BaseModel):
    """Top-level VAPI webhook payload."""
    message: VAPIMessage

    class Config:
        extra = "allow"


# Response Models 

class ToolCallResult(BaseModel):
    """Result for a single tool call."""
    toolCallId: str
    result: str


class ToolCallResponse(BaseModel):
    """Response sent back to VAPI for tool calls."""
    results: List[ToolCallResult]


class CallResponse(BaseModel):
    """Response for call trigger endpoints."""
    status: str
    call_id: Optional[str] = None
    user_id: str
    message: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    service: str
    version: str