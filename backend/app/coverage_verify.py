"""用药覆盖日历内置核验断言（只读）。

对照题目要求核验：
- 规定样例（30 片 1/日 → 10 日后 2/日、住院 4 日、退 6 片）两条路线的
  覆盖区间与库存余额；
- 可归责的真实超量重叠天数、跨处方剩余库存与未匹配退货导致的未决来源；
- 同成分不同剂型库存不相加；
- 来源账每笔天数贡献已随 CoverageRun 留档；
- 该能力不写入/改变父任务案件定性。
"""
from sqlalchemy.orm import Session

from .models import Case, CoverageRun, CoverageScenario
from .services.coverage import analyze_person


def _chk(key, name, passed, expected, actual, detail=""):
    return {"key": key, "name": name, "passed": bool(passed),
            "expected": expected, "actual": actual, "detail": detail}


def _focus(result: dict) -> dict:
    key = result["focus_pool_key"]
    return next(p for p in result["pools"] if p["summary"]["pool_key"] == key)


def run_coverage_verify(db: Session) -> dict:
    checks = []
    scenarios = db.query(CoverageScenario).order_by(CoverageScenario.code).all()
    by_code = {s.code: s for s in scenarios}
    results = {}
    for sc in scenarios:
        results[sc.code] = analyze_person(
            db, sc.person_code, window_start=sc.window_start, days=sc.days,
            focus_group=sc.focus_group, focus_form_class=sc.focus_form_class)

    # 1) S1 规定演算样例：区间 + 余额精确值 -----------------------------
    r1 = results["COV-S1"]
    p1 = _focus(r1)
    s1 = p1["summary"]
    line1 = p1["route_a_intervals"][0]
    bal1 = {r["date"]: r["end_balance"] for r in p1["route_b_balance"]}
    s1_ok = (
        line1["claim_end"] == "2026-03-21"
        and line1["active_claim_days_total"] == 17
        and s1["hospital_days"] == 4
        and s1["excess_days"] == 0
        and bal1.get("2026-03-10") == 20
        and bal1.get("2026-03-12") == 16
        and bal1.get("2026-03-17") == 8
        and bal1.get("2026-03-21") == 0
        and bal1.get("2026-03-22") == 0
        and s1["walk_consumed_base"] == 24
        and s1["total_return_base"] == 6
        and s1["balance_identity_holds"]
    )
    checks.append(_chk(
        "cov_sample_interval_balance",
        "样例：30片1/日→2/日+住院4日+退6片，覆盖至03-21且余额逐日复算无误",
        s1_ok,
        "claim_end=2026-03-21；有效覆盖17天/住院4天；余额 03-10=20,03-12=16,"
        "03-17末=8,03-21末=0；消耗24+退6=30",
        {"claim_end": line1["claim_end"],
         "active_days": line1["active_claim_days_total"],
         "hospital_days": s1["hospital_days"],
         "bal": {"03-10": bal1.get("2026-03-10"), "03-12": bal1.get("2026-03-12"),
                 "03-17": bal1.get("2026-03-17"), "03-21": bal1.get("2026-03-21")},
         "consumed": s1["walk_consumed_base"], "returned": s1["total_return_base"],
         "identity": s1["balance_identity_holds"]}))

    # 2) 两条路线对真实超量天数一致 --------------------------------------
    dual = {code: _focus(results[code])["summary"]["dual_route_match"]
            for code in ("COV-S1", "COV-S2", "COV-S3", "COV-S4")}
    corr = {code: (_focus(results[code])["summary"]["excess_days"],
                   _focus(results[code])["summary"]["balance_excess_corroborated_days"])
            for code in dual}
    dual_ok = all(dual.values()) and all(a == b for a, b in corr.values())
    checks.append(_chk(
        "cov_two_routes_agree",
        "区间切段法与库存余额法对“真实超量覆盖天数”结论一致（余额佐证 available>need）",
        dual_ok, "四个样例 dual_route_match=true 且 excess==balance_corroborated",
        {"dual_route_match": dual, "excess_vs_balance": corr}))

    # 3) S2 可归责重叠 + 剩余库存口径未定 --------------------------------
    p2 = _focus(results["COV-S2"])
    s2 = p2["summary"]
    ex_run = next((a for a in p2["attributions"] if a["status"] == "excess"), None)
    bal2 = {r["date"]: r["end_balance"] for r in p2["route_b_balance"]}
    s2_ok = (
        s2["excess_days"] == 10
        and ex_run and ex_run["start"] == "2026-03-16"
        and ex_run["end"] == "2026-03-25" and ex_run["days"] == 10
        and s2["coexist_days_balance"] == 15
        and s2["caliber_leftover_days"] == 5
        and bal2.get("2026-03-30") == 10
    )
    checks.append(_chk(
        "cov_attributable_overlap",
        "双源：10天可归责真实超量(03-16~25)，另5天剩余库存裁定口径未定(03-26~30)",
        s2_ok,
        "excess=10 [03-16..03-25]；物理共存15；caliber=5 [03-26..03-30]；03-30余额10",
        {"excess_days": s2["excess_days"],
         "excess_span": ex_run and [ex_run["start"], ex_run["end"]],
         "coexist": s2["coexist_days_balance"],
         "caliber": s2["caliber_leftover_days"],
         "balance_0330": bal2.get("2026-03-30")}))

    # 4) S3 未决来源 + 漏记停药敏感性 ------------------------------------
    p3 = _focus(results["COV-S3"])
    s3 = p3["summary"]
    und_runs = [a for a in p3["attributions"] if a["status"] == "undetermined"]
    und_dates = []
    for a in und_runs:
        und_dates.append((a["start"], a["end"], a["days"], a["reasons"]))
    s3_ok = (
        s3["excess_days"] == 1
        and s3["undetermined_attribution_days"] == 2
        and und_runs and und_runs[0]["start"] == "2026-03-16"
        and und_runs[0]["end"] == "2026-03-17"
        and "unlinked_return" in und_runs[0]["reasons"]
        and p3["sensitivity"]["missing_stop_would_add_days"] == 3
    )
    checks.append(_chk(
        "cov_unresolved_sources",
        "停药+未匹配退货：03-15真实超量1天，03-16~17归属未定2天；漏记停药虚增3天",
        s3_ok,
        "excess=1(03-15)；undetermined=2(03-16~17,unlinked_return)；"
        "missing_stop_adds=3",
        {"excess": s3["excess_days"], "undetermined_runs": und_dates,
         "missing_stop_adds": p3["sensitivity"]["missing_stop_would_add_days"]}))

    # 5) S4 等效关系 + 剂型隔离 ------------------------------------------
    r4 = results["COV-S4"]
    p4 = _focus(r4)
    s4 = p4["summary"]
    od_pool = next((p for p in r4["pools"]
                    if p["summary"]["form_class"] == "orally_disintegrating"), None)
    guard = next((g for g in r4["form_guards"]
                  if g["type"] == "form_separation"), None)
    s4_ok = (
        len(r4["pools"]) == 2
        and s4["excess_days"] == 21
        and od_pool is not None
        and od_pool["summary"]["covered_days"] == 14
        and od_pool["summary"]["excess_days"] == 0
        and guard is not None
        and guard["pools_qty"].get("tablet_immediate") == 60
        and guard["pools_qty"].get("orally_disintegrating") == 14
        and guard["naive_same_ingredient_qty"] == 74
    )
    checks.append(_chk(
        "cov_dosage_form_separation",
        "同成分同剂型两品牌并池(21天超量)；口崩片另池覆盖14天，60与14不相加(朴素74为误)",
        s4_ok,
        "pools=2；普通片 excess=21/qty60；口崩片 covered=14/excess0/qty14；naive=74 被拦截提示",
        {"pool_count": len(r4["pools"]), "tablet_excess": s4["excess_days"],
         "od_covered": od_pool and od_pool["summary"]["covered_days"],
         "guard": guard and {"pools_qty": guard["pools_qty"],
                             "naive": guard["naive_same_ingredient_qty"]}}))

    # 6) 来源账每笔天数贡献 + 留档 ---------------------------------------
    contrib_ok = True
    missing = []
    for code, res in results.items():
        for p in res["pools"]:
            for ln in p["route_a_intervals"]:
                if not ln.get("source_code") or "active_claim_days_in_window" not in ln:
                    contrib_ok = False
                    missing.append(ln.get("code"))
    archived = db.query(CoverageRun).count()
    runs_have_contrib = all(
        bool((run.result or {}).get("pools")) for run in db.query(CoverageRun).all())
    ledger_ok = contrib_ok and archived >= len(scenarios) and runs_have_contrib
    checks.append(_chk(
        "cov_source_day_contributions",
        "来源账保留每笔发药的有效区间/切段与窗内天数贡献，并已 CoverageRun 留档",
        ledger_ok,
        "每笔 interval 带 source_code 与 active_claim_days_in_window；"
        f"CoverageRun≥{len(scenarios)} 且结果含 pools",
        {"missing_source_or_days": missing, "coverage_runs": archived,
         "runs_have_pools": runs_have_contrib}))

    # 7) 不写父任务案件定性 ----------------------------------------------
    baseline = {"C-001": "pending", "C-002": "weak_identity",
                "C-003": "excluded_legitimate", "C-004": "closed_returned",
                "C-005": "pending", "C-006": "pending",
                "C-007": "pending", "C-008": "excluded_legitimate"}
    current = {c.code: c.status for c in db.query(Case).all()}
    unchanged = all(current.get(k) == v for k, v in baseline.items())
    checks.append(_chk(
        "cov_no_parent_case_write",
        "覆盖日历为独立只读核查层：复算不写入/改变父任务案件状态或定性",
        unchanged and len(current) == 8,
        "8 个案件状态与基线完全一致；覆盖结果仅存 coverage_run 独立表",
        {"case_status": current}))

    passed = sum(1 for c in checks if c["passed"])
    return {"overall": passed == len(checks), "passed": passed,
            "total": len(checks), "checks": checks}
