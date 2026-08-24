from pydantic_settings import BaseSettings
from pathlib import Path
from typing import List
import json

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    EMAIL_HOST: str
    EMAIL_PORT: int
    EMAIL_USER: str
    EMAIL_PASSWORD: str
    EMAIL_FROM: str
    EMAIL_DESTINO: List[str]
    EMAIL_TLS: bool = True
    EMAIL_SSL: bool = False

    class Config:
        env_file = BASE_DIR / ".env"
        extra = "ignore"  # Ignore extra fields from environment

    @staticmethod
    def parse_email_destino(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [v]
        return v

    def model_post_init(self, __context):
        if isinstance(self.EMAIL_DESTINO, str):
            try:
                self.EMAIL_DESTINO = json.loads(self.EMAIL_DESTINO)
            except json.JSONDecodeError:
                self.EMAIL_DESTINO = [self.EMAIL_DESTINO]

settings = Settings()