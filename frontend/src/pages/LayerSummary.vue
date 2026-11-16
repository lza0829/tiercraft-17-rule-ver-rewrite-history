<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const domains = ref([])
const domainId = ref(1)
const versions = ref([])
const ruleVersion = ref('')
const rows = ref([])
const err = ref('')

async function loadRows() {
  const qs = [`domain_id=${domainId.value}`]
  if (ruleVersion.value) qs.push(`rule_version=${encodeURIComponent(ruleVersion.value)}`)
  rows.value = await getJson(`/api/layers/summary?${qs.join('&')}`)
}

async function refresh() {
  versions.value = await getJson(`/api/layers/versions?layer=summary&domain_id=${domainId.value}`)
  if (!versions.value.some((v) => v.rule_version === ruleVersion.value)) {
    const preferred = versions.value.find((v) => v.active) || versions.value[0]
    ruleVersion.value = preferred ? preferred.rule_version : ''
  }
  await loadRows()
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
</script>
<template>
  <div>
    <h2>汇总层</h2>
    <p class="lead">按业务日与门店编码聚合后的汇总表，按明细所属规则版本分别汇总。</p>
    <div class="toolbar">
      <label>
        主题域
        <select v-model.number="domainId" @change="refresh">
          <option v-for="d in domains" :key="d.id" :value="d.id">{{ d.name }}</option>
        </select>
      </label>
      <label>
        规则版本
        <select v-model="ruleVersion" @change="loadRows">
          <option value="">全部版本</option>
          <option v-for="v in versions" :key="v.rule_version" :value="v.rule_version">
            {{ v.rule_version }}{{ v.active ? '（启用中）' : '' }}（{{ v.row_count }} 行）
          </option>
        </select>
      </label>
    </div>
    <p v-if="err" class="err">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>编号</th><th>主题域</th><th>业务日</th><th>门店编码</th>
            <th>金额合计</th><th>件数合计</th><th>明细行数</th><th>规则版本</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.domain_id }}</td>
            <td>{{ r.biz_day }}</td>
            <td>{{ r.store_code }}</td>
            <td>{{ r.amount_sum }}</td>
            <td>{{ r.qty_sum }}</td>
            <td>{{ r.line_count }}</td>
            <td><span class="version-chip">{{ r.rule_pack_version }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="muted">该版本下汇总层为空，请先跑明细到汇总。</p>
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
</style>
