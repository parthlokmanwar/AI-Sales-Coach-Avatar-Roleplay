"""
core/rbac.py
────────────
Role-Based Access Control + multi-tenant scoping dependencies.

These FastAPI dependencies are the single choke point through which every
protected endpoint resolves *who* is calling, *what role* they hold, and *which
company's data* they are allowed to touch. Building features on top of these
(rather than ad-hoc `if role == ...` checks) is what keeps tenant isolation
correct as the surface area grows.

Usage examples (used from Phase 1 onward):

    @router.get("/api/platform/companies")
    async def list_companies(user=Depends(require_super_admin)):
        ...

    @router.post("/api/admin/agents")
    async def add_agent(user=Depends(require_company_admin)):
        company_id = user["company_id"]      # guaranteed present + scoped
        ...

    @router.get("/api/me/reports")
    async def my_reports(scope=Depends(tenant_scope)):
        # scope.company_id / scope.user_id are pre-resolved and safe to filter on
        ...
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from core.models import ROLE_COMPANY_ADMIN, ROLE_SALES_AGENT, ROLE_SUPER_ADMIN


def get_current_user(request: Request) -> dict:
    """Return the user dict the gate middleware attached to the request.

    The AuthGateMiddleware (main.py) has already validated the session token and
    set `request.state.user`; if it is missing the request was not authenticated.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required.")
    return user


def require_roles(*roles: str):
    """Dependency factory — allow only the given roles."""
    def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="You do not have permission to perform this action.")
        return user
    return _dep


# Convenience dependencies for the three tiers.
require_super_admin = require_roles(ROLE_SUPER_ADMIN)
require_company_admin = require_roles(ROLE_SUPER_ADMIN, ROLE_COMPANY_ADMIN)
require_agent = require_roles(ROLE_SUPER_ADMIN, ROLE_COMPANY_ADMIN, ROLE_SALES_AGENT)


@dataclass
class TenantScope:
    """Pre-resolved isolation scope for repository queries.

    actor_id   : the caller's real user id — ALWAYS set. Use this to stamp the
                 owner when CREATING rows (so admins' own activity is theirs too).
    user_id    : the read FILTER — set for sales agents (they see ONLY their own
                 rows); None for admins (who see all rows within their company).
    company_id : the tenant whose data the caller may access (None = all, for
                 super admins operating platform-wide).
    role       : the caller's role.
    """
    actor_id: Optional[str]
    user_id: Optional[str]
    company_id: Optional[str]
    role: str

    @property
    def is_super_admin(self) -> bool:
        return self.role == ROLE_SUPER_ADMIN

    @property
    def own_data_only(self) -> bool:
        return self.role == ROLE_SALES_AGENT


def tenant_scope(user: dict = Depends(get_current_user)) -> TenantScope:
    """Resolve the caller into a TenantScope for safe, isolated data access."""
    role = user.get("role")
    company_id = user.get("company_id")
    actor_id = user.get("id")
    # Agents are restricted to their own rows; admins to their whole company;
    # super admins are unrestricted (company_id None = no tenant filter).
    user_id = actor_id if role == ROLE_SALES_AGENT else None
    return TenantScope(actor_id=actor_id, user_id=user_id, company_id=company_id, role=role)
