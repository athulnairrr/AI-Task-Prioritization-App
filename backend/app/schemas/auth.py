from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    """Identity derived from a verified Supabase JWT. Never trust these
    values if they instead came from client-supplied request data."""

    id: str
    email: str | None = None


class TenantMembership(BaseModel):
    tenant_id: str
    tenant_name: str
    role: str
