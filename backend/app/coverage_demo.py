"""患者级用药覆盖日历——虚构核验样例播种（幂等、可在旧库上补播）。

四个聚焦样例（全部虚构）：
- COV-S1 / P15：题目规定演算样例——30 片 1/日起，10 日后改 2/日，
  其间住院 4 日暂停门诊药，第 17 日关联退回 6 片。
- COV-S2 / P16：双源重叠——30 片（03-01）+10 片（03-16），恒定 1/日，
  产生 10 天可归责真实超量覆盖 + 5 天跨处方剩余库存裁定口径未定。
- COV-S3 / P17：停药 + 未匹配退货——1 天真实超量、2 天归属未定，
  并量化“漏记停药会虚增 3 天重叠”的敏感性。
- COV-S4 / P18：等效关系与剂型隔离——同成分同剂型两品牌可合并（21 天重叠），
  同成分口崩片属不同库存池，14 片不得与 60 片相加。
"""
from datetime import date as D
from datetime import datetime as DT

from sqlalchemy.orm import Session

from .models import (
    CoverageRun,
    CoverageScenario,
    MedDispense,
    MedDoseVersion,
    MedDrugCatalog,
    MedEquivGroup,
    MedReturn,
    MedStay,
    MedStop,
    Person,
    SourceRecord,
)

WS = D(2026, 3, 1)


def _dt(d, h=10):
    return DT(d.year, d.month, d.day, h, 0)


def _src(db: Session, code: str, rtype: str, bkey: str, system: str,
         citation: str, vf: D, payload: dict | None = None) -> SourceRecord:
    existing = db.query(SourceRecord).filter_by(code=code).first()
    if existing:
        return existing
    row = SourceRecord(
        code=code, source_system=system, record_type=rtype, business_key=bkey,
        valid_from=_dt(vf, 9), received_at=_dt(vf, 18), status="current",
        citation=citation, payload=payload or {})
    db.add(row)
    db.flush()
    return row


def _person(db, code, name, note):
    p = db.query(Person).filter_by(code=code).first()
    if p:
        return p
    p = Person(code=code, name=name, pseudo_id=code, gender="男",
               birth_year=1952, region="CY", contact_phone=None,
               address="朝阳区覆盖日历样例小区", note=note)
    db.add(p)
    db.flush()
    return p


def seed_coverage_demo(db: Session) -> dict:
    """幂等写入覆盖日历样例；已存在 COV-S1 时直接跳过。不自行 commit。"""
    if db.query(CoverageScenario).filter_by(code="COV-S1").first():
        return {"skipped": True}

    # ------------------------------------------------------------ 人员
    _person(db, "P15", "周明远", "覆盖日历演算样例：剂量上调+住院+退货")
    _person(db, "P16", "吴佩兰", "覆盖日历样例：双源重叠与剩余库存裁定")
    _person(db, "P17", "郑长青", "覆盖日历样例：停药与未匹配退货")
    _person(db, "P18", "冯婉清", "覆盖日历样例：药品等效与剂型隔离")

    # ------------------------------------------------------------ 等效组 / 目录
    eq_src = _src(db, "SRC-COV-EQ", "med_equivalence", "EQ-RULES-2026",
                  "药品目录与等效关系库",
                  "医保药品通用名/剂型对照与等效替换规则表 EQ-2026-03",
                  D(2026, 2, 25),
                  payload={"rule": "同等效组且同 form_class 且换算系数已知方可并池；"
                                   "同成分不同剂型分池"})
    groups = [
        MedEquivGroup(code="EQ-AML", name="氨氯地平口服常释剂量池",
                      ingredient="氨氯地平", dose_unit="片",
                      note="同成分普通片可互换；口崩片/控缓释片另池，不与普通片相加"),
        MedEquivGroup(code="EQ-ATV", name="阿托伐他汀口服常释剂量池",
                      ingredient="阿托伐他汀", dose_unit="片"),
        MedEquivGroup(code="EQ-MET", name="二甲双胍口服常释剂量池",
                      ingredient="二甲双胍", dose_unit="片"),
    ]
    db.add_all(groups)
    db.flush()

    catalog = [
        MedDrugCatalog(code="DRUG-AML-LH", name="络活喜 苯磺酸氨氯地平片",
                       group_code="EQ-AML", ingredient="氨氯地平",
                       dosage_form="片剂（普通片）", form_class="tablet_immediate",
                       spec="5mg*7片/盒", unit_factor=1.0,
                       equivalence_kind="same_form_equiv", source_id=eq_src.id),
        MedDrugCatalog(code="DRUG-AML-AN", name="安内真 苯磺酸氨氯地平片",
                       group_code="EQ-AML", ingredient="氨氯地平",
                       dosage_form="片剂（普通片）", form_class="tablet_immediate",
                       spec="5mg*14片/盒", unit_factor=1.0,
                       equivalence_kind="same_form_equiv", source_id=eq_src.id),
        MedDrugCatalog(code="DRUG-AML-OD", name="苯磺酸氨氯地平口崩片",
                       group_code="EQ-AML", ingredient="氨氯地平",
                       dosage_form="口崩片", form_class="orally_disintegrating",
                       spec="5mg*7片/盒", unit_factor=1.0,
                       equivalence_kind="diff_form_separate", source_id=eq_src.id),
        MedDrugCatalog(code="DRUG-ATV", name="阿托伐他汀钙片",
                       group_code="EQ-ATV", ingredient="阿托伐他汀",
                       dosage_form="片剂（普通片）", form_class="tablet_immediate",
                       spec="20mg*7片/板", unit_factor=1.0, source_id=eq_src.id),
        MedDrugCatalog(code="DRUG-MET", name="盐酸二甲双胍片",
                       group_code="EQ-MET", ingredient="二甲双胍",
                       dosage_form="片剂（普通片）", form_class="tablet_immediate",
                       spec="0.5g*20片/盒", unit_factor=1.0, source_id=eq_src.id),
    ]
    db.add_all(catalog)
    db.flush()

    # ============================================================ S1 / P15
    s = "SRC-COV-P15-RXV1"
    src = _src(db, s, "dose_version", "RX-COV-15A", "电子处方中心",
               "处方 RX-COV-15A：氨氯地平 5mg 1片/日（03-01~03-10）", WS)
    db.add(MedDoseVersion(code="COV15-DV1", person_code="P15", group_code="EQ-AML",
                          form_class="tablet_immediate", rx_code="RX-COV-15A",
                          daily_dose=1, start_date=D(2026, 3, 1),
                          end_date=D(2026, 3, 10), source_id=src.id))
    src2 = _src(db, "SRC-COV-P15-RXV2", "dose_version", "RX-COV-15B",
                "电子处方中心",
                "剂量调整处方 RX-COV-15B：10 日后就诊调整为 2片/日，03-11 起",
                D(2026, 3, 10), payload={"change": "1/day -> 2/day"})
    db.add(MedDoseVersion(code="COV15-DV2", person_code="P15", group_code="EQ-AML",
                          form_class="tablet_immediate", rx_code="RX-COV-15B",
                          daily_dose=2, start_date=D(2026, 3, 11), end_date=None,
                          source_id=src2.id))
    sd = _src(db, "SRC-COV-P15-D1", "dispense", "ST-COV-151", "医保结算/药店发药系统",
              "结算 ST-COV-151 / 发药 COV-D1：氨氯地平普通片 30 片，2026-03-01", WS,
              payload={"qty": 30})
    db.add(MedDispense(code="COV15-D1", person_code="P15", group_code="EQ-AML",
                       form_class="tablet_immediate", drug_code="DRUG-AML-LH",
                       qty=30, unit_factor=1, qty_base=30,
                       dispense_date=WS, settle_code="ST-COV-151",
                       pharmacy_code="PH1", source_id=sd.id))
    sh = _src(db, "SRC-COV-P15-STAY", "inpatient_stay", "ZY-COV-15",
              "H1 住院床位系统",
              "住院记录 ZY-COV-15：2026-03-13~03-16 共 4 日，期间门诊氨氯地平暂停",
              D(2026, 3, 13), payload={"days": 4})
    db.add(MedStay(code="COV15-H1", person_code="P15",
                   from_date=D(2026, 3, 13), to_date=D(2026, 3, 16),
                   scope_group="EQ-AML", org_code="H1", source_id=sh.id))
    sr = _src(db, "SRC-COV-P15-RET", "drug_return", "TH-COV-151",
              "药店退货验收系统",
              "退货验收单 TH-COV-151：2026-03-17 退回氨氯地平 6 片，关联 ST-COV-151",
              D(2026, 3, 17), payload={"qty": 6, "ref": "COV15-D1"})
    db.add(MedReturn(code="COV15-R1", person_code="P15", group_code="EQ-AML",
                     form_class="tablet_immediate", qty_base=6,
                     return_date=D(2026, 3, 17), dispense_code="COV15-D1",
                     match_status="linked", source_id=sr.id))

    # ============================================================ S2 / P16
    sx = _src(db, "SRC-COV-P16-RX", "dose_version", "RX-COV-16",
              "电子处方中心", "阿托伐他汀 20mg 1片/日，全月恒定", WS)
    db.add(MedDoseVersion(code="COV16-DV", person_code="P16", group_code="EQ-ATV",
                          form_class="tablet_immediate", rx_code="RX-COV-16",
                          daily_dose=1, start_date=WS, end_date=None,
                          source_id=sx.id))
    for code, qty, day, stl in [
            ("COV16-DA", 30, 1, "ST-COV-161"),
            ("COV16-DB", 10, 16, "ST-COV-162")]:
        ss = _src(db, f"SRC-COV-P16-{code}", "dispense", stl,
                  "医保结算/药店发药系统",
                  f"结算 {stl}：阿托伐他汀钙片 {qty} 片，2026-03-{day:02d}",
                  D(2026, 3, day))
        db.add(MedDispense(code=code, person_code="P16", group_code="EQ-ATV",
                           form_class="tablet_immediate", drug_code="DRUG-ATV",
                           qty=qty, unit_factor=1, qty_base=qty,
                           dispense_date=D(2026, 3, day), settle_code=stl,
                           pharmacy_code="PH2", source_id=ss.id))

    # ============================================================ S3 / P17
    sm = _src(db, "SRC-COV-P17-RX", "dose_version", "RX-COV-17",
              "电子处方中心", "二甲双胍 0.5g 2片/日", WS)
    db.add(MedDoseVersion(code="COV17-DV", person_code="P17", group_code="EQ-MET",
                          form_class="tablet_immediate", rx_code="RX-COV-17",
                          daily_dose=2, start_date=WS, end_date=None,
                          source_id=sm.id))
    for code, qty, day, stl in [
            ("COV17-DA", 40, 1, "ST-COV-171"),
            ("COV17-DB", 20, 15, "ST-COV-172")]:
        ss = _src(db, f"SRC-COV-P17-{code}", "dispense", stl,
                  "医保结算/药店发药系统",
                  f"结算 {stl}：二甲双胍 {qty} 片，2026-03-{day:02d}",
                  D(2026, 3, day))
        db.add(MedDispense(code=code, person_code="P17", group_code="EQ-MET",
                           form_class="tablet_immediate", drug_code="DRUG-MET",
                           qty=qty, unit_factor=1, qty_base=qty,
                           dispense_date=D(2026, 3, day), settle_code=stl,
                           pharmacy_code="PH4", source_id=ss.id))
    sstop = _src(db, "SRC-COV-P17-STOP", "stop_order", "TZ-COV-17",
                 "H2 医嘱/停药系统",
                 "停药医嘱 TZ-COV-17：自 2026-03-18 起停用二甲双胍（换治疗方案）",
                 D(2026, 3, 17), payload={"stop_date": "2026-03-18"})
    db.add(MedStop(code="COV17-ST", person_code="P17", group_code="EQ-MET",
                   form_class="tablet_immediate", stop_date=D(2026, 3, 18),
                   certainty="certain", note="有正式停药医嘱", source_id=sstop.id))
    sru = _src(db, "SRC-COV-P17-RETU", "drug_return_unmatched", "TH-COV-17X",
               "药店退货验收系统",
               "退货验收单 TH-COV-17X：2026-03-16 退二甲双胍 6 片，"
               "但原结算/发药单号缺失，无法匹配到 COV17-DA 或 COV17-DB",
               D(2026, 3, 16), payload={"qty": 6, "matched": False})
    db.add(MedReturn(code="COV17-RU", person_code="P17", group_code="EQ-MET",
                     form_class="tablet_immediate", qty_base=6,
                     return_date=D(2026, 3, 16), dispense_code=None,
                     match_status="unlinked", source_id=sru.id))

    # ============================================================ S4 / P18
    se = _src(db, "SRC-COV-P18-RX", "dose_version", "RX-COV-18",
              "电子处方中心", "氨氯地平 5mg 1片/日", WS)
    db.add(MedDoseVersion(code="COV18-DV", person_code="P18", group_code="EQ-AML",
                          form_class="tablet_immediate", rx_code="RX-COV-18",
                          daily_dose=1, start_date=WS, end_date=None,
                          source_id=se.id))
    # 口崩片是不同剂型池，需要自己的剂量版本（同样 1 片/日，但库存绝不与普通片相加）
    db.add(MedDoseVersion(code="COV18-DVOD", person_code="P18", group_code="EQ-AML",
                          form_class="orally_disintegrating", rx_code="RX-COV-18-OD",
                          daily_dose=1, start_date=WS, end_date=None,
                          note="口崩片 1 片/日，剂型不同，独立库存池",
                          source_id=se.id))
    s4rows = [
        ("COV18-L1", "DRUG-AML-LH", 30, 1, "ST-COV-181", "PH1", "络活喜普通片"),
        ("COV18-L2", "DRUG-AML-AN", 30, 10, "ST-COV-182", "PH5", "安内真普通片"),
        ("COV18-OD", "DRUG-AML-OD", 14, 12, "ST-COV-183", "PH1", "氨氯地平口崩片"),
    ]
    for code, drug, qty, day, stl, ph, label in s4rows:
        ss = _src(db, f"SRC-COV-P18-{code}", "dispense", stl,
                  "医保结算/药店发药系统",
                  f"结算 {stl}：{label} {qty} 片，2026-03-{day:02d}",
                  D(2026, 3, day))
        fc = "orally_disintegrating" if drug == "DRUG-AML-OD" else "tablet_immediate"
        db.add(MedDispense(code=code, person_code="P18", group_code="EQ-AML",
                           form_class=fc, drug_code=drug,
                           qty=qty, unit_factor=1, qty_base=qty,
                           dispense_date=D(2026, 3, day), settle_code=stl,
                           pharmacy_code=ph, source_id=ss.id))

    # ------------------------------------------------------------ 剧本 + 期望
    scenarios = [
        CoverageScenario(
            code="COV-S1", title="规定演算：剂量上调 + 住院4日 + 退6片",
            person_code="P15", window_start=WS, days=30,
            focus_group="EQ-AML", focus_form_class="tablet_immediate",
            tags=["剂量版本", "住院暂停", "关联退货", "双路线一致"],
            description=("30 片、起 1 片/日；用 10 天后（03-11）剂量调到 2 片/日；"
                         "03-13~03-16 住院 4 日门诊药暂停；03-17 退回关联的 6 片。"
                         "核验：有效覆盖至 03-21，区间法与余额法一致，0 天超量重叠。"),
            expected={"claim_end": "2026-03-21", "active_claim_days": 17,
                      "hospital_days": 4, "excess_days": 0,
                      "balance": {"2026-03-10": 20, "2026-03-12": 16,
                                  "2026-03-17_end": 8, "2026-03-21_end": 0},
                      "consumed_base": 24, "returned_base": 6}),
        CoverageScenario(
            code="COV-S2", title="双源重叠：10天真实超量 + 5天剩余库存口径未定",
            person_code="P16", window_start=WS, days=30,
            focus_group="EQ-ATV", focus_form_class="tablet_immediate",
            tags=["可归责重叠", "真实超量", "剩余库存裁定", "口径未定"],
            description=("03-01 发 30 片、03-16 再发 10 片，恒定 1 片/日。"
                         "按各笔给药期区间相交，03-16~03-25 共 10 天两源同时覆盖，"
                         "来源齐全，为可归责真实超量；余额法另见 03-26~03-30 两批次"
                         "物理共存 5 天，属跨处方剩余库存裁定口径未定，不直接定性。"),
            expected={"confirmed_excess_days": 10,
                      "excess_start": "2026-03-16", "excess_end": "2026-03-25",
                      "coexist_days": 15, "caliber_undetermined_days": 5,
                      "caliber_start": "2026-03-26", "caliber_end": "2026-03-30",
                      "end_balance_0330": 10}),
        CoverageScenario(
            code="COV-S3", title="停药 + 未匹配退货：1天真实超量、2天归属未定",
            person_code="P17", window_start=WS, days=30,
            focus_group="EQ-MET", focus_form_class="tablet_immediate",
            tags=["停药截断", "未匹配退货", "未决来源", "敏感性"],
            description=("40 片(03-01)+20 片(03-15)，2 片/日；03-16 一笔 6 片退货"
                         "无法匹配原发药行；03-18 起有医嘱停药。03-15 为真实超量，"
                         "03-16~17 因未匹配退货归属未定；若漏记停药，03-18~20 会"
                         "虚增 3 天表面重叠。"),
            expected={"confirmed_excess_days": 1,
                      "confirmed_dates": ["2026-03-15"],
                      "undetermined_days": 2,
                      "undetermined_dates": ["2026-03-16", "2026-03-17"],
                      "missed_stop_adds_days": 3,
                      "balance_0317_end": 20}),
        CoverageScenario(
            code="COV-S4", title="等效关系：同剂型可并池，不同剂型不相加",
            person_code="P18", window_start=WS, days=30,
            focus_group="EQ-AML", focus_form_class="tablet_immediate",
            tags=["药品等效", "同剂型合并", "剂型隔离", "禁止同成分相加"],
            description=("络活喜与安内真均为氨氯地平 5mg 普通片，属同一库存池："
                         "30 片(03-01)+30 片(03-10) 产生 21 天真实超量覆盖。"
                         "另有氨氯地平口崩片 14 片(03-12)，剂型不同，单独成池，"
                         "不得把 14 与普通片 60 片相加（朴素 74 为错误口径）。"),
            expected={"pool_count": 2, "tablet_excess_days": 21,
                      "tablet_qty": 60, "orally_disintegrating_qty": 14,
                      "od_covered_days": 14,
                      "naive_same_ingredient_qty": 74}),
    ]
    db.add_all(scenarios)
    db.flush()

    # ------------------------------------------------------------ 留档一次复算
    from .services.coverage import analyze_person
    for sc in scenarios:
        result = analyze_person(
            db, sc.person_code, window_start=sc.window_start, days=sc.days,
            focus_group=sc.focus_group, focus_form_class=sc.focus_form_class)
        db.add(CoverageRun(
            run_code=f"RUN-{sc.code}-SEED", scenario_code=sc.code,
            person_code=sc.person_code, window_start=sc.window_start,
            days=sc.days, author="system-seed",
            params={"focus_group": sc.focus_group,
                    "focus_form_class": sc.focus_form_class},
            result=result))
    db.flush()
    return {"seeded": [s.code for s in scenarios]}


def ensure_coverage_demo() -> None:
    """供应用启动调用：独立会话、幂等补播并提交。"""
    from .database import SessionLocal
    db = SessionLocal()
    try:
        if db.query(CoverageScenario).count() == 0:
            seed_coverage_demo(db)
            db.commit()
    finally:
        db.close()
