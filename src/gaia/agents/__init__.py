from gaia.logger import get_logger

logger = get_logger(__name__)

# Optional imports for other agents
try:
    from gaia.agents.Llm.app import MyAgent as llm
except ImportError:
    logger.warning("Llm agent not available")
    llm = None

try:
    from gaia.agents.Chaty.app import MyAgent as chaty
except ImportError:
    logger.warning("Chaty agent not available")
    chaty = None

try:
    from gaia.agents.Clip.app import MyAgent as clip
except ImportError:
    logger.warning("Clip agent not available")
    clip = None

try:
    from gaia.agents.Joker.app import MyAgent as joker
except ImportError:
    logger.warning("Joker agent not available")
    joker = None

try:
    from gaia.agents.Rag.app import MyAgent as rag
except ImportError:
    logger.warning("RAG agent not available")
    rag = None
