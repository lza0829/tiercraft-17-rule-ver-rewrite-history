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
  rows.value = await getJson(`/api/layers/detail?${qs.join('&')}`)
}

async function refresh() {
  versions.value = await getJson(`/api/layers/versions?layer=detail&domain_id=${domainId.value}`)
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
    <h2>明细层</h2>
    <p class="lead">
      通过启用规则包裁决的贴源行进入本层，并带回本次启用的版本号；未通过的行进入拒收清单。
      切换启用版本不会改写已有明细行的版本号。
    </p>
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
            <th>编号</th><th>主题域</th><th>贴源引用</th><th>业务日</th><th>门店编码</th>
            <th>门店名称</th><th>品类</th><th>金额</th><th>件数</th>
            <th>清洗标记</th><th>规则版本</th><th>任务</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.domain_id }}</td>
            <td>{{ r.paste_ref || '—' }}</td>
            <td>{{ r.biz_day }}</td>
            <td>{{ r.store_code }}</td>
            <td>{{ r.store_name }}</td>
            <td>{{ r.category }}</td>
            <td>{{ r.amount }}</td>
            <td>{{ r.qty }}</td>
            <td>{{ r.clean_flag }}</td>
            <td><span class="version-chip">{{ r.rule_pack_version }}</span></td>
            <td>{{ r.job_id ? `任务 #${r.job_id}` : '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!rows.length" class="muted">该版本下暂无明细，请先在「分层任务」执行贴源到明细。</p>
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
