from fastapi import APIRouter
from fastapi.responses import JSONResponse
import logging

router = APIRouter()

logger = logging.getLogger(__name__)

@router.get("/status", response_model=dict)
async def get_status():
    """Return the current Lemonade state.

    The function attempts to import the Lemonade provider and query its
    status.  If the provider is unavailable or an error occurs, a generic
    message is returned so the UI can display an appropriate state.
    """
    try:
        from gaia.llm.providers.lemonade import get_status as lemonade_get_status
        status = lemonade_get_status()
        if not isinstance(status, dict):
            status = {"state": "unknown", "message": str(status)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Lemonade status unavailable: %s", exc)
        status = {"state": "unknown", "message": "Cannot connect to GAIA agent"}
    return JSONResponse(content=status)
