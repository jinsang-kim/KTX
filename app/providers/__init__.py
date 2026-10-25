from app.config import Settings
from app.providers.base import ProviderError, SoldOutError, TrainProvider

__all__ = ["ProviderError", "SoldOutError", "TrainProvider", "create_provider"]


def create_provider(settings: Settings) -> TrainProvider:
    if settings.provider == "korail":
        from app.providers.korail import KorailProvider

        return KorailProvider(settings.korail_id, settings.korail_pw)

    from app.providers.mock import MockProvider

    return MockProvider()
