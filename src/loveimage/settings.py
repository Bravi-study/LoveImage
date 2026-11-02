from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    kie_ai_api_key: str
    commandcode_api_key: str

    kie_model: str = "gpt-image-2-5-flare-image-to-image"
    kie_resolution: Literal["1K", "2K", "4K"] = "2K"
    llm_model: str = "deepseek/deepseek-v4.1-flash-fast"
    daily_generation_limit: int = 30
    personal_daily_limit: int = 6
    uploads_per_ip: int = 30
    images_per_ip: int = 12

    database_url: str = "postgresql://localhost/loveimage"
    data_dir: Path = Path("data")
    web_dist: Path = Path("web/dist")

    kie_base_url: str = "https://api.kie.ai"
    kie_upload_url: str = "https://kieai.redpandaai.co"
    llm_base_url: str = "https://api.commandcode.ai/provider/v1"

    def shoot_dir(self, shoot_id) -> Path:
        return self.data_dir / "shoots" / str(shoot_id)
