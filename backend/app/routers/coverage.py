"""患者级用药覆盖日历 API。

独立核查层：只读取处方剂量版本/发药/住院/停药/退货/等效关系进行复算，
结果可留档到 coverage_run，但绝不写入案件（case）定性或状态。
"""
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..coverage_verify import run_coverage_verify
from ..database import get_db
from ..models import CoverageRun, CoverageScenario, Person
from ..security import current_user
from ..services.coverage import WINDOW_DAYS, analyze_person, equivalence_view

router = APIRouter(prefix="/api/coverage", tags=["coverage"],
                   dependencies=[Depends(current_user)])


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else d


def _authorize_person(user, person: Person):
    if user.role == "admin" or "*" in (user.region_scope or []):
        return
    if person.region and person.region in (user.region_scope or []):
        return
    raise HTTPException(status_code=403,
                        detail="该患者不在你的统筹区数据权限内")


def _scenario_dict(s: CoverageScenario) -> dict:
    return {"code": s.code, "title": s.title, "person_code": s.person_code,
            "window_start": _iso(s.window_start), "days": s.days,
            "focus_group": s.focus_group,
            "focus_form_class": s.focus_form_class,
            "tags": s.tags or [], "description": s.description,
            "expected": s.expected or {}}


@router.get("/scenarios")
def list_scenarios(db: Session = Depends(get_db), user=Depends(current_user)):
    rows = db.query(CoverageScenario).order_by(CoverageScenario.code).all()
    items = []
    for s in rows:
        p = db.query(Person).filter_by(code=s.person_code).first()
        if p:
            try:
                _authorize_person(user, p)
            except HTTPException:
                continue
        d = _scenario_dict(s)
        d["person_name"] = p.name if p else s.person_code
        d["region"] = p.region if p else None
        items.append(d)
    return {"items": items}


@router.get("/equivalence")
def get_equivalence(person_code: str | None = None,
                    db: Session = Depends(get_db)):
    return equivalence_view(db, person_code)


def _resolve_params(db, scenario: str | None, person_code: str | None,
                    window_start: str | None, days: int | None,
                    focus_group: str | None, focus_form_class: str | None,
                    user):
    sc = None
    if scenario:
        sc = db.query(CoverageScenario).filter_by(code=scenario).first()
        if not sc:
            raise HTTPException(404, f"覆盖日历剧本 {scenario} 不存在")
        person_code = sc.person_code
        window_start = window_start or _iso(sc.window_start)
        days = days or sc.days
        focus_group = focus_group or sc.focus_group
        focus_form_class = focus_form_class or sc.focus_form_class
    days = days or WINDOW_DAYS
    if not person_code:
        raise HTTPException(400, "必须提供 scenario 或 person_code")
    person = db.query(Person).filter_by(code=person_code).first()
    if not person:
        raise HTTPException(404, f"人员 {person_code} 不存在")
    _authorize_person(user, person)
    if not window_start:
        window_start = datetime.now().date().replace(day=1).isoformat()
    return sc, person_code, window_start, days, focus_group, focus_form_class


@router.get("/analyze")
def analyze(scenario: str | None = None,
            person_code: str | None = None,
            window_start: str | None = None,
            days: int | None = Query(None, ge=7, le=120),
            focus_group: str | None = None,
            focus_form_class: str | None = None,
            db: Session = Depends(get_db), user=Depends(current_user)):
    _, pc, ws, dd, fg, fc = _resolve_params(
        db, scenario, person_code, window_start, days,
        focus_group, focus_form_class, user)
    return analyze_person(db, pc, window_start=ws, days=dd,
                          focus_group=fg, focus_form_class=fc)


@router.get("/verify")
def verify(db: Session = Depends(get_db)):
    return run_coverage_verify(db)


class RunIn(BaseModel):
    scenario_code: str | None = None
    person_code: str | None = None
    window_start: str | None = None
    days: int = WINDOW_DAYS
    focus_group: str | None = None
    focus_form_class: str | None = None
    note: str | None = None


@router.post("/runs")
def save_run(body: RunIn, db: Session = Depends(get_db),
             user=Depends(current_user)):
    sc, pc, ws, dd, fg, fc = _resolve_params(
        db, body.scenario_code, body.person_code, body.window_start,
        body.days, body.focus_group, body.focus_form_class, user)
    result = analyze_person(db, pc, window_start=ws, days=dd,
                            focus_group=fg, focus_form_class=fc)
    run_code = f"RUN-{pc}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
    row = CoverageRun(
        run_code=run_code,
        scenario_code=body.scenario_code or (sc.code if sc else None),
        person_code=pc, window_start=datetime.fromisoformat(ws).date(),
        days=dd, author=user.username,
        params={"focus_group": fg, "focus_form_class": fc, "note": body.note},
        result=result)
    db.add(row)
    db.commit()
    return {"run_code": run_code, "id": row.id, "saved": True}


@router.get("/runs")
def list_runs(scenario_code: str | None = None,
              db: Session = Depends(get_db), user=Depends(current_user)):
    q = db.query(CoverageRun)
    if scenario_code:
        q = q.filter_by(scenario_code=scenario_code)
    rows = q.order_by(CoverageRun.id.desc()).limit(50).all()
    return {"items": [{
        "run_code": r.run_code, "scenario_code": r.scenario_code,
        "person_code": r.person_code, "window_start": _iso(r.window_start),
        "days": r.days, "author": r.author,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "focus_summary": (r.result or {}).get("focus_summary"),
    } for r in rows]}


@router.get("/runs/{run_code}")
def get_run(run_code: str, db: Session = Depends(get_db)):
    r = db.query(CoverageRun).filter_by(run_code=run_code).first()
    if not r:
        raise HTTPException(404, "留档复算不存在")
    return {"run_code": r.run_code, "scenario_code": r.scenario_code,
            "person_code": r.person_code, "window_start": _iso(r.window_start),
            "days": r.days, "author": r.author,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "params": r.params, "result": r.result}
