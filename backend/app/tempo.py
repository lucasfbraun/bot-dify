"""Datas no fuso da empresa: filtros De/Até das telas de administração e horários dos arquivos exportados."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from .config import settings

try:
    FUSO = ZoneInfo(settings.FUSO_HORARIO)
except (ZoneInfoNotFoundError, ValueError):  # imagem sem base de fusos: Brasília não tem horário de verão desde 2019
    FUSO = timezone(timedelta(hours=-3))


def periodo(inicio: str | None, fim: str | None) -> tuple[datetime | None, datetime | None]:
    """Datas AAAA-MM-DD no fuso da empresa -> início do primeiro dia e fim do último dia (com fuso)."""
    try:
        ini = datetime.strptime(inicio, "%Y-%m-%d").replace(tzinfo=FUSO) if inicio else None
        f = datetime.strptime(fim, "%Y-%m-%d").replace(tzinfo=FUSO) + timedelta(days=1, microseconds=-1) if fim else None
    except ValueError:
        raise HTTPException(400, "Data inválida (use AAAA-MM-DD).")
    if ini and f and ini > f:
        raise HTTPException(400, "A data inicial é depois da data final.")
    return ini, f


def iso_utc(d: datetime | None) -> str | None:
    """Mesmo formato gravado no banco (ISO em UTC), para comparar como texto."""
    return d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else None


def hora(ts: float | str | None) -> str:
    """dd/mm/aaaa hh:mm no fuso da empresa, a partir de timestamp (Dify) ou ISO (banco)."""
    if not ts:
        return "—"
    d = datetime.fromisoformat(ts) if isinstance(ts, str) else datetime.fromtimestamp(ts, timezone.utc)
    return d.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")
