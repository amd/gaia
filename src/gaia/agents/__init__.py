# Import base LLM agent which is always required
from gaia.agents.Llm.app import MyAgent as llm

# Optional imports for other agents
try:
    from gaia.agents.Chaty.app import MyAgent as chaty
except ImportError:
    chaty = None

try:
    from gaia.agents.Clip.app import MyAgent as clip
except ImportError:
    clip = None
