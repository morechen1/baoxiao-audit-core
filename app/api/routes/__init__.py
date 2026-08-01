from app.api.routes.explanations import router as explanations_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.screenings import router as screenings_router
from app.api.routes.sources import router as sources_router
from app.api.routes.workflow import router as workflow_router

__all__ = [
    "health_router",
    "explanations_router",
    "knowledge_router",
    "screenings_router",
    "sources_router",
    "workflow_router",
]
