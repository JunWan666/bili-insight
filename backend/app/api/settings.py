from __future__ import annotations

from typing import Annotated, Protocol, cast

from fastapi import APIRouter, Depends

from app.api.dependencies import get_container
from app.container import ApplicationContainer
from app.schemas.settings import AppSettings
from app.services.settings import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


class _SettingsContainer(Protocol):
    settings_service: SettingsService


def get_settings_service(
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> SettingsService:
    return cast(_SettingsContainer, container).settings_service


@router.get("", response_model=AppSettings)
async def read_settings(
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> AppSettings:
    return await service.get()


@router.put("", response_model=AppSettings)
async def update_settings(
    value: AppSettings,
    service: Annotated[SettingsService, Depends(get_settings_service)],
) -> AppSettings:
    return await service.update(value)
