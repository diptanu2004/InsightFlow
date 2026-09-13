"""Central config, loaded once from environment / .env. Import `settings` everywhere else."""
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    confidence_threshold: float = float(os.getenv("CONFIDENCE_THRESHOLD", "0.75"))


settings = Settings()
