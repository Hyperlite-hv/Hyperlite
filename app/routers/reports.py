"""The weekly report (app/core/weekly_report.py): its settings, a preview and a sending now, for administrators."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import weekly_report
from app.core.audit import log_action
from app.core.security import require_area

router = APIRouter(prefix="/reports", tags=["notifications"])


class WeeklySettings(BaseModel):
    enabled: bool
    weekday: int
    hour: int


@router.get("/weekly")
def get_weekly(user: dict = Depends(require_area("alert.view"))):
    """The settings, when it was last sent, and the report as it would be sent now (its data and its text)."""
    report = weekly_report.current()
    return {
        "settings": weekly_report.settings(),
        "last_sent": weekly_report.last_sent(),
        "report": report,
        "text": weekly_report.text(report),
    }


@router.put("/weekly/settings")
def put_weekly_settings(payload: WeeklySettings, user: dict = Depends(require_area("alert.manage"))):
    try:
        value = weekly_report.save_settings(payload.enabled, payload.weekday, payload.hour)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "weekly_report_settings", "notifications", "success", str(value))
    return value


@router.post("/weekly/send")
def send_weekly(user: dict = Depends(require_area("alert.manage"))):
    """Sends the report now to the channels that take the weekly_report event."""
    weekly_report.send(username=user["username"])
    return {"sent": True}
