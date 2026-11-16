<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const packs = ref([])
const err = ref('')
const msg = ref('')
const busy = ref(false)

const form = ref({
  version: '',
  name: '',
  description: '',
  rules: [
    { rule_code: 'R001', rule_type: 'required', fields: 'store_name', min: '', allowed: '' },
    { rule_code: 'R002', rule_type: 'amount_min', fields: 'amount', min: 0, allowed: '' },
    { rule_code: 'R003', rule_type: 'category_whitelist', fields: 'category', min: '', allowed: '饮料,零食' },
  ],
})

const ruleTypeOptions = [
  { value: 'required', label: '必填空值' },
  { value: 'amount_min', label: '金额下限' },
  { value: 'category_whitelist', label: '品类白名单' },
]

const active = computed(() => packs.value.find((p) => p.enabled) || null)

const fieldHint = {
  required: '多个字段用逗号分隔，例如 store_name,store_code',
  amount_min: '填写允许的最小金额，例如 0',
  category_whitelist: '品类用逗号分隔，例如 饮料,零食',
}

async function refresh() {
  packs.value = await getJson('/api/rule-packs')
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function addRule() {
  form.value.rules.push({
    rule_code: `R${String(form.value.rules.length + 1).padStart(3, '0')}`,
    rule_type: 'required',
    fields: '',
    min: '',
    allowed: '',
  })
}

function removeRule(index) {
  form.value.rules.splice(index, 1)
}

function toPayloadRule(rule) {
  const base = { rule_code: rule.rule_code, rule_type: rule.rule_type }
  if (rule.rule_type === 'required') {
    base.field = (rule.fields || '').split(/[,，]/)[0].trim()
    base.config = {
      fields: (rule.fields || '')
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    }
  } else if (rule.rule_type === 'amount_min') {
    base.field = 'amount'
    base.config = { min: Number(rule.min) }
  } else {
    base.field = 'category'
    base.config = {
      allowed: (rule.allowed || '')
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean),
    }
  }
  return base
}

async function createPack() {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const pack = await postJson('/api/rule-packs', {
      version: form.value.version.trim(),
      name: form.value.name.trim() || undefined,
      description: form.value.description.trim() || undefined,
      rules: form.value.rules.map(toPayloadRule),
    })
    msg.value = `已登记版本「${pack.version}」，需启用后才会用于新任务。`
    form.value.version = ''
    form.value.name = ''
    form.value.description = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function activate(pack) {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    await postJson(`/api/rule-packs/${pack.id}/activate`, {})
    msg.value = `已启用版本「${pack.version}」。新任务将按该版本裁决，历史明细版本号不变。`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <div>
    <h2>清洗规则包</h2>
    <p class="lead">
      规则包按版本登记，同一时刻仅一个启用版本。贴源到明细任务按启用版本裁决；
      未通过的行进入拒收清单。切换启用版本不会改写任何历史明细行的版本号。
    </p>
    <p v-if="err" class="err">{{ err }}</p>
    <p v-if="msg" class="ok">{{ msg }}</p>

    <div class="panel">
      <h3>当前启用版本</h3>
      <p v-if="active" class="active-pack">
        <strong>{{ active.version }}</strong> · {{ active.name }}
        <span class="muted">（{{ active.rule_count }} 条规则，启用时间 {{ active.activated_at || '—' }}）</span>
      </p>
      <p v-else class="warn">尚未启用任何版本，分层任务将无法运行。</p>
    </div>

    <div class="panel">
      <h3>已登记版本</h3>
      <table>
        <thead>
          <tr>
            <th>编号</th><th>版本</th><th>名称</th><th>规则</th><th>状态</th>
            <th>登记时间</th><th>启用时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in packs" :key="p.id">
            <td>{{ p.id }}</td>
            <td>{{ p.version }}</td>
            <td>{{ p.name }}</td>
            <td>
              <div v-for="r in p.rules" :key="r.id" class="rule-line">
                <span class="rule-code">{{ r.rule_code }}</span>
                <span class="rule-type">{{ r.rule_type_label }}</span>
                <span class="muted">{{ r.description }}</span>
              </div>
            </td>
            <td :class="p.enabled ? 'ok' : 'muted'">{{ p.enabled ? '启用中' : '未启用' }}</td>
            <td>{{ p.created_at }}</td>
            <td>{{ p.activated_at || '—' }}</td>
            <td>
              <button v-if="!p.enabled" class="secondary" :disabled="busy" @click="activate(p)">
                启用
              </button>
              <span v-else class="ok">当前</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!packs.length" class="muted">暂无规则包。</p>
    </div>

    <div class="panel">
      <h3>登记新版本</h3>
      <div class="form-grid">
        <label>版本号<input v-model="form.version" placeholder="例如 v2-严格口径" /></label>
        <label>名称<input v-model="form.name" placeholder="例如 严格口径" /></label>
        <label>说明<input v-model="form.description" placeholder="可选" /></label>
      </div>
      <p class="muted hint">
        规则包须同时包含必填空值、金额下限、品类白名单三类规则，登记后不可修改，只能新登记版本。
      </p>
      <table>
        <thead>
          <tr><th>规则编号</th><th>类型</th><th>作用字段</th><th>配置</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="(rule, i) in form.rules" :key="i">
            <td><input v-model="rule.rule_code" class="narrow" /></td>
            <td>
              <select v-model="rule.rule_type">
                <option v-for="o in ruleTypeOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </td>
            <td>
              <input
                v-if="rule.rule_type === 'required'"
                v-model="rule.fields"
                placeholder="store_name,store_code"
              />
              <span v-else class="muted">{{ rule.rule_type === 'amount_min' ? 'amount' : 'category' }}</span>
            </td>
            <td>
              <input
                v-if="rule.rule_type === 'amount_min'"
                v-model="rule.min"
                type="number"
                class="narrow"
                placeholder="0"
              />
              <input
                v-else-if="rule.rule_type === 'category_whitelist'"
                v-model="rule.allowed"
                :placeholder="fieldHint[rule.rule_type]"
              />
              <span v-else class="muted">{{ fieldHint[rule.rule_type] }}</span>
            </td>
            <td>
              <button class="secondary" :disabled="form.rules.length <= 1" @click="removeRule(i)">移除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="toolbar">
        <button class="secondary" @click="addRule">加一条规则</button>
        <button :disabled="busy || !form.version.trim()" @click="createPack">登记版本</button>
      </div>
    </div>
  </div>
</template>
<style scoped>
.rule-line { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; font-size: 13px; }
.rule-code { font-weight: 700; color: var(--accent); }
.rule-type { color: var(--muted); }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 10px; }
.form-grid label { display: grid; gap: 6px; font-size: 13px; color: var(--muted); }
input {
  font: inherit; padding: 7px 10px; border: 1px solid var(--line);
  border-radius: 2px; background: #fff; color: var(--ink); width: 100%;
}
input.narrow { max-width: 150px; }
.hint { margin: 0 0 12px; font-size: 13px; }
.active-pack { margin: 0; font-size: 15px; }
</style>
