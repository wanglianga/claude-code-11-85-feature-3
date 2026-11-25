# 医保风险线索关系分析平台（RiskGraph）

面向医保稽核部门的**跨机构风险线索关系分析**演示系统：从就诊、处方、结算、药店库存/供药、
医师排班、转诊、养老床位与申诉材料中构建**可回溯、受限扩展、可反证、可复演**的关系图，
帮助稽核人员发现值得人工核验的跨机构异常。

> ⚖️ **合规红线**：规则分与图模型分都只是“风险结构 + 核查优先级”，**不是欺诈或违法结论**。
> 家庭共用联系方式、养老机构代办、慢病协作不会被直接解释为欺诈；弱身份证据不自动合并个人。
> 所有案件定性必须经人工核查与法定程序。

技术栈：**Vue 3 + Vite + Element Plus（SVG 自研图谱）｜ FastAPI + SQLAlchemy ｜ PostgreSQL**。
一键部署：**Docker Compose（PostgreSQL 16 + FastAPI 同源托管前端 dist）**。

---

## 1. 功能与题目要求对照

| 题目要求 | 实现位置 |
| --- | --- |
| 来源账：来源系统 / 业务有效时间 / 接收时间 / 撤销更正 / 可引用字段 | 表 `source_record`；页面 **来源账**；每条业务记录均挂 `source_id`，支持更正链 `supersedes` |
| 身份解析分级：伪标识、有效期内专业凭证=强；共享电话/地址/代办人=弱候选，不自动合并 | `services/identity.py`；页面 **身份解析**；弱证据仅 `pending`，可人工确认/驳回并产生簇新版本+快照 |
| 边保存具体依据、来源与支持/反驳方向 | 表 `graph_edge`：`basis / source_codes / stance(support/refute/neutral) / status(active/negated)` |
| 图快照：冻结截止日、身份簇版本、边范围、政策版本 | 表 `graph_snapshot`；页面 **快照复演**（冻结图谱、四要素、两快照 Diff） |
| 模式求值：多点重复购药 / 拆方 / 集中结算 / 异常转诊的必要事实 | `services/patterns.py`；页面 **模式与排序**、案件详情“必要事实” |
| 反证编排：进销存、配送签收、停诊、床位服务、政策、申诉结果 | `services/counterevidence.py`；页面 **反证联查** 与案件“反证”页签 |
| **患者级用药覆盖日历（双路线）**：剂量版本 / 实际发药量 / 住院区间 / 停药退货 / 等效与剂型 → 每日可用量、重叠覆盖天数；来源账保留每笔天数贡献；异常区分**真实超量**与**口径未定**；**不写父任务案件定性** | `services/coverage.py`、`coverage_demo.py`、`coverage_verify.py`、`routers/coverage.py`；页面 **用药覆盖日历**（路由 `/coverage`）；核验 `GET /api/coverage/verify`（7 项） |
| 案件投影：最小子图、金额范围、证据出处、人工状态；分数非结论 | `services/projection.py`；页面 **案件投影 / 案件详情**；按统筹区/机构做接口级权限 |
| 扩展范围=起始索赔/受益人+允许边；同伪标识/强簇、处方/结算/供药、两跳、180 天 | `services/expansion.py` 默认参数；页面 **受限图扩展** |
| 弱身份候选只生成待核边，不能继续扩展全网 | BFS 中弱边对端标记 `terminal_candidates`，不入队；验收 `p23_weak_no_expand` |
| 高金额跨机构扩展须模式配置增加边与截止条件并重新冻结快照 | `expansion.high_amount_extension` + `POST /api/graph/high-amount-extension`；C-001 被门槛拒绝、C-007 授权并可冻结 |
| 规则组合筛选风险结构 → 金额 + 反证完整度排序 | `services/ranking.py`，排序选项 `amount_completeness / amount_unresolved / rule` |
| 带时间切分验证的图模型：地区/机构偏差校准、高分可追溯路径与反证 | `services/graphmodel.py`（纯 Python 逻辑回归，train≤2026-01-31 / val=2026-02；验证集指标真实计算；地区+机构类型残差校准） |
| 规则与模型在 P1 上展示**相同支持链** | 模型结果直接回传规则引擎的 `support_chain` 为 `trace_chain_uids`；验收 `same_chain_and_rank` |
| 变更（身份裁定/政策有效日/退费更正）→ 新快照 + 标出受影响案件；旧材料保留原判断来源 | `routers/identity.py`、`routers/snapshots.py`；`case_judgment` 不可变版本，旧版本永久指向旧快照 |
| 虚构网络四类验收原型 | 见下节，**总览页“一键验收”** 或 `GET /api/acceptance/run`（10 项断言） |

### 虚构验收网络（全部为虚构数据）

- **P1 / C-001（多点重复购药，成立支持链）**：伪标识 `P1`（含药店建档镜像 `P1B`，强簇）
  在 **康宁（朝阳店）PH1** 与 **百草堂（东城店）PH2** 同日（2026-03-10）取得同成分 **氨氯地平**，
  数量 30/28、给药期重叠 28 天，两笔结算互相落在同一给药期间；开方医师陈明 `D1` 当日排班 **停诊 SCH1**
  且无多点执业备案。规则与图模型给出完全相同的核心支持链，在“多点重复购药”同目标开放线索中均排第一。
- **P2/P3 / C-002（弱身份，不并人）**：共用养老机构电话 `010-88886666` 与代办社工（两条弱证据），
  但身份簇始终分离；各自处方、本人签收 `DL1/DL2`、养老床位 `BED2/BED3` 均匹配。弱边不可继续扩展。
- **P4 / C-003（合法供药）**：表面同日重复购药由药店间调拨 `TR4`（PH4→PH3，批号一致）、
  进销存出入库闭环与串户结算全额红冲更正 `SRC-ST5B-C2` 解释；重叠边置 `negated`，不再进入扩展。
- **P5 / C-004（反证关闭）**：申诉 `APP1` 成立，退货验收单 `TH-0318` 与 `return_in` 台账印证药品退回，
  结算全额退款，案件 `closed_returned`。
- 另有：**C-005** P9 同诊三张处方（拆方）、**C-006** PH5 同终端 60 分钟四人集中结算、
  **C-007** P14 90 日三次同向转诊 + ¥88,200 结算（高金额跨机构扩展）、
  **C-008** P6 慢病协作月度续方（给药期不重叠 + 长处方备案，不命中并排除）。

---

## 2. 一键 Docker 启动（推荐验收方式）

```bash
cd /workspace/85-feature-3
docker compose up -d --build
# 等待 app 健康（首次会自动建表并播种虚构网络，含用药覆盖日历样例）
docker compose ps
```

- 平台：<http://localhost:8000>
- Swagger / OpenAPI：<http://localhost:8000/docs>
- 数据库端口映射到宿主机 **5433**（账号/库/密码均为 `ccdev`），避免与本机 PostgreSQL 冲突。
- 停止：`docker compose down`；清空数据卷重来：`docker compose down -v`。

> 说明：本交付容器内未安装 docker，因此镜像构建未在开发容器中实际执行；
> Dockerfile 采用与已验证本地流程完全一致的 Node 22 构建 + Python 3.12 运行步骤。
> 下列“本地开发与验证”均为在本容器预装运行时上**实际执行并通过**的结果。

---

## 3. 本地开发与验证（已实际执行）

### 3.1 数据库（容器内预装 PostgreSQL 18）

```bash
# 已验证 ccdev 可连；创建独立库（只需一次）
PGPASSWORD=ccdev /opt/postgresql-18/bin/psql -h 127.0.0.1 -U ccdev -d ccdev \
  -c "CREATE DATABASE riskgraph;"
```

### 3.2 后端（Python 3.12 + venv）

```bash
cd /workspace/85-feature-3/backend
/opt/python-3.12/bin/python3 -m venv /workspace/85-feature-3/.venv
/workspace/85-feature-3/.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
  -r requirements.txt

# 初始化/重置播种（也可跳过：API 启动时空库会自动播种）
PYTHONPATH=/workspace/85-feature-3/backend \
  /workspace/85-feature-3/.venv/bin/python -m app.seed

# 启动（本地开发端口与 vite proxy 对齐为 8000）
# config 默认 DATABASE_URL 已指向本机 riskgraph，STATIC_DIR 默认指向 ../frontend/dist
DATABASE_URL='postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/riskgraph' \
  /workspace/85-feature-3/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 健康检查：<http://127.0.0.1:8000/api/health>
- Swagger：<http://127.0.0.1:8000/docs>

### 3.3 前端（Node 22）

```bash
cd /workspace/85-feature-3/frontend
/opt/node-22/bin/npm install --registry=https://registry.npmmirror.com

# 方式 A：开发服务器（/api 代理到 127.0.0.1:8000，可用 API_TARGET 覆盖）
/opt/node-22/bin/npm run dev        # http://localhost:5173

# 方式 B：构建后由 FastAPI 同源托管（上面的 STATIC_DIR 指向 dist）
/opt/node-22/bin/npm run build
```

### 3.4 测试账号

| 账号 | 密码 | 权限 |
| --- | --- | --- |
| `admin` | `admin123` | 管理员，全部统筹区/机构；可重置数据、重训模型、触发变更 |
| `auditor` | `audit123` | 朝阳片区稽核员（`region_scope=["CY"]`），仅可见 C-001/C-004/C-006，访问 C-002 详情返回 403，可验证投影权限收敛 |

---

## 4. 本题验证方式与已执行结论

### 4.1 一键验收（10 项断言，全部通过）

- 界面：**总览 / 验收剧本 → 运行验收**（或登录后 `GET /api/acceptance/run`）。
- 命令行实测：

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s http://127.0.0.1:8000/api/acceptance/run -H "Authorization: Bearer $TOKEN"
```

开发过程中的真实执行结果（服务层与 HTTP 层均跑过）：

```
OVERALL True 10 / 10
PASS source_ledger            # 103 条来源、0 缺引用、SRC-ST5B 更正链
PASS identity_grading         # P1/P1B 同强簇；P2/P3 异簇，IE-03 pending
PASS p1_chain                 # 同日/重叠28/同一给药期/停诊 + 7 条核心边
PASS same_chain_and_rank      # trace_chain_uids == support_chain；规则/模型同目标均 C-001 第一
PASS p23_weak_no_expand       # P3 为 terminal，ST4/RX4 不可达
PASS p4_transfer              # TR4 checked_explains，overlap negated 且扩展不可见
PASS p5_appeal_return         # APP1 upheld/returned，C-004 closed_returned
PASS restricted_expansion     # 2 跳 / 180 天，SCH1/PH1/PH2 可达，无越窗边
PASS high_amount_gate         # C-001 被拒、C-007 授权扩展并可达 bed 节点
PASS snapshot_immutable       # 快照#1 四要素齐全，判断 v1@snap1 带免责声明
```

### 4.2 患者级用药覆盖日历：双路线样例核验（本题新增，7/7 通过）

界面：左侧导航 **用药覆盖日历**（或总览页“用药覆盖日历”按钮），顶部可切换 `COV-S1~S4` 四个虚构样例，
点 **🧪 运行 7 项核验** 应显示“全部通过 7/7”。命令行 / HTTP 实测：

```bash
# 命令行（仓库根目录，退出码 0 即全部通过）
/workspace/85-feature-3/.venv/bin/python backend/verify_coverage.py

# 或登录后 HTTP
curl -s http://127.0.0.1:8000/api/coverage/verify \
  -H "Authorization: Bearer $TOKEN"
```

真实执行结果：

```
COVERAGE VERIFY ALL PASS 7/7
[PASS] cov_sample_interval_balance   # 规定样例区间+余额精确值
[PASS] cov_two_routes_agree          # 两路线“真实超量天数”一致且余额佐证
[PASS] cov_attributable_overlap      # 10天可归责超量 + 5天剩余库存口径未定
[PASS] cov_unresolved_sources        # 1天真实超量 + 2天未匹配退货未定 + 漏记停药敏感性
[PASS] cov_dosage_form_separation    # 同剂型并池21天；口崩片另池14天，60≠74相加
[PASS] cov_source_day_contributions  # 每笔来源天数贡献 + CoverageRun 留档
[PASS] cov_no_parent_case_write      # 8 个案件状态不变，不写父任务定性
```

**规定演算样例 COV-S1 / P15（30 片 1/日 → 10 日后 2/日，住院 4 日，退 6 片）**，30 日窗 `2026-03-01~03-30`：

| 日期节点 | 库存余额（期末） | 说明 |
| --- | --- | --- |
| 03-01 ~ 03-10 | 29 → **20** | 1 片/日 ×10，共耗 10 |
| 03-11 ~ 03-12 | 18 → **16** | 剂量调到 2 片/日，2 天耗 4 |
| 03-13 ~ 03-16 | **16**（不变） | 住院 4 日，门诊药暂停、不消耗 |
| 03-17 | 退 6 后 10，耗 2 → **8** | 关联退货单 TH-COV-151 核减 |
| 03-18 ~ 03-21 | 6 / 4 / 2 / **0** | 2 片/日，03-21 耗尽 |
| 03-22 ~ 03-30 | **0** | 9 天覆盖缺口 |

恒等式：发 30 ＝ 消耗 24 ＋ 退回 6；路线 A 有效覆盖区间 **2026-03-01 ~ 03-21**
（切段 `03-01~10 ·1/日·10天`、`03-11~12 ·2/日·2天`、住院留隙 `03-13~16`、
`03-17~21 ·2/日·5天`），有效覆盖 **17 天**、住院留隙 **4 天**；路线 B 逐日余额与上表完全一致，
两路线真实超量天数均为 **0**。若漏记该笔退货，区间末端会错误延到 **03-24**（敏感性面板可见）。

- **COV-S2/P16 可归责重叠 + 剩余库存裁定**：03-01 发 30、03-16 发 10，恒定 1/日。
  路线 A 两笔给药期区间在 **03-16~03-25 相交 10 天**，来源齐全、路线 B 佐证当日 `在库来源≥2 且可用>需`，
  定为 **10 天真实超量覆盖**并归因到该来源对；路线 B 另见两批次物理共存到 **03-30（共 15 天）**，
  其中 **03-26~03-30 这 5 天**是否算重叠取决于“跨处方旧方剩余是否按新剂量重估/FIFO”，
  列为 **口径未定（cross_rx_leftover_policy）**，不直接定性；03-30 期末余额恰为 10。
- **COV-S3/P17 停药 + 未匹配退货（未决来源）**：40(03-01)+20(03-15)、2/日；03-16 一笔 6 片退货
  **匹配不到原发药行**；03-18 有医嘱停药。**03-15 为 1 天真实超量**，**03-16~17 共 2 天**因退货归属未定
  列为口径未定；若**漏记停药**，03-18~20 会虚增 **3 天**表面重叠（敏感性量化）。
- **COV-S4/P18 等效与剂型隔离**：络活喜与安内真均为氨氯地平 5mg **普通片**（同 `form_class`、系数 1），
  合并库存池，30(03-01)+30(03-10) → **21 天真实超量**；另有氨氯地平**口崩片** 14 片（03-12），
  属不同剂型池、独立日剂量，覆盖 **14 天**、**0 超量**。系统明确拦截“同成分朴素合计 74 片”的错误口径，
  正确为普通片池 60、口崩片池 14，**跨剂型不产生重叠**。

前端已用 Playwright + 无头 Chromium 真实登录并巡检：默认 `COV-S1` 的全部 5 个页签、
切换 `COV-S2`/`COV-S4` 及 S4 的“口崩片”池，核对 30 个日历格、30 行余额表、核验 7/7 与统计数字，
**浏览器 console / pageerror 均为 0**（`COV-S3` 由 7 项断言与 HTTP 接口核验）。
容器内无中文字体，截图里汉字显示为方框，但 DOM 文本、数值与布局断言正常。

### 4.3 已实际执行的其他验证

- `python -m app.seed` 成功：`19 persons, 121 source records, 119 nodes, 267 edges, snapshot #1`
  （新增 P15~P18 覆盖日历样例与 18 条 `SRC-COV-*` 来源；覆盖数据在独立新表，不进入图边与案件定性）。
- 遍历全部 GET API 与 **8 个案件详情**均 200，投影子图 5~11 节点 / 6~18 边，每案 6 条反证核查。
- 权限：`auditor` 仅列出 `['C-001','C-004','C-006']`，访问 `GET /api/cases/C-002` 返回 **403**。
- 变更流（实测）：驳回 IE-03 → 快照 #2/身份簇 v2/影响 C-002；
  政策换版 → 快照 #3/影响 C-008 并追加判断 v2；
  ST12 退费更正 → 快照 #4/影响 C-005 并追加判断 v2（v1 保留）；
  案件人工状态写入核查时间线。
- `npm run build` **构建成功**（Vue SFC 全部通过编译，产物由 FastAPI `/` 托管，实测 200）。
- 使用 Playwright + 无头 Chromium（CDP 方式）真实打开并逐页巡检了登录、总览验收、来源账、身份解析、
  受限图扩展、模式排序、反证联查、案件列表/详情、快照复演共 10 个界面：登录跳转、一键验收全绿、
  图谱力导向渲染/边配色/支持链均正常，**浏览器 console 0 错误**，并逐页截图做了人工目检
  （过程中发现并修复了“边坐标取错字段导致连线不显示”的真实渲染 bug）。
- 重置接口曾出现“请求会话持锁 + drop_all”自死锁，已通过重建前先 `rollback()/close()` 修复并复测秒回。
- 容器内**没有 docker**，因此未执行 `docker compose build`；Dockerfile/Compose 仅做了 YAML 解析与逻辑核对，
  镜像构建步骤与已验证的本地 Node/Python 流程一致。建议在装有 Docker 的机器上执行第 2 节命令做最终确认。

### 4.4 建议的人工 UI 走查（约 6 分钟）

0. **用药覆盖日历（本题新增，约 2 分钟）**：左侧进入 → 默认 `COV-S1`，看 30 格日历配色与统计卡
   （真实超量 0 / 口径未定 0 / 正常覆盖 17 / 住院 4 / 缺口 9），点 03-13~16 住院格、03-21 耗尽格看详情；
   “双路线核验”页对照切段与 30 行余额表及敏感性；“来源贡献账”点 `SRC-COV-P15-*` 可跳来源账；
   “异常视图”看 S2 的 10 天红色真实超量 + 5 天琥珀口径未定、S3 的未匹配退货、S4 的剂型隔离蓝条；
   切到 S4 再切“口崩片”库存池。点“复算并留档”会写入独立的 `coverage_run`，不影响任何案件。
1. `admin/admin123` 登录 → 总览 → **运行验收**，应全绿。
2. **身份解析**：看到 IE-01/IE-02 强证据自动成簇、IE-03~06 弱候选；可对 IE-03 驳回，提示新快照与 C-002。
3. **受限图扩展**：起点 `P1` 扩展，点红/橙边查看 `basis` 与来源编号；切到 `P2` 可见黄色虚线圈 P3 且无法延伸；
   高金额面板对 C-001 拒绝、C-007 授权并可冻结。
4. **模式与排序**：MULTI_POINT 下规则/模型两种排序 C-001 均第一；进 C-001 详情核对事实、
   完全相同的支持链 UID、模型特征贡献与校准因子。
5. **反证联查**：切换 C-003/C-004 看绿色“合理解释/确认反证”，C-001 看红色“仍未排除”。
6. **案件投影**：提交核查状态；切换判断版本；用 `auditor/audit123` 重新登录验证只能看朝阳案件。
7. **快照复演**：查看 #1 冻结图与政策/身份版本；执行政策换版或 ST12 退费，查看新快照、Diff 与受影响案件，
   再回 C-005/C-008 对比 v1 与新判断。

---

## 5. 目录结构

```
/workspace/85
├── Dockerfile                 # 多阶段：node22 构建 → python3.12 托管
├── docker-compose.yml        # db(postgres:16) + app，健康依赖
├── README.md
├── backend/
│   ├── requirements.txt
│   ├── verify_coverage.py     # 用药覆盖日历 7 项断言的命令行入口
│   └── app/
│       ├── main.py            # FastAPI、启动播种（含覆盖日历幂等补播）、静态托管
│       ├── config.py database.py models.py security.py acceptance.py seed.py
│       ├── coverage_demo.py coverage_verify.py
│       ├── routers/           # auth/sources/identity/graph/patterns/cases/snapshots/tools/coverage
│       └── services/          # identity/graph_builder/patterns/counterevidence/
│                              # graphmodel/expansion/projection/snapshot/ranking/rejudge/coverage.py
└── frontend/
    ├── vite.config.js package.json
    └── src/
        ├── App.vue AppLayout.vue main.js router.js api.js store.js styles.css
        ├── components/GraphCanvas.vue   # 自研 SVG 力导向图（支持链高亮/弱边终端/否定边）
        └── pages/             # Login/Dashboard/Sources/Identity/Explorer/Patterns/
                               # Counter/Coverage/Cases/CaseDetail/Snapshots
```

## 6. 主要 API 一览

```
POST /api/auth/login
GET  /api/meta/stats
GET  /api/acceptance/run
POST /api/meta/reset                         (admin)
GET  /api/sources?source_system=&record_type=&status=&q=
GET  /api/sources/{code}
GET  /api/identity/evidence | /clusters
POST /api/identity/evidence/{code}/adjudicate
GET  /api/graph/expand?start_type=beneficiary|claim&start_code=P1&max_hops=2&window_days=180
POST /api/graph/high-amount-extension
GET  /api/patterns/configs | /ranking?pattern_code=&sort=
POST /api/patterns/evaluate
GET  /api/patterns/model/latest ; POST /api/patterns/model/train
GET  /api/cases ; GET /api/cases/{code} ; GET /api/cases/{code}/judgments/{v}
POST /api/cases/{code}/review
GET  /api/counter...（反证数据内含在 /api/cases/{code}）
GET  /api/snapshots ; /{no} ; /diff?from_no=&to_no=
POST /api/snapshots/policy-change | /refund-correction
# 患者级用药覆盖日历（本题新增，均需登录；auditor 仅可见 CY 患者，样例均为 CY）
GET  /api/coverage/scenarios                 # COV-S1~S4 剧本
GET  /api/coverage/equivalence?person_code=   # 等效组/剂型池规则
GET  /api/coverage/analyze?scenario=COV-S1   # 双路线完整结果（日历/区间/余额/来源贡献/异常/敏感性）
GET  /api/coverage/verify                    # 7 项内置断言
GET  /api/coverage/runs ; /runs/{run_code}   # 复算留档列表/详情
POST /api/coverage/runs                      # 复算并写入独立 coverage_run（不碰案件）
```

## 7. 边界与后续可加强

- 图模型是**可审计教学实现**（纯 Python 逻辑回归 + 确定性演练样本），用于演示时间切分、校准与路径追溯，
  不是生产级反欺诈模型；新型协作模式召回较低是规则法的已知特性。
- 认证为库内不透明令牌的轻量实现，生产应对接统一身份/审计网关并补充 HTTPS、CSRF 与更细字段级权限。
- 数据全为虚构；未使用 Redis（题目环境虽预装），无搜索/JSON 模块依赖。
