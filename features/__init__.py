"""
Per-feature FastAPI routers.

Each module here is independent — owns its routes and reaches into
core.deps for shared state. Add a new feature by creating a new
APIRouter file and importing it from main.py.
"""
