<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const domains = ref([])
const domainId = ref(1)
const jobs = ref([])
const packs = ref([])
const active = ref(null)
const pickVersion = ref('')
const report = ref(null)
const err = ref('')
const busy = ref(false)

async function refresh() {
  jobs.value = await getJson(`/api/jobs?domain_id=${domainId.value}`)
  packs.value = await getJson('/api/rule-packs')
  active.value = packs.value.find((p) => p.enabled) || null
}

onMounted(async () => {
  try {
    domains.value = await getJson('/api/domains')
    if (domains.value.length) domainId.value = domains.value[0].id
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const canSwitch = computed(
  () => pickVersion.value && (!active.value || pickVersion.value !== active.value.version),
)

async function run(step) {
  busy.value = true
  err.value = ''
  report.value = null
  try {
    const url = step === '明细'
      ? '/api/jobs/paste-to-detail'
      : '/api/jobs/detail-to-summary'
    const job = await postJson(url, { domain_id: domainId.value })
    await refresh()
    await showReport(job.id)
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function switchVersion() {
  busy.value = true
  err.value = ''
  try {
    await postJson('/api/rule-packs/activate', { version: pickVersion.value })
    pickVersion.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function showReport(jobId) {
  report.value = await getJson(`/api/jobs/${jobId}/report`)
}
</script>
<template>
  <div>
    <h2>分层任务</h2>
    <p class="lead">
      按主题域推进：先贴源到明细，再明细到汇总。贴源到明细按当前启用规则包裁决，
      通过行带本次版本号写入明细，未通过行进入拒收清单。
    </p>
    <div class="toolbar">
      <label>
        主题域
        <select v-model.number="domainId" @change="refresh">
          <option v-for="d in domains" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
      <button :disabled="busy || !active" @click="run('明细')">跑贴源到明细</button>
      <button class="secondary" :disabled="busy || !active" @click="run('汇总')">跑明细到汇总</button>
    </div>

    <div class="panel">
      <h3>本次任务规则版本</h3>
      <p v-if="active">
        当前启用：<strong>{{ active.version }}</strong> · {{ active.name }}
        <span class="muted">（{{ active.rule_count }} 条规则）</span>
      </p>
      <p v-else class="warn">尚未启用任何规则包，请先到「清洗规则包」启用一个版本。</p>
      <div class="toolbar">
        <label>
          切换启用版本
          <select v-model="pickVersion">
            <option value="">选择版本</option>
            <option v-for="p in packs" :key="p.id" :value="p.version">
              {{ p.version }} · {{ p.name }}{{ p.enabled ? '（当前）' : '' }}
            </option>
          </select>
        </label>
        <button class="secondary" :disabled="busy || !canSwitch" @click="switchVersion">
          保存启用切换
        </button>
        <router-link class="btn" to="/rule-packs">登记 / 管理规则包</router-link>
      </div>
      <p class="muted hint">切换只影响之后的新任务；已有明细行保留其产出时的版本号。</p>
    </div>

    <p v-if="err" class="err">{{ err }}</p>

    <div v-if="report" class="panel report">
      <h3>任务报告 #{{ report.任务.id }}</h3>
      <table>
        <tbody>
          <tr><th>步骤</th><td>{{ report.任务.step_label }}</td></tr>
          <tr><th>任务状态</th><td :class="report.任务.superseded ? 'warn' : 'ok'">{{ report.任务.status_label }}</td></tr>
          <tr><th>本次使用版本</th><td><strong>{{ report.规则版本 || '—' }}</strong></td></tr>
          <tr><th>当前启用版本</th><td>{{ report.当前启用版本 || '—' }}</td></tr>
          <tr><th>贴源行数（输入）</th><td>{{ report.贴源行数 }}</td></tr>
          <tr><th>写入</th><td>{{ report.任务.out_count }}</td></tr>
          <tr><th>拒收</th><td>{{ report.任务.drop_count }}</td></tr>
          <tr v-if="report.明细行数 !== undefined"><th>明细实际行数</th><td>{{ report.明细行数 }}</td></tr>
          <tr v-if="report.拒收行数 !== undefined"><th>拒收清单行数</th><td>{{ report.拒收行数 }}</td></tr>
          <tr v-if="report.汇总行数合计 !== undefined"><th>汇总行数合计</th><td>{{ report.汇总行数合计 }}</td></tr>
          <tr>
            <th>一致性</th>
            <td :class="report.一致 === true ? 'ok' : report.一致 === false ? 'warn' : 'muted'">
              {{ report.一致 === true ? '一致' : report.一致 === false ? '存在差异' : '已替代，不参与核对' }}
            </td>
          </tr>
          <template v-if="report.裁决证明">
            <tr><th colspan="2" class="proof-head">规则裁决证明（与明细、拒收同源落库）</th></tr>
            <tr><th>证明·启用版本</th><td><strong>{{ report.裁决证明.启用版本 }}</strong></td></tr>
            <tr>
              <th>证明·输入 / 写入 / 拒收</th>
              <td>{{ report.裁决证明.输入行数 }} / {{ report.裁决证明.写入行数 }} / {{ report.裁决证明.拒收行数 }}</td>
            </tr>
            <tr><th>证明·写入样本键</th><td>{{ report.裁决证明.写入样本键.join('、') || '—' }}</td></tr>
            <tr><th>证明·拒收样本键</th><td>{{ report.裁决证明.拒收样本键.join('、') || '—' }}</td></tr>
            <tr><th>证明·登记时间</th><td>{{ report.裁决证明.登记时间 }}</td></tr>
          </template>
          <tr v-if="report.说明"><th>说明</th><td>{{ report.说明 }}</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>任务记录</h3>
      <table>
        <thead>
          <tr>
            <th>编号</th><th>步骤</th><th>状态</th><th>规则版本</th><th>说明</th>
            <th>输入</th><th>写入</th><th>拒收</th><th>时间</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="j in jobs" :key="j.id">
            <td>{{ j.id }}</td>
            <td>{{ j.step_label || j.step }}</td>
            <td :class="j.superseded ? 'warn' : ''">{{ j.status_label || j.status }}</td>
            <td><span class="version-chip">{{ j.rule_pack_version || '—' }}</span></td>
            <td>{{ j.message }}</td>
            <td>{{ j.in_count }}</td>
            <td>{{ j.out_count }}</td>
            <td>{{ j.drop_count }}</td>
            <td>{{ j.created_at }}</td>
            <td><button class="secondary" @click="showReport(j.id)">报告</button></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!jobs.length" class="muted">还没有任务记录。</p>
    </div>
  </div>
</template>
<style scoped>
.version-chip {
  display: inline-block;
  padding: 2px 8px;
  border: 1px solid var(--line);
  background: #e8eef5;
  font-size: 12px;
  letter-spacing: 0.04em;
}
.hint { margin: 0; font-size: 13px; }
.report { background: rgba(232, 238, 245, 0.5); padding: 16px; border: 1px solid var(--line); }
.proof-head { color: var(--accent); border-top: 2px solid var(--line); padding-top: 10px; }
.btn { display: inline-block; padding: 8px 16px; background: transparent; color: var(--accent); border: 1px solid var(--accent); }
</style>
