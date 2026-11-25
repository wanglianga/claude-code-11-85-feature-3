<template>
  <div class="page" v-loading="loading">
    <h2 class="page-title">患者级用药覆盖日历</h2>
    <p class="page-sub">
      结算行“给药期重叠”不必然是重复取药：本页从<b>处方剂量版本、实际发药量、住院区间、停药/退货、
      药品等效关系</b>推导患者每日可用量与重叠覆盖天数。提供<b>区间切段法</b>与<b>库存余额滚动法</b>
      两条可互相核验的路线；异常视图区分<b style="color:#b91c1c">真实超量覆盖</b>与
      <b style="color:#b45309">口径未定</b>。本页为独立核查层，<b>不写入父任务案件定性</b>。
    </p>

    <div class="disclaimer-banner">
      ⚖️ 覆盖日历只重建用药事实与口径，结论仍是“待人工核查的线索”，不是欺诈/重复取药定性；
      所有样例均为虚构数据。
    </div>

    <!-- 剧本选择 -->
    <div class="card">
      <div class="flex-between" style="flex-wrap:wrap;gap:12px">
        <el-radio-group v-model="scenarioCode" type="button" @change="loadAnalyze">
          <el-radio-button v-for="s in scenarios" :key="s.code" :value="s.code">
            {{ s.code }} · {{ s.person_name }}
          </el-radio-button>
        </el-radio-group>
        <div class="flex gap8">
          <el-button size="small" :loading="verifying" @click="runVerify">
            🧪 运行 7 项核验
          </el-button>
          <el-button size="small" type="primary" plain :loading="saving" @click="saveRun">
            💾 复算并留档（本剧本已留档 {{ runCount }} 次）
          </el-button>
        </div>
      </div>
      <div v-if="currentScenario" class="mt12">
        <div class="flex gap8" style="align-items:flex-start">
          <el-tag size="small" type="info" effect="plain">{{ currentScenario.title }}</el-tag>
          <el-tag v-for="t in currentScenario.tags" :key="t" size="small">{{ t }}</el-tag>
        </div>
        <p class="small muted" style="line-height:1.8;margin:8px 0 0">
          {{ currentScenario.description }}
        </p>
      </div>

      <!-- 内置核验结果 -->
      <div v-if="verify" class="verify-box mt12">
        <div class="flex-between">
          <b>内置核验：
            <span :style="{color: verify.overall ? '#15803d' : '#dc2626'}">
              {{ verify.overall ? '全部通过' : '存在失败' }} {{ verify.passed }}/{{ verify.total }}
            </span>
          </b>
          <el-button link size="small" @click="showVerify = !showVerify">
            {{ showVerify ? '收起明细' : '展开明细' }}
          </el-button>
        </div>
        <div v-show="showVerify" class="mt8">
          <div v-for="c in verify.checks" :key="c.key" class="verify-line">
            <span :class="c.passed ? 'vk pass' : 'vk fail'">{{ c.passed ? '✓' : '✗' }}</span>
            <div style="flex:1">
              <b class="small">{{ c.name }}</b>
              <div class="small muted">{{ c.expected }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <template v-if="data && focusPool">
      <!-- 汇总卡片 -->
      <div class="grid stat-grid cov-stats">
        <div class="stat excess">
          <div class="n">{{ sum.excess_days }}</div><div class="l">真实超量覆盖天数</div>
        </div>
        <div class="stat undetermined">
          <div class="n">{{ sum.undetermined_days }}</div><div class="l">口径未定天数</div>
        </div>
        <div class="stat">
          <div class="n">{{ sum.covered_days }}</div><div class="l">正常覆盖天数</div>
        </div>
        <div class="stat hospital">
          <div class="n">{{ sum.hospital_days }}</div><div class="l">住院暂停天数</div>
        </div>
        <div class="stat stopped">
          <div class="n">{{ sum.stopped_days }}</div><div class="l">停药天数</div>
        </div>
        <div class="stat gap">
          <div class="n">{{ sum.gap_days }}</div><div class="l">覆盖缺口天数</div>
        </div>
      </div>

      <div class="card">
        <div class="flex-between" style="flex-wrap:wrap;gap:10px">
          <div class="flex gap8" style="align-items:center;flex-wrap:wrap">
            <h3 style="margin:0">{{ data.person.code }} {{ data.person.name }} · 30 日窗 {{ data.window_start }} 起</h3>
            <el-tag size="small">{{ sum.group_code }}</el-tag>
            <el-tag size="small" type="info" effect="plain">
              {{ sum.form_label }}（库存池，单位：{{ sum.dose_unit }}）
            </el-tag>
          </div>
          <el-radio-group v-if="data.pools.length > 1" :model-value="poolKey"
                           size="small" @update:model-value="selectPool">
            <el-radio-button v-for="p in data.pools" :key="p.summary.pool_key"
                             :value="p.summary.pool_key">
              {{ p.summary.form_label }}
            </el-radio-button>
          </el-radio-group>
        </div>
        <el-alert class="mt12" :type="sum.dual_route_match ? 'success' : 'warning'"
                  :closable="false" show-icon>
          <template #title>
            双路线核验：区间法真实超量 <b>{{ sum.excess_days }}</b> 天 ＝
            余额法佐证 <b>{{ sum.balance_excess_corroborated_days }}</b> 天，
            <b>{{ sum.dual_route_match ? '两路线一致 ✓' : '不一致，需人工核对' }}</b>；
            余额恒等式（发−退＝消耗＋期末）{{ sum.balance_identity_holds ? '成立 ✓' : '不成立 ✗' }}。
            余额法另见多批次物理共存 {{ sum.coexist_days_balance }} 天，
            其中 {{ sum.caliber_leftover_days }} 天属剩余库存裁定口径未定。
          </template>
        </el-alert>
      </div>

      <el-tabs v-model="tab" class="cov-tabs">
        <!-- ================= 覆盖日历 ================= -->
        <el-tab-pane label="📅 覆盖日历" name="calendar">
          <div class="card">
            <div class="legend">
              <span class="item" v-for="(m, k) in STATUS_META" :key="k">
                <i class="sw" :style="{background:m.bg,borderColor:m.bd}"></i>{{ m.label }}
              </span>
            </div>
            <div class="cal-week">
              <span v-for="w in ['一','二','三','四','五','六','日']" :key="w">{{ w }}</span>
            </div>
            <div class="cal-grid">
              <span v-for="n in leadingBlanks" :key="'b'+n" class="cal-blank"></span>
              <button v-for="d in cal" :key="d.date" class="cal-cell"
                      :class="[d.status, {sel: selectedDate === d.date}]"
                      @click="selectedDate = d.date">
                <span class="cal-dn">{{ d.day_no }}</span>
                <span class="cal-md">{{ d.date.slice(5) }}</span>
                <span class="cal-bal">余 {{ fmt(d.end_balance) }}</span>
                <span v-if="d.k_sources >= 2" class="cal-k">×{{ d.k_sources }}源</span>
              </button>
            </div>

            <div v-if="selectedDay" class="day-detail mt16">
              <div class="flex-between">
                <b>{{ selectedDay.date }}（周{{ selectedDay.weekday }}）· 第 {{ selectedDay.day_no }} 天</b>
                <el-tag size="small" :type="STATUS_TAG[selectedDay.status]">
                  {{ STATUS_META[selectedDay.status]?.label || selectedDay.status }}
                </el-tag>
              </div>
              <div class="kv mt8">
                <span class="k">规定日剂量</span><span>{{ fmt(selectedDay.need) }} {{ sum.dose_unit }}/日（基础剂量 {{ fmt(selectedDay.base_dose) }}）</span>
                <span class="k">日初/可用</span><span>{{ fmt(selectedDay.available) }}（含当日发入 {{ fmt(selectedDay.inflow) }}、退回 {{ fmt(selectedDay.returned) }}）</span>
                <span class="k">当日消耗</span><span>{{ fmt(selectedDay.consumed) }}，期末余额 <b>{{ fmt(selectedDay.end_balance) }}</b></span>
                <span class="k">在库来源数</span><span>{{ selectedDay.k_sources }}：{{ (selectedDay.on_hand_lines||[]).join('、') || '—' }}</span>
                <span class="k">区间覆盖行</span><span>{{ (selectedDay.covering_lines||[]).join('、') || '—' }}</span>
                <span class="k" v-if="selectedDay.hospital">住院暂停</span>
                <span v-if="selectedDay.hospital">{{ selectedDay.hospital }}：住院期间门诊药不消耗库存</span>
                <span class="k" v-if="selectedDay.stopped">停药</span>
                <span v-if="selectedDay.stopped">停药单 {{ selectedDay.stopped }} 生效中</span>
                <span class="k" v-if="selectedDay.reasons?.length">口径标记</span>
                <span v-if="selectedDay.reasons?.length">
                  <el-tag v-for="r in selectedDay.reasons" :key="r" size="small" type="warning"
                          style="margin-right:6px">{{ REASON_TEXT[r] || r }}</el-tag>
                </span>
              </div>
              <div v-if="selectedDay.fifo_consumption?.length" class="small muted mt8">
                FIFO 消耗归因：
                <span v-for="f in selectedDay.fifo_consumption" :key="f.line" class="chain-uid"
                      style="margin-right:6px">{{ f.line }} 出 {{ fmt(f.units) }}</span>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- ================= 双路线 ================= -->
        <el-tab-pane label="🔀 双路线核验" name="routes">
          <el-row :gutter="14">
            <el-col :span="11">
              <div class="card route-a">
                <h3>路线 A · 有效日期区间 + 剂量切段</h3>
                <p class="small muted" style="line-height:1.7">
                  每笔发药按剂量版本切成不同日剂量的覆盖段，住院日留隙、停药截断、关联退货核减。
                  跨处方旧方剩余按“同池库存、新剂量重估”裁定。
                </p>
                <div v-for="ln in focusPool.route_a_intervals" :key="ln.code" class="line-box">
                  <div class="flex-between">
                    <b class="small">{{ ln.code }}</b>
                    <span>
                      <source-chip :code="ln.source_code" />
                    </span>
                  </div>
                  <div class="small mt8">
                    发 {{ fmt(ln.qty_base) }} · 关联退 {{ fmt(ln.linked_return_qty) }} ·
                    有效 {{ fmt(ln.effective_qty) }} {{ sum.dose_unit }}
                  </div>
                  <div class="small mt8">
                    有效区间 <b>{{ ln.claim_start || '—' }} ~ {{ ln.claim_end || '未耗尽' }}</b>
                    ；窗内覆盖 <b>{{ ln.active_claim_days_in_window }}</b> 天
                    <span v-if="ln.pause_days_within_span">；跨度内含住院留隙 {{ ln.pause_days_within_span }} 天</span>
                  </div>
                  <div class="seg-wrap mt8">
                    <el-tag v-for="(g, i) in ln.segments" :key="i" size="small"
                            type="primary" effect="plain" class="seg-tag">
                      {{ g.start.slice(5) }}~{{ g.end.slice(5) }} · {{ fmt(g.daily_dose) }}/日 · {{ g.days }}天
                    </el-tag>
                  </div>
                  <div class="small muted mt8">
                    FIFO 耗尽日 {{ ln.depletion_date || '窗口延伸期内未耗尽' }}，
                    延伸期末剩余 {{ fmt(ln.lot_remaining_extended) }}
                    <template v-if="ln.remaining_unallocated > 0">
                      ；停药/超 horizon 未分配 {{ fmt(ln.remaining_unallocated) }}
                    </template>
                  </div>
                </div>
              </div>
            </el-col>
            <el-col :span="13">
              <div class="card route-b">
                <h3>路线 B · 患者—药品库存余额逐日滚动</h3>
                <p class="small muted" style="line-height:1.7">
                  发药加入、退货核减、非住院/非停药日按规定日剂量 FIFO 消耗。
                  能反映真实剩余量，但对<b>漏记停药、漏记/错配退货敏感</b>（见下方敏感性）。
                </p>
                <el-table :data="focusPool.route_b_balance" size="small" height="430"
                          :row-class-name="balRowClass" border>
                  <el-table-column prop="day_no" label="日" width="42" align="center" />
                  <el-table-column prop="date" label="日期" width="92">
                    <template #default="{row}">{{ row.date.slice(5) }}</template>
                  </el-table-column>
                  <el-table-column prop="opening" label="日初" width="56" align="right">
                    <template #default="{row}">{{ fmt(row.opening) }}</template>
                  </el-table-column>
                  <el-table-column prop="inflow" label="发入" width="56" align="right">
                    <template #default="{row}">
                      <span :class="{pos:row.inflow>0}">{{ row.inflow ? '+'+fmt(row.inflow) : '·' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="returned" label="退" width="50" align="right">
                    <template #default="{row}">
                      <span :class="{neg:row.returned>0}">{{ row.returned ? '−'+fmt(row.returned) : '·' }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="need" label="需" width="48" align="right" />
                  <el-table-column prop="consumed" label="耗" width="48" align="right" />
                  <el-table-column prop="end_balance" label="期末" width="60" align="right">
                    <template #default="{row}"><b>{{ fmt(row.end_balance) }}</b></template>
                  </el-table-column>
                  <el-table-column label="来源" width="50" align="center">
                    <template #default="{row}">
                      <el-tag size="small" :type="row.k_sources>=2?'danger':'info'" effect="plain">
                        {{ row.k_sources }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="标记" min-width="120">
                    <template #default="{row}">
                      <el-tag v-if="row.hospital" size="small" type="primary">住院</el-tag>
                      <el-tag v-if="row.stopped" size="small" type="info">停药</el-tag>
                      <el-tag v-if="row.stockout" size="small" type="danger">缺口</el-tag>
                      <el-tag v-for="n in row.ret_notes" :key="n" size="small" type="warning">
                        {{ n === 'return_exceeds_balance' ? '退货超余额' : '关联退货超批次' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <div class="card sensitivity">
                <h3>敏感性 · 漏记停药 / 退货的影响</h3>
                <div class="small" style="line-height:2">
                  <div>当前口径真实超量 <b>{{ focusPool.sensitivity?.recorded_excess_days }}</b> 天、
                    区间相交合计 <b>{{ focusPool.sensitivity?.recorded_overlap_total_days }}</b> 天。</div>
                  <div>若<b>漏记停药</b>：相交合计变为
                    {{ focusPool.sensitivity?.if_stop_omitted_overlap_total_days }} 天，
                    <b class="warn-text">将虚增 {{ focusPool.sensitivity?.missing_stop_would_add_days }} 天表面重叠</b>。</div>
                  <div>若<b>漏记退货</b>：真实超量变为
                    {{ focusPool.sensitivity?.if_return_omitted_excess_days }} 天，
                    各笔区间末端变为 {{ (focusPool.sensitivity?.if_return_omitted_claim_ends||[]).join('、') || '—' }}。</div>
                  <div class="muted">{{ focusPool.sensitivity?.note }}</div>
                </div>
              </div>
            </el-col>
          </el-row>
        </el-tab-pane>

        <!-- ================= 来源贡献账 ================= -->
        <el-tab-pane label="📒 来源贡献账" name="ledger">
          <div class="card">
            <h3>每笔发药的天数贡献（可回溯到来源账 code）</h3>
            <el-table :data="focusPool.route_a_intervals" size="small" border class="mt8">
              <el-table-column prop="code" label="发药行" width="110" />
              <el-table-column label="来源账" width="150">
                <template #default="{row}"><source-chip :code="row.source_code" /></template>
              </el-table-column>
              <el-table-column prop="dispense_date" label="发药日" width="104" />
              <el-table-column label="发/退/有效" width="120" align="right">
                <template #default="{row}">
                  {{ fmt(row.qty_base) }} / −{{ fmt(row.linked_return_qty) }} / <b>{{ fmt(row.effective_qty) }}</b>
                </template>
              </el-table-column>
              <el-table-column label="有效区间" width="190">
                <template #default="{row}">{{ row.claim_start }} ~ {{ row.claim_end || '未耗尽' }}</template>
              </el-table-column>
              <el-table-column label="切段（日剂量×天数）" min-width="240">
                <template #default="{row}">
                  <el-tag v-for="(g,i) in row.segments" :key="i" size="small" effect="plain"
                          style="margin:2px">{{ fmt(g.daily_dose) }}/日×{{ g.days }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="窗内贡献天数" width="110" align="center">
                <template #default="{row}"><b>{{ row.active_claim_days_in_window }}</b></template>
              </el-table-column>
              <el-table-column label="归因超量/未定" width="120" align="center">
                <template #default="{row}">
                  <span class="excess-text">{{ lineOverlap(row.code).excess }}</span>
                  /
                  <span class="warn-text">{{ lineOverlap(row.code).und }}</span>
                </template>
              </el-table-column>
            </el-table>
            <p class="small muted mt8">
              “窗内贡献天数”为该笔发药经剂量/住院/停药/退货调整后，实际分配到处方日剂量的 30 日窗内天数；
              “归因超量/未定”来自配对相交段。每一笔均带来源账 code，可在「来源账」页检索原始凭证。
            </p>
          </div>
        </el-tab-pane>

        <!-- ================= 异常视图 ================= -->
        <el-tab-pane :label="`🚨 异常视图（${excessAnoms.length} 超量 / ${undAnoms.length} 未定）`" name="anomaly">
          <el-row :gutter="14">
            <el-col :span="12">
              <div class="card anom-col excess-col">
                <h3 class="excess-text">● 真实超量覆盖（来源齐全、双路线佐证）</h3>
                <el-empty v-if="!excessAnoms.length" description="本剧本无真实超量覆盖日" :image-size="70" />
                <div v-for="(a,i) in excessAnoms" :key="'e'+i" class="anom-item excess-item">
                  <div class="flex-between">
                    <b>{{ a.days }} 天 · {{ a.start }} ~ {{ a.end }}</b>
                    <el-tag size="small" type="danger">真实超量</el-tag>
                  </div>
                  <div class="small mt8">{{ a.title }}：{{ (a.line_codes||[]).join(' × ') }}</div>
                  <p class="small" style="line-height:1.7;margin:6px 0">{{ a.detail }}</p>
                  <div><source-chip v-for="s in a.source_codes" :key="s" :code="s" /></div>
                </div>
              </div>
            </el-col>
            <el-col :span="12">
              <div class="card anom-col und-col">
                <h3 class="warn-text">● 口径未定（不确认为超量，列未决来源）</h3>
                <el-empty v-if="!undAnoms.length" description="本剧本无口径未定项" :image-size="70" />
                <div v-for="(a,i) in undAnoms" :key="'u'+i" class="anom-item und-item">
                  <div class="flex-between">
                    <b>{{ a.days }} 天<span v-if="a.start"> · {{ a.start }} ~ {{ a.end }}</span></b>
                    <el-tag size="small" type="warning">口径未定</el-tag>
                  </div>
                  <div class="small mt8">{{ a.title }}<template v-if="a.line_codes?.length">：{{ a.line_codes.join(' × ') }}</template></div>
                  <p class="small" style="line-height:1.7;margin:6px 0">{{ a.detail }}</p>
                  <div>
                    <el-tag v-for="r in a.reasons" :key="r" size="small" type="warning" effect="plain"
                            style="margin-right:6px">{{ REASON_TEXT[r] || r }}</el-tag>
                    <source-chip v-for="s in a.source_codes" :key="s" :code="s" />
                  </div>
                </div>
              </div>
            </el-col>
          </el-row>
          <div class="card">
            <h3>ℹ️ 剂型 / 等效口径提示（解释项，非异常）</h3>
            <el-empty v-if="!infoAnoms.length" description="本患者无跨剂型池" :image-size="60" />
            <div v-for="(a,i) in infoAnoms" :key="'i'+i" class="anom-item info-item">
              <b>{{ a.title }}</b>
              <p class="small" style="line-height:1.8;margin:6px 0">{{ a.detail }}</p>
            </div>
            <el-alert type="info" :closable="false" class="mt12"
                      title="以上分类仅用于核查优先级与口径说明，不会写回 C-001~C-008 等任何父任务案件；案件定性必须经人工核查与法定程序。" />
          </div>
        </el-tab-pane>

        <!-- ================= 等效关系 ================= -->
        <el-tab-pane label="🧬 等效与剂型" name="equiv">
          <div class="card">
            <h3>药品等效关系与库存池规则</h3>
            <p class="small muted">
              只有“同等效组 + 同剂型类别 + 换算系数已知”的药品才合并库存；同成分但不同剂型分池，
              <b>不得仅按 ingredient 相加</b>。
            </p>
            <div v-for="g in data.equivalence.groups" :key="g.code" class="eq-group">
              <div class="flex-between">
                <b>{{ g.code }} · {{ g.name }}</b>
                <el-tag size="small" :type="g.has_multiple_forms ? 'warning' : 'success'" effect="plain">
                  {{ g.has_multiple_forms ? `含 ${g.form_classes.length} 个剂型池，需隔离` : '单一剂型池' }}
                </el-tag>
              </div>
              <div class="small muted">有效成分：{{ g.ingredient }}；计量单位：{{ g.dose_unit }}</div>
              <el-table :data="g.drugs" size="small" border class="mt8">
                <el-table-column prop="code" label="药品编码" width="150" />
                <el-table-column prop="name" label="名称" min-width="200" />
                <el-table-column prop="dosage_form" label="剂型文案" width="150" />
                <el-table-column label="剂型池" width="120">
                  <template #default="{row}">
                    <el-tag size="small" :type="row.form_class==='tablet_immediate'?'':'warning'">
                      {{ row.form_label }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="spec" label="规格" width="140" />
                <el-table-column label="换算系数" width="90" align="center">
                  <template #default="{row}">{{ row.unit_factor }}</template>
                </el-table-column>
                <el-table-column label="本患者使用" width="90" align="center">
                  <template #default="{row}">
                    <el-tag v-if="row.used_by_person" size="small" type="primary">使用</el-tag>
                    <span v-else class="muted">—</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-empty v-else-if="!loading" description="暂无覆盖日历数据" />
  </div>
</template>

<script setup>
import { computed, h, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { RouterLink } from 'vue-router'
import api from '../api.js'

// 内联来源账 chip（router-link 到来源账检索）
const SourceChip = {
  props: { code: String },
  setup(props) {
    return () => props.code
      ? h(RouterLink, { to: `/sources?q=${encodeURIComponent(props.code)}`,
                        class: 'chain-uid src-chip' }, () => props.code)
      : h('span', { class: 'muted small' }, '无来源')
  }
}

const scenarios = ref([])
const scenarioCode = ref('COV-S1')
const currentScenario = computed(() => scenarios.value.find((s) => s.code === scenarioCode.value))
const data = ref(null)
const loading = ref(false)
const tab = ref('calendar')
const poolKey = ref('')
const selectedDate = ref(null)
const verify = ref(null)
const verifying = ref(false)
const showVerify = ref(false)
const saving = ref(false)
const runCount = ref(0)

const STATUS_META = {
  excess: { label: '真实超量', bg: '#fee2e2', bd: '#fca5a5' },
  undetermined: { label: '口径未定', bg: '#fef3c7', bd: '#fcd34d' },
  covered: { label: '正常覆盖', bg: '#dcfce7', bd: '#86efac' },
  hospital: { label: '住院暂停', bg: '#dbeafe', bd: '#93c5fd' },
  stopped: { label: '停药', bg: '#e2e8f0', bd: '#cbd5e1' },
  gap: { label: '覆盖缺口', bg: '#ffedd5', bd: '#fdba74' },
  empty: { label: '无剂量/空', bg: '#f8fafc', bd: '#e2e8f0' }
}
const STATUS_TAG = {
  excess: 'danger', undetermined: 'warning', covered: 'success',
  hospital: 'primary', stopped: 'info', gap: 'warning', empty: 'info'
}
const REASON_TEXT = {
  unlinked_return: '退货未匹配原发药行',
  dose_version_estimated: '剂量版本为估算',
  caliber_leftover: '剩余库存裁定口径分歧',
  cross_rx_leftover_policy: '跨处方剩余库存裁定分歧'
}

const focusPool = computed(() => {
  if (!data.value?.pools?.length) return null
  return data.value.pools.find((p) => p.summary.pool_key === poolKey.value)
    || data.value.pools[0]
})
const sum = computed(() => focusPool.value?.summary || {})
const cal = computed(() => focusPool.value?.calendar || [])
const selectedDay = computed(() => cal.value.find((d) => d.date === selectedDate.value) || null)
const leadingBlanks = computed(() => {
  if (!cal.value.length) return 0
  const [y, m, dd] = cal.value[0].date.split('-').map(Number)
  return (new Date(Date.UTC(y, m - 1, dd)).getUTCDay() + 6) % 7
})
const excessAnoms = computed(() => (data.value?.anomalies || []).filter((a) => a.kind === 'true_excess'))
const undAnoms = computed(() => (data.value?.anomalies || []).filter((a) => a.kind === 'undetermined'))
const infoAnoms = computed(() => (data.value?.anomalies || []).filter((a) => a.kind === 'info'))

function fmt(v) {
  if (v === null || v === undefined || v === '') return '0'
  return typeof v === 'number' ? (Number.isInteger(v) ? v : Number(v).toFixed(2)) : v
}
function selectPool(k) {
  poolKey.value = k
  selectedDate.value = focusPool.value?.calendar?.[0]?.date || null
}
function lineOverlap(code) {
  let excess = 0; let und = 0
  for (const a of focusPool.value?.attributions || []) {
    if (a.line_a === code || a.line_b === code) {
      if (a.status === 'excess') excess += a.days
      else und += a.days
    }
  }
  return { excess, und }
}
function balRowClass({ row }) {
  if (row.hospital) return 'row-hospital'
  if (row.stopped) return 'row-stopped'
  if (row.stockout) return 'row-gap'
  if (row.k_sources >= 2 && row.available > row.need) return 'row-overlap'
  return ''
}

async function loadScenarios() {
  scenarios.value = (await api.get('/api/coverage/scenarios')).items
}
async function loadRuns() {
  try {
    const r = await api.get('/api/coverage/runs', { params: { scenario_code: scenarioCode.value } })
    runCount.value = r.items.length
  } catch (e) { /* 非关键 */ }
}
async function loadAnalyze() {
  loading.value = true
  try {
    data.value = await api.get('/api/coverage/analyze', { params: { scenario: scenarioCode.value } })
    poolKey.value = data.value.focus_pool_key
    selectedDate.value = data.value.window_start
    await loadRuns()
  } finally {
    loading.value = false
  }
}
async function runVerify() {
  verifying.value = true
  showVerify.value = true
  try {
    verify.value = await api.get('/api/coverage/verify')
  } finally {
    verifying.value = false
  }
}
async function saveRun() {
  saving.value = true
  try {
    const r = await api.post('/api/coverage/runs', { scenario_code: scenarioCode.value })
    ElMessage.success(`已留档复算：${r.run_code}`)
    await loadRuns()
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await loadScenarios()
  await loadAnalyze()
  runVerify()
})
</script>

<style scoped>
.cov-stats .stat .n { font-size: 24px; }
.stat.excess .n { color: #dc2626; }
.stat.undetermined .n { color: #b45309; }
.stat.hospital .n { color: #1d4ed8; }
.stat.stopped .n { color: #64748b; }
.stat.gap .n { color: #c2410c; }
.cov-tabs :deep(.el-tabs__content) { overflow: visible; }

.verify-box { border: 1px solid var(--rg-line); border-radius: 10px; padding: 10px 14px; background: #f8fafc; }
.verify-line { display: flex; gap: 10px; align-items: flex-start; padding: 6px 0; border-bottom: 1px dashed #e2e8f0; }
.verify-line:last-child { border-bottom: none; }
.vk { width: 20px; height: 20px; border-radius: 50%; color: #fff; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; flex: none; }
.vk.pass { background: #16a34a; }
.vk.fail { background: #dc2626; }

.cal-week, .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
.cal-week span { text-align: center; font-size: 12px; color: var(--rg-muted); padding: 4px 0; }
.cal-blank { aspect-ratio: 1.15 / 1; }
.cal-cell { border: 1px solid var(--rg-line); border-radius: 9px; background: #fff; padding: 6px 7px;
  display: flex; flex-direction: column; align-items: flex-start; gap: 1px; cursor: pointer;
  aspect-ratio: 1.15 / 1; min-height: 74px; text-align: left; font-family: inherit; font-size: 12px;
  transition: box-shadow .12s, transform .05s; }
.cal-cell:hover { box-shadow: 0 2px 10px rgba(29,78,216,.15); border-color: #93c5fd; }
.cal-cell.sel { outline: 2px solid var(--rg-blue); outline-offset: 1px; }
.cal-dn { font-weight: 800; font-size: 14px; }
.cal-md { font-size: 10.5px; color: var(--rg-muted); }
.cal-bal { font-size: 11.5px; margin-top: auto; font-variant-numeric: tabular-nums; }
.cal-k { font-size: 10px; background: #fee2e2; color: #b91c1c; border-radius: 4px; padding: 0 5px; }
.cal-cell.excess { background: #fee2e2; border-color: #fca5a5; }
.cal-cell.undetermined { background: #fef3c7; border-color: #fcd34d; }
.cal-cell.covered { background: #f0fdf4; border-color: #bbf7d0; }
.cal-cell.hospital { background: #dbeafe; border-color: #93c5fd; }
.cal-cell.stopped { background: #f1f5f9; border-color: #cbd5e1; color: #64748b; }
.cal-cell.gap { background: #ffedd5; border-color: #fdba74; }
.cal-cell.empty { background: #fafafa; }
.day-detail { border: 1px solid var(--rg-line); border-radius: 10px; padding: 12px 14px; background: #fbfdff; }

.line-box { border: 1px solid var(--rg-line); border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; background: #fcfdff; }
.seg-tag { margin: 2px 4px 2px 0; }
.src-chip { display: inline-block; margin: 2px 4px 2px 0; text-decoration: none; }
.pos { color: #15803d; font-weight: 700; }
.neg { color: #dc2626; font-weight: 700; }
.excess-text { color: #b91c1c; font-weight: 700; }
.warn-text { color: #b45309; font-weight: 700; }
:deep(.el-table .row-hospital) { background: #eff6ff !important; }
:deep(.el-table .row-stopped) { background: #f8fafc !important; color: #94a3b8; }
:deep(.el-table .row-gap) { background: #fff7ed !important; }
:deep(.el-table .row-overlap) { background: #fef2f2 !important; }

.anom-col { min-height: 200px; }
.anom-item { border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; border: 1px solid var(--rg-line); background: #fff; }
.excess-item { border-left: 4px solid #dc2626; background: #fefcfc; }
.und-item { border-left: 4px solid #d97706; background: #fffdf5; }
.info-item { border-left: 4px solid #0ea5e9; background: #f7fdff; }
.eq-group { border: 1px solid var(--rg-line); border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
@media (max-width: 1200px) { .cal-cell { min-height: 64px; } }
</style>
