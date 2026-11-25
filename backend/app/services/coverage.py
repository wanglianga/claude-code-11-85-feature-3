"""患者级用药覆盖日历：双路线推导引擎（纯 Python、只读、可复算）。

背景
----
结算/发药行上的“给药期重叠”不必然等于重复取药，可能由以下因素造成或消除：
1. 处方剂量版本调整（如 1 片/日 → 2 片/日），同样数量覆盖天数改变；
2. 住院区间门诊药暂停，住院日不消耗门诊库存，覆盖区间应顺延/留隙；
3. 停药医嘱截断覆盖；
4. 退药核减患者侧可用量；
5. 药品等效关系——只有“同等效组 + 同剂型库存池 + 已知换算系数”才能合并库存，
   同成分但不同剂型（普通片/控缓释片/口崩片/胶囊/口服液/注射剂）不得相加。

两条路线计算同一个“30 日窗内超出规定可用量的天数”：

* 路线 A（区间切段法）：把每笔发药按剂量版本切成有效覆盖区间/段，住院日留隙、
  停药截断、关联退货核减；多笔区间相交即为可归责的重叠天数。解释清晰，
  但跨处方调整时旧方剩余库存如何按新剂量重估，需要显式裁定（见 leftover_policy）。
* 路线 B（库存余额滚动法）：患者—药品库存池逐日滚动，FIFO 批次来源可归因，
  给出每日可用量/余额与多来源共存日。能反映剩余库存，但对漏记停药、漏记退货敏感。

口径分层（异常视图据此区分，不写入任何父任务案件定性）：
* true_excess   真实超量覆盖：来源齐全确定，A 区间相交且 B 余额佐证 available>need；
* undetermined  口径未定：未匹配退货、剩余库存裁定分歧、剂量/换算缺失等；
* explained     已有合法解释（住院暂停/停药/退货/剂型隔离），不计异常。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import combinations
from math import isclose

from sqlalchemy.orm import Session

from ..models import (
    MedDispense,
    MedDoseVersion,
    MedDrugCatalog,
    MedEquivGroup,
    MedReturn,
    MedStay,
    MedStop,
    Person,
)

WINDOW_DAYS = 30
EXTEND_DAYS = 90          # 窗口后继续滚动，确保批次耗尽、覆盖期末可求
CLAIM_HORIZON_DAYS = 200  # 单笔区间法最远推算天数

# 剂型中文标签（前端也有一份，后端用于结论文案）
FORM_LABELS = {
    "tablet_immediate": "普通片",
    "tablet_extended": "控/缓释片",
    "orally_disintegrating": "口崩片",
    "capsule": "胶囊",
    "oral_liquid": "口服液",
    "injection": "注射剂",
    "other": "其他剂型",
}


def _d(x) -> date:
    if x is None:
        return None
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _iso(x) -> str | None:
    if x is None:
        return None
    if isinstance(x, str):
        return x[:10]
    return x.isoformat()


def _daterange(a: date, b: date):
    cur = a
    while cur <= b:
        yield cur
        cur += timedelta(days=1)


# ============================================================ 输入装配
@dataclass
class Line:
    code: str
    source_code: str | None
    qty_base: float
    dispense_date: date
    drug_code: str | None = None
    settle_code: str | None = None
    pharmacy_code: str | None = None


@dataclass
class Version:
    code: str
    daily_dose: float
    start_date: date
    end_date: date | None
    rx_code: str | None = None
    certainty: str = "certain"
    source_code: str | None = None


@dataclass
class Ret:
    code: str
    source_code: str | None
    qty_base: float
    return_date: date
    dispense_code: str | None
    match_status: str


@dataclass
class Stay:
    code: str
    source_code: str | None
    from_date: date
    to_date: date
    scope_group: str


@dataclass
class Stop:
    code: str
    source_code: str | None
    stop_date: date
    certainty: str = "certain"


@dataclass
class PoolInput:
    person_code: str
    group_code: str
    form_class: str
    group_name: str = ""
    dose_unit: str = "片"
    versions: list[Version] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    returns: list[Ret] = field(default_factory=list)
    stays: list[Stay] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)


# ============================================================ 单库存池分析
def analyze_pool(inp: PoolInput, window_start, days: int = WINDOW_DAYS, *,
                 use_stops: bool = True, use_returns: bool = True,
                 compute_sensitivity: bool = True) -> dict:
    ws = _d(window_start)
    we = ws + timedelta(days=days - 1)
    w_ord = set(range(ws.toordinal(), we.toordinal() + 1))

    versions = sorted(inp.versions, key=lambda v: (v.start_date, v.code))
    lines = sorted(inp.lines, key=lambda l: (l.dispense_date, l.code))
    returns = sorted(inp.returns, key=lambda r: (r.return_date, r.code))
    stops = sorted(inp.stops, key=lambda s: s.stop_date)

    # ---------- 规定日剂量 / 住院 / 停药 日历 ----------
    def base_dose(d: date) -> float:
        hit = [v for v in versions
               if v.start_date <= d and (v.end_date is None or d <= v.end_date)]
        if not hit:
            return 0.0
        return max(hit, key=lambda v: v.start_date).daily_dose

    def in_hospital(d: date) -> Stay | None:
        for s in inp.stays:
            if (s.scope_group in ("*", inp.group_code)
                    and s.from_date <= d <= s.to_date):
                return s
        return None

    def stopped_at(d: date) -> Stop | None:
        if not use_stops:
            return None
        for st in stops:
            if st.stop_date <= d:
                # 停药后若有更新的剂量版本生效，则视为恢复，不再处于停药态
                resumed = any(v.start_date > st.stop_date and v.start_date <= d
                              for v in versions)
                if not resumed:
                    return st
        return None

    def need_on(d: date):
        """返回 (need, hospital_stay, stop, base_dose)。"""
        dose = base_dose(d)
        stay = in_hospital(d)
        stop = stopped_at(d)
        if stay is not None:
            return 0.0, stay, stop, dose
        if stop is not None:
            return 0.0, stay, stop, dose
        return dose, stay, stop, dose

    # ============================================================
    # 路线 A：每笔发药的有效覆盖区间 + 剂量切段（独立核算法）
    # ============================================================
    line_results = []
    # line_code -> set(ordinal) 实际分配到用量的“有效覆盖日”
    covered_sets: dict[str, set[int]] = {}

    for ln in lines:
        linked_ret_qty = 0.0
        unlinked_ret_qty = 0.0
        ret_sources = []
        if use_returns:
            for r in returns:
                if r.return_date < ln.dispense_date:
                    continue
                if r.match_status == "linked" and r.dispense_code == ln.code:
                    linked_ret_qty += r.qty_base
                    ret_sources.append(r.source_code or r.code)
                elif r.match_status == "unlinked":
                    unlinked_ret_qty += r.qty_base  # 仅汇总，不摊入单笔下界

        effective_qty = max(0.0, ln.qty_base - linked_ret_qty)
        left = effective_qty
        covered: set[int] = set()
        seg_raw: list[tuple[date, float]] = []  # (day, dose)
        claim_end = None
        horizon_end = ln.dispense_date + timedelta(days=CLAIM_HORIZON_DAYS)
        d = ln.dispense_date
        while d <= horizon_end and left > 1e-9:
            need, _, _, _ = need_on(d)
            if need > 0:
                take = min(need, left)
                # 仅当能覆盖“整日剂量”或为最后残余日时记为覆盖日
                covered.add(d.toordinal())
                seg_raw.append((d, need))
                left -= take
                if left <= 1e-9:
                    claim_end = d
                    break
            d += timedelta(days=1)
        if claim_end is None and covered:
            claim_end = date.fromordinal(max(covered))

        # 按相同日剂量合并连续段
        segments = []
        if seg_raw:
            s0, rate = seg_raw[0]
            prev = s0
            cnt = 1
            for day, rt in seg_raw[1:]:
                if isclose(rt, rate) and (day - prev).days == 1:
                    prev = day
                    cnt += 1
                else:
                    segments.append(_seg(s0, prev, rate, cnt))
                    s0, prev, rate, cnt = day, day, rt, 1
            segments.append(_seg(s0, prev, rate, cnt))

        # 区间跨度内的住院暂停天数（解释“为什么跨度 > 覆盖天数”）
        pause_days = 0
        if claim_end:
            cur = ln.dispense_date
            while cur <= claim_end:
                st = in_hospital(cur)
                if st is not None:
                    pause_days += 1
                cur += timedelta(days=1)

        covered_sets[ln.code] = covered
        line_results.append({
            "code": ln.code,
            "source_code": ln.source_code,
            "drug_code": ln.drug_code,
            "settle_code": ln.settle_code,
            "pharmacy_code": ln.pharmacy_code,
            "dispense_date": _iso(ln.dispense_date),
            "qty_base": _num(ln.qty_base),
            "linked_return_qty": _num(linked_ret_qty),
            "unlinked_return_pool_qty": _num(unlinked_ret_qty),
            "effective_qty": _num(effective_qty),
            "claim_start": _iso(ln.dispense_date) if covered else None,
            "claim_end": _iso(claim_end),
            "segments": segments,
            "active_claim_days_total": len(covered),
            "active_claim_days_in_window": len(covered & w_ord),
            "pause_days_within_span": pause_days,
            "remaining_unallocated": _num(max(0.0, left)),
            "return_sources": ret_sources,
            "uncertain": False,
            "uncertain_reasons": [],
        })

    # ---------- 路线 A：窗口内逐日多线相交 + 归因 ----------
    # 未匹配退货：自退货日起污染该池所有重叠日的可归责性
    unlinked_return_days = set()
    unlinked_return_sources = []
    if use_returns:
        for r in returns:
            if r.match_status == "unlinked":
                unlinked_return_sources.append(r.source_code or r.code)
                for o in w_ord:
                    dd = date.fromordinal(o)
                    if dd >= r.return_date:
                        unlinked_return_days.add(o)

    # day -> 覆盖该行的 line codes
    day_covering: dict[int, list[str]] = {}
    for o in w_ord:
        cov = [lc for lc, cs in covered_sets.items() if o in cs]
        if len(cov) >= 2:
            day_covering[o] = sorted(cov)

    # pair -> list[(ordinal, status, reasons)]
    pair_days: dict[tuple[str, str], list[tuple[int, str, list[str]]]] = {}
    overlap_excess_days: set[int] = set()
    overlap_undetermined_days: set[int] = set()
    day_reasons: dict[int, set[str]] = {}

    for o, cov in day_covering.items():
        reasons = set()
        status = "excess"
        if o in unlinked_return_days:
            status = "undetermined"
            reasons.add("unlinked_return")
        # 剂量版本不确定
        dd = date.fromordinal(o)
        uncertain_ver = [v.code for v in versions
                         if v.certainty != "certain"
                         and v.start_date <= dd
                         and (v.end_date is None or dd <= v.end_date)]
        if uncertain_ver:
            status = "undetermined"
            reasons.add("dose_version_estimated")
        day_reasons[o] = reasons
        if status == "excess":
            overlap_excess_days.add(o)
        else:
            overlap_undetermined_days.add(o)
        for a, b in combinations(cov, 2):
            pair_days.setdefault((a, b), []).append(
                (o, status, sorted(reasons)))

    # ============================================================
    # 路线 B：库存余额逐日滚动（FIFO 批次）
    # ============================================================
    lot_map = {}
    lots: list[dict] = []
    for ln in lines:
        # 批次在发药日才激活（rem 从 0 起，走到发药日再注入 qty），
        # 避免未来发药行被提前计入 opening / on_hand。
        lot = {"code": ln.code, "source_code": ln.source_code,
               "initial": ln.qty_base, "rem": 0.0, "active": False,
               "dispense_date": ln.dispense_date, "depletion_date": None,
               "tainted": False}
        lots.append(lot)
        lot_map[ln.code] = lot

    walk_start = min([ws] + [l.dispense_date for l in lines]
                     + [v.start_date for v in versions])
    walk_end = we + timedelta(days=EXTEND_DAYS)
    daily = []
    bal_excess_corroborated = 0
    coexist_days = set()
    bal_multi_excess_days = set()

    cur = walk_start
    while cur <= walk_end:
        opening = sum(l["rem"] for l in lots)
        inflow = 0.0
        for ln in lines:
            if ln.dispense_date == cur:
                inflow += ln.qty_base
                lot = lot_map[ln.code]
                lot["rem"] = ln.qty_base
                lot["active"] = True

        returned = 0.0
        ret_notes = []
        if use_returns:
            for r in returns:
                if r.return_date != cur:
                    continue
                returned += r.qty_base
                if r.match_status == "linked" and r.dispense_code in lot_map:
                    lot = lot_map[r.dispense_code]
                    take = min(lot["rem"], r.qty_base)
                    lot["rem"] = _num(lot["rem"] - take)
                    if r.qty_base > lot["initial"] + 1e-9 or (
                            lot["rem"] <= 1e-9 and take < r.qty_base - 1e-9):
                        ret_notes.append("linked_return_exceeds_lot")
                else:
                    # 未匹配退货：默认 FIFO 冲减最早批次（口径敏感）
                    left = r.qty_base
                    for lot in sorted(lots, key=lambda x: x["dispense_date"]):
                        if left <= 1e-9:
                            break
                        take = min(lot["rem"], left)
                        lot["rem"] = _num(lot["rem"] - take)
                        left -= take
                    if left > 1e-9:
                        ret_notes.append("return_exceeds_balance")

        available = sum(l["rem"] for l in lots)
        need, stay, stop, base_dose_today = need_on(cur)

        # 当日可用（退货/发药后、消耗前）仍有余额的批次
        on_hand = [l["code"] for l in lots if l["rem"] > 1e-9]
        on_hand_sources = [l["source_code"] for l in lots
                           if l["rem"] > 1e-9 and l["source_code"]]

        consumed = 0.0
        lot_take_today = []
        if need > 0 and available > 1e-9:
            left_need = need
            for lot in sorted(lots, key=lambda x: (x["dispense_date"], x["code"])):
                if left_need <= 1e-9:
                    break
                if lot["rem"] <= 1e-9:
                    continue
                take = min(lot["rem"], left_need)
                lot["rem"] = _num(lot["rem"] - take)
                consumed += take
                left_need -= take
                lot_take_today.append({"line": lot["code"],
                                       "source_code": lot["source_code"],
                                       "units": _num(take)})
                if lot["rem"] <= 1e-9 and lot["depletion_date"] is None:
                    lot["depletion_date"] = cur

        end_balance = sum(l["rem"] for l in lots)
        covered_today = need > 0 and available + 1e-9 >= need
        in_w = cur.toordinal() in w_ord

        if in_w and need > 0 and len(on_hand) >= 2:
            coexist_days.add(cur.toordinal())
            if available > need + 1e-9:
                bal_multi_excess_days.add(cur.toordinal())

        row = {
            "date": _iso(cur),
            "day_no": (cur - ws).days + 1 if 0 <= (cur - ws).days < days else None,
            "in_window": in_w,
            "opening": _num(opening),
            "inflow": _num(inflow),
            "returned": _num(returned),
            "available": _num(available),
            "need": _num(need),
            "base_dose": _num(base_dose_today),
            "consumed": _num(consumed),
            "end_balance": _num(end_balance),
            "covered": covered_today,
            "stockout": need > 0 and not covered_today,
            "hospital": stay.code if stay else None,
            "stopped": stop.code if stop else None,
            "on_hand_lines": on_hand,
            "on_hand_sources": on_hand_sources,
            "k_sources": len(on_hand),
            "fifo_consumption": lot_take_today,
            "ret_notes": ret_notes,
        }
        daily.append(row)
        cur += timedelta(days=1)

    day_by_ord = {date.fromisoformat(r["date"]).toordinal(): r for r in daily}

    # B 对 A 真实超量日的佐证
    for o in overlap_excess_days:
        r = day_by_ord.get(o)
        if r and r["k_sources"] >= 2 and r["available"] > r["need"] + 1e-9:
            bal_excess_corroborated += 1

    # 物理共存但不在 A 相交集合中的日子 -> 剩余库存裁定口径未定
    caliber_days = set()
    for o in coexist_days:
        if o not in day_covering:
            caliber_days.add(o)

    # ---------- 配对归因段 ----------
    attributions = []
    for (a, b), occ in sorted(pair_days.items()):
        # 合并连续且同状态的日期
        occ_sorted = sorted(occ)
        runs = []
        start_o = prev_o = occ_sorted[0][0]
        status = occ_sorted[0][1]
        reasons = set(occ_sorted[0][2])
        for o, st, rs in occ_sorted[1:]:
            if o == prev_o + 1 and st == status:
                prev_o = o
                reasons.update(rs)
            else:
                runs.append(_attr_run(a, b, start_o, prev_o, status, reasons))
                start_o = prev_o = o
                status = st
                reasons = set(rs)
        runs.append(_attr_run(a, b, start_o, prev_o, status, reasons))
        attributions.extend(runs)

    # 口径未定：物理共存 vs 区间相交 的差集，按连续段输出
    caliber_runs = []
    for a, b in _runs_from_days(caliber_days):
        caliber_runs.append({
            "kind": "caliber_leftover",
            "status": "undetermined",
            "start": _iso(a), "end": _iso(b),
            "days": (b - a).days + 1,
            "reason": "cross_rx_leftover_policy",
            "title": "跨处方剩余库存裁定分歧",
            "detail": ("库存余额法显示两笔发药批次当日同时有剩余（物理共存），"
                        "但单笔给药期区间已不相交；是否计为重叠取决于"
                        "“旧方剩余按新剂量重估（库存池化/FIFO）”还是"
                        "“各笔按本处方剂量独立算给药期”，口径未定，不直接定性。"),
        })

    # ---------- 窗口日历（前端格子） ----------
    calendar = []
    for o in sorted(w_ord):
        d = date.fromordinal(o)
        r = day_by_ord.get(o, {})
        covering = day_covering.get(o, [])
        if r.get("hospital"):
            st_status = "hospital"
        elif r.get("stopped"):
            st_status = "stopped"
        elif o in overlap_excess_days:
            st_status = "excess"
        elif o in overlap_undetermined_days or o in caliber_days:
            st_status = "undetermined"
        elif r.get("stockout"):
            st_status = "gap"
        elif r.get("covered"):
            st_status = "covered"
        else:
            st_status = "empty"
        calendar.append({
            "date": _iso(d), "day_no": (d - ws).days + 1,
            "weekday": "一二三四五六日"[d.weekday()],
            "status": st_status,
            "need": r.get("need", 0), "available": r.get("available", 0),
            "end_balance": r.get("end_balance", 0),
            "consumed": r.get("consumed", 0),
            "returned": r.get("returned", 0),
            "inflow": r.get("inflow", 0),
            "k_sources": r.get("k_sources", 0),
            "covering_lines": covering,
            "on_hand_lines": r.get("on_hand_lines", []),
            "fifo_consumption": r.get("fifo_consumption", []),
            "hospital": r.get("hospital"), "stopped": r.get("stopped"),
            "stockout": r.get("stockout", False),
            "reasons": sorted(day_reasons.get(o, set()))
                       + (["caliber_leftover"] if o in caliber_days else []),
        })

    # ---------- 汇总 ----------
    def count_status(name):
        return sum(1 for c in calendar if c["status"] == name)

    total_dispense = sum(l.qty_base for l in lines)
    total_return = sum(r.qty_base for r in returns) if use_returns else 0.0
    walk_consumed = sum(row["consumed"] for row in daily)
    end_balance_final = daily[-1]["end_balance"] if daily else 0.0
    identity_holds = isclose(
        total_dispense - total_return, walk_consumed + end_balance_final,
        abs_tol=1e-6)

    # 批次层补充进 line_results
    for lr in line_results:
        lot = lot_map.get(lr["code"])
        if lot:
            lr["fifo_consumed_total"] = _num(lot["initial"] - lot["rem"])
            lr["lot_remaining_extended"] = _num(lot["rem"])
            lr["depletion_date"] = _iso(lot["depletion_date"])

    summary = {
        "pool_key": f"{inp.group_code}||{inp.form_class}",
        "group_code": inp.group_code,
        "form_class": inp.form_class,
        "form_label": FORM_LABELS.get(inp.form_class, inp.form_class),
        "group_name": inp.group_name,
        "dose_unit": inp.dose_unit,
        "window_start": _iso(ws), "window_end": _iso(we), "days": days,
        "calendar_days": days,
        "covered_days": count_status("covered"),
        "overlap_total_days": len(day_covering),
        "excess_days": len(overlap_excess_days),
        "undetermined_days": len(overlap_undetermined_days | caliber_days),
        "undetermined_attribution_days": len(overlap_undetermined_days),
        "caliber_leftover_days": len(caliber_days),
        "hospital_days": count_status("hospital"),
        "stopped_days": count_status("stopped"),
        "gap_days": count_status("gap"),
        "coexist_days_balance": len(coexist_days),
        "balance_multi_excess_days": len(bal_multi_excess_days),
        "balance_excess_corroborated_days": bal_excess_corroborated,
        "dual_route_match": bal_excess_corroborated == len(overlap_excess_days),
        "total_dispense_base": _num(total_dispense),
        "total_return_base": _num(total_return),
        "walk_consumed_base": _num(walk_consumed),
        "end_balance_extended": _num(end_balance_final),
        "balance_identity_holds": identity_holds,
        "leftover_policy": "fungible_fifo_rerate_to_current_dose",
        "leftover_policy_note": (
            "同池药品视为可互换库存：跨处方剂量调整后，旧方剩余数量按当前有效"
            "日剂量继续消耗（库存池化 + FIFO）。若改按各处方旧剂量独立消化剩余，"
            "会得到不同重叠天数，其差异日列入口径未定。"),
    }

    # ---------- 异常项（跨日聚合，供异常视图） ----------
    anomalies = []
    src_of_line = {l.code: l.source_code for l in lines}
    for run in attributions:
        anomalies.append({
            "kind": "true_excess" if run["status"] == "excess" else "undetermined",
            "status": run["status"],
            "type": "source_overlap",
            "person_code": inp.person_code,
            "group_code": inp.group_code,
            "form_class": inp.form_class,
            "form_label": summary["form_label"],
            "line_codes": [run["line_a"], run["line_b"]],
            "source_codes": [s for s in (src_of_line.get(run["line_a"]),
                                         src_of_line.get(run["line_b"])) if s],
            "start": run["start"], "end": run["end"], "days": run["days"],
            "reasons": run["reasons"],
            "title": ("两笔发药有效覆盖区间相交" if run["status"] == "excess"
                      else "重叠可归责性未定"),
            "detail": (_excess_detail(run, inp) if run["status"] == "excess"
                       else _undetermined_detail(run)),
        })
    for cr in caliber_runs:
        anomalies.append({
            "kind": "undetermined", "status": "undetermined",
            "type": "caliber_leftover",
            "person_code": inp.person_code,
            "group_code": inp.group_code, "form_class": inp.form_class,
            "form_label": summary["form_label"],
            "line_codes": [], "source_codes": [],
            "start": cr["start"], "end": cr["end"], "days": cr["days"],
            "reasons": ["cross_rx_leftover_policy"],
            "title": cr["title"], "detail": cr["detail"],
        })

    # ---------- 敏感性：漏记停药 / 漏记退货 会怎样 ----------
    sensitivity = (_sensitivity(
        inp, ws, days, len(overlap_excess_days), len(day_covering))
        if compute_sensitivity else None)

    return {
        "summary": summary,
        "calendar": calendar,
        "route_a_intervals": line_results,
        "route_b_balance": [r for r in daily if r["in_window"]],
        "attributions": attributions,
        "anomalies": anomalies,
        "sensitivity": sensitivity,
        "unlinked_return_sources": unlinked_return_sources,
    }


def _seg(s0, prev, rate, cnt):
    return {"start": _iso(s0), "end": _iso(prev),
            "daily_dose": _num(rate), "days": cnt}


def _attr_run(a, b, s_o, e_o, status, reasons):
    sd, ed = date.fromordinal(s_o), date.fromordinal(e_o)
    return {"line_a": a, "line_b": b, "status": status,
            "start": _iso(sd), "end": _iso(ed),
            "days": (ed - sd).days + 1, "reasons": sorted(set(reasons))}


def _runs_from_days(days_set: set[int]):
    if not days_set:
        return []
    ords = sorted(days_set)
    out = []
    s = p = ords[0]
    for o in ords[1:]:
        if o == p + 1:
            p = o
        else:
            out.append((date.fromordinal(s), date.fromordinal(p)))
            s = p = o
    out.append((date.fromordinal(s), date.fromordinal(p)))
    return out


def _num(x):
    if x is None:
        return 0
    x = float(x)
    if abs(x - round(x)) < 1e-9:
        return int(round(x))
    return round(x, 3)


def _excess_detail(run, inp: PoolInput) -> str:
    return (f"发药行 {run['line_a']} 与 {run['line_b']} 经剂量版本/住院/停药/退货"
            f"调整后的有效覆盖区间在 {run['start']}~{run['end']}（{run['days']} 天）"
            f"均覆盖同一规定日剂量，且库存余额法佐证当日可用量>规定用量、两个批次同时在库；"
            f"来源齐全确定，列为真实超量覆盖天数（仍只是核查线索，不是案件定性）。")


def _undetermined_detail(run) -> str:
    reason_text = {
        "unlinked_return": "存在未匹配到原发药行的退货记录，无法确定应核减哪一笔的覆盖区间",
        "dose_version_estimated": "存在估算/不确定的剂量版本",
    }
    rs = "；".join(reason_text.get(r, r) for r in run["reasons"]) or "口径依据不足"
    return (f"{run['start']}~{run['end']}（{run['days']} 天）两笔区间表面相交，但{rs}，"
            f"该段重叠的可归责性与天数口径未定，列入未决来源，不确认为真实超量。")


def _sensitivity(inp: PoolInput, ws: date, days: int, recorded_excess: int,
                  recorded_overlap_total: int) -> dict:
    """分别模拟“漏记停药”“漏记退货”，给出会虚增/改变多少重叠。"""
    out = {"recorded_excess_days": recorded_excess,
           "recorded_overlap_total_days": recorded_overlap_total}
    try:
        no_stop = analyze_pool(inp, ws, days, use_stops=False, use_returns=True,
                               compute_sensitivity=False)
        out["if_stop_omitted_excess_days"] = no_stop["summary"]["excess_days"]
        out["if_stop_omitted_overlap_total_days"] = no_stop["summary"]["overlap_total_days"]
        # 漏记停药会让原本被停药解释掉的相交日重新出现（即使它们因别的未决项
        # 被归为口径未定，也仍是“看起来像重叠”的虚增），故按总相交日计。
        out["missing_stop_would_add_days"] = max(
            0, no_stop["summary"]["overlap_total_days"] - recorded_overlap_total)
        out["missing_stop_would_add_excess_days"] = max(
            0, no_stop["summary"]["excess_days"] - recorded_excess)
        out["if_stop_omitted_undetermined_days"] = no_stop["summary"]["undetermined_days"]
    except Exception as e:  # pragma: no cover - 敏感性仅为展示，不应影响主结果
        out["stop_error"] = str(e)
    try:
        no_ret = analyze_pool(inp, ws, days, use_stops=True, use_returns=False,
                              compute_sensitivity=False)
        out["if_return_omitted_excess_days"] = no_ret["summary"]["excess_days"]
        ends = [l.get("claim_end") for l in no_ret["route_a_intervals"]]
        out["if_return_omitted_claim_ends"] = ends
    except Exception as e:  # pragma: no cover
        out["return_error"] = str(e)
    out["note"] = "库存余额滚动法对漏记停药、漏记/错配退货敏感；该面板量化这种敏感性。"
    return out


# ============================================================ DB 装载 + 人员分析
def _source_code(db: Session, source_id: int | None) -> str | None:
    if not source_id:
        return None
    from ..models import SourceRecord
    s = db.get(SourceRecord, source_id)
    return s.code if s else None


def load_person_pools(db: Session, person_code: str) -> list[PoolInput]:
    groups = {g.code: g for g in db.query(MedEquivGroup).all()}

    versions = db.query(MedDoseVersion).filter_by(person_code=person_code).all()
    dispenses = db.query(MedDispense).filter_by(person_code=person_code).all()
    returns = db.query(MedReturn).filter_by(person_code=person_code).all()
    stays = db.query(MedStay).filter_by(person_code=person_code).all()
    stops = db.query(MedStop).filter_by(person_code=person_code).all()

    pool_keys = set()
    for rows in (versions, dispenses, returns, stops):
        for r in rows:
            fc = getattr(r, "form_class", None) or "other"
            pool_keys.add((r.group_code, fc))

    pools = []
    for group_code, form_class in sorted(pool_keys):
        g = groups.get(group_code)
        pi = PoolInput(
            person_code=person_code, group_code=group_code, form_class=form_class,
            group_name=g.name if g else group_code,
            dose_unit=g.dose_unit if g else "片")
        pi.versions = [
            Version(v.code, v.daily_dose, _d(v.start_date), _d(v.end_date),
                    v.rx_code, v.certainty, _source_code(db, v.source_id))
            for v in versions
            if v.group_code == group_code and (v.form_class or "other") == form_class]
        pi.lines = [
            Line(m.code, _source_code(db, m.source_id), m.qty_base,
                 _d(m.dispense_date), m.drug_code, m.settle_code, m.pharmacy_code)
            for m in dispenses
            if m.group_code == group_code and m.form_class == form_class]
        pi.returns = [
            Ret(r.code, _source_code(db, r.source_id), r.qty_base,
                _d(r.return_date), r.dispense_code, r.match_status)
            for r in returns
            if r.group_code == group_code and r.form_class == form_class]
        pi.stays = [
            Stay(s.code, _source_code(db, s.source_id), _d(s.from_date),
                 _d(s.to_date), s.scope_group)
            for s in stays]
        pi.stops = [
            Stop(t.code, _source_code(db, t.source_id), _d(t.stop_date), t.certainty)
            for t in stops
            if t.group_code == group_code
            and (not t.form_class or t.form_class == form_class)]
        pools.append(pi)
    return pools


def equivalence_view(db: Session, person_code: str | None = None) -> dict:
    groups = db.query(MedEquivGroup).order_by(MedEquivGroup.code).all()
    drugs = db.query(MedDrugCatalog).order_by(MedDrugCatalog.code).all()
    if person_code:
        used = {m.drug_code for m in
                db.query(MedDispense).filter_by(person_code=person_code).all()}
    out_groups = []
    for g in groups:
        gd = [d for d in drugs if d.group_code == g.code]
        forms = sorted({d.form_class for d in gd})
        out_groups.append({
            "code": g.code, "name": g.name, "ingredient": g.ingredient,
            "dose_unit": g.dose_unit, "note": g.note,
            "has_multiple_forms": len(forms) > 1,
            "form_classes": forms,
            "drugs": [{
                "code": d.code, "name": d.name, "dosage_form": d.dosage_form,
                "form_class": d.form_class,
                "form_label": FORM_LABELS.get(d.form_class, d.form_class),
                "spec": d.spec, "unit_factor": d.unit_factor,
                "equivalence_kind": d.equivalence_kind,
                "used_by_person": (d.code in used) if person_code else None,
            } for d in gd],
        })
    return {"groups": out_groups, "form_labels": FORM_LABELS}


def analyze_person(db: Session, person_code: str, window_start=None,
                   days: int = WINDOW_DAYS, focus_group: str | None = None,
                   focus_form_class: str | None = None) -> dict:
    person = db.query(Person).filter_by(code=person_code).first()
    if not person:
        raise KeyError(f"人员 {person_code} 不存在")

    # 窗口默认取该人员剧本/发药所在月的 1 号；演示数据由 scenario 显式传入
    if window_start is None:
        first = (db.query(MedDispense).filter_by(person_code=person_code)
                 .order_by(MedDispense.dispense_date).first())
        window_start = _d(first.dispense_date).replace(day=1) if first else date.today()
    ws = _d(window_start)

    pools_in = load_person_pools(db, person_code)
    pools = [analyze_pool(pi, ws, days) for pi in pools_in]

    # 同成分跨剂型“禁止相加”提示 + 朴素错误口径对照
    form_guards = _form_separation_guards(db, person_code, pools)

    # 关注池
    focus_key = None
    if focus_group:
        match = next((p for p in pools
                      if p["summary"]["group_code"] == focus_group
                      and (not focus_form_class
                           or p["summary"]["form_class"] == focus_form_class)), None)
        if match:
            focus_key = match["summary"]["pool_key"]
    if focus_key is None and pools:
        focus_key = pools[0]["summary"]["pool_key"]

    all_anomalies = []
    for p in pools:
        all_anomalies.extend(p["anomalies"])
    all_anomalies.extend(form_guards)
    order = {"true_excess": 0, "undetermined": 1, "info": 2}
    all_anomalies.sort(key=lambda a: (order.get(a.get("kind"), 3),
                                      a.get("start") or "9999"))

    focus = next((p for p in pools if p["summary"]["pool_key"] == focus_key),
                 pools[0] if pools else None)

    return {
        "person": {"code": person.code, "name": person.name,
                   "region": person.region, "pseudo_id": person.pseudo_id},
        "window_start": _iso(ws), "days": days,
        "focus_pool_key": focus_key,
        "focus_summary": focus["summary"] if focus else None,
        "pools": pools,
        "anomalies": all_anomalies,
        "equivalence": equivalence_view(db, person_code),
        "form_guards": form_guards,
        "disclaimer": ("本日历仅重建用药覆盖与核查口径，区分真实超量覆盖与口径未定；"
                       "不写入、不改变任何父任务案件的定性或状态，案件结论须经人工核查。"),
    }


def _form_separation_guards(db: Session, person_code: str, pools: list[dict]) -> list[dict]:
    """同成分不同剂型：明确库存不相加，并给出朴素错误合计对照。"""
    groups = {g.code: g for g in db.query(MedEquivGroup).all()}
    disp = db.query(MedDispense).filter_by(person_code=person_code).all()
    by_group: dict[str, list[MedDispense]] = {}
    for m in disp:
        by_group.setdefault(m.group_code, []).append(m)

    guards = []
    for group_code, rows in by_group.items():
        forms = {m.form_class for m in rows}
        if len(forms) <= 1:
            continue
        g = groups.get(group_code)
        naive = sum(m.qty_base for m in rows)
        per_pool = {}
        for m in rows:
            per_pool[m.form_class] = per_pool.get(m.form_class, 0) + m.qty_base
        guards.append({
            "kind": "info", "status": "explained", "type": "form_separation",
            "person_code": person_code, "group_code": group_code,
            "ingredient": g.ingredient if g else group_code,
            "title": "同成分不同剂型，库存禁止相加",
            "detail": (
                f"同属{g.name if g else group_code}（有效成分 {g.ingredient if g else '?'}）"
                f"但存在 {len(forms)} 个不同剂型库存池："
                + "；".join(f"{FORM_LABELS.get(f, f)} {_num(q)}{g.dose_unit if g else ''}"
                            for f, q in sorted(per_pool.items()))
                + f"。不能仅因同成分就把它们相加：朴素同成分合计={_num(naive)}"
                  f"{g.dose_unit if g else ''} 是错误口径，正确做法是分池核算、"
                  "跨剂型不产生覆盖重叠。"),
            "naive_same_ingredient_qty": _num(naive),
            "pools_qty": {f: _num(q) for f, q in per_pool.items()},
            "source_codes": [], "days": 0,
        })
    return guards
