import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, ChevronDown, CloudUpload, Download, MessageCircle,
  LifeBuoy, LogOut, Save, Search, Trash2, Usb,
} from 'lucide-react'
import Login from './Login.jsx'
import { Badge, TABS, filterRecords, failText, input, whatsappUrl } from './shared.jsx'

// dev: Vite on :5173 talks to the backend on :8000; production build is served by the backend itself (relative URLs)
const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '')
const EMPTY = { box_slot: '', client_phone: '', status: 'In Office', notes: '' }
const ROW = { 'Expiring Soon': 'bg-amber-50', Expired: 'bg-red-50' }

async function api(path, opts) {
  const res = await fetch(API + path, opts)
  if (!res.ok) {
    throw new Error(await failText(res))
  }
  return res
}

const statusOf = (days) => (days < 0 ? 'Expired' : days <= 30 ? 'Expiring Soon' : 'Active')

function Field({ label, children }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-600">{label}</span>
      {children}
    </label>
  )
}

export default function App() {
  const [records, setRecords] = useState([])
  const [q, setQ] = useState('')
  const [tab, setTab] = useState('all')
  const [certs, setCerts] = useState([])
  const [cur, setCur] = useState(null) // cert or record loaded in the form
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState('')
  const [toast, setToast] = useState(null)
  const [help, setHelp] = useState(false)
  const [notFound, setNotFound] = useState('')
  const [auth, setAuth] = useState(null) // null = checking, else {logged_in, email}
  const [offline, setOffline] = useState(false) // chose to work without cloud login

  const say = (text, error = false) => setToast({ text, error })
  const fail = (e) => say(e.message === 'Failed to fetch' ? 'Cannot reach backend on localhost:8000' : e.message, true)

  const loadRecords = useCallback(async () => {
    try {
      setRecords(await (await api('/api/records')).json())
    } catch (e) { fail(e) }
  }, [])
  useEffect(() => { loadRecords() }, [loadRecords])
  useEffect(() => { api('/api/auth/status').then((r) => r.json()).then(setAuth).catch((e) => { setAuth({ logged_in: false }); fail(e) }) }, [])
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(t)
  }, [toast])

  const stats = useMemo(() => ({
    total: records.length,
    active: records.filter((r) => r.expiry_status === 'Active').length,
    expiring: records.filter((r) => r.expiry_status === 'Expiring Soon').length,
    expired: records.filter((r) => r.expiry_status === 'Expired').length,
  }), [records])

  const shown = useMemo(() => filterRecords(records, q, tab), [records, q, tab])

  const existing = cur && records.find((r) => r.serial_number === cur.serial_number)

  function choose(c) {
    setCur(c)
    const old = records.find((r) => r.serial_number === c.serial_number)
    setForm(old ? { box_slot: old.box_slot, client_phone: old.client_phone, status: old.status, notes: old.notes } : EMPTY)
  }

  async function extract() {
    setBusy('extract')
    try {
      const { certificates: list, message } = await (await api('/api/extract')).json()
      setCerts(list)
      if (!list.length) { setCur(null); setHelp(true); setNotFound(message || 'No DSC found.'); return say(message || 'No DSC found.', true) }
      setNotFound('')
      choose(list[0])
      say(`${list.length} certificate(s) found; ${list.filter((c) => !c.already_registered).length} unregistered.`)
    } catch (e) { fail(e) } finally { setBusy('') }
  }

  async function save() {
    if (!cur) return say('Extract a token (or pick a row) first.', true)
    if (!form.box_slot.trim()) return say('Box / Slot Number is required.', true)
    if (existing && !window.confirm(`Already registered in [${existing.box_slot}] — update existing record?`)) return
    setBusy('save')
    try {
      const body = { ...form, serial_number: cur.serial_number, holder_name: cur.holder_name, issuer: cur.issuer, issue_date: cur.issue_date, expiry_date: cur.expiry_date }
      const r = await (await api('/api/records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json()
      const syncMsg = r.sync.configured ? (r.sync.ok ? ' Synced to cloud.' : ` Cloud sync failed: ${r.sync.error}`) : ''
      say(`${r.updated ? 'Updated' : 'Saved'} ${r.record.holder_name}.${syncMsg}`, r.sync.configured && !r.sync.ok)
      setCerts((cs) => cs.map((c) => (c.serial_number === cur.serial_number ? { ...c, already_registered: true, existing_box_slot: form.box_slot } : c)))
      setCur(null); setForm(EMPTY) // clear form only after a successful save
      loadRecords()
    } catch (e) { fail(e) } finally { setBusy('') }
  }

  async function remove(r) {
    if (!window.confirm(`Delete DSC record for ${r.holder_name} (${r.box_slot})?`)) return
    try {
      await api(`/api/records/${r.serial_number}`, { method: 'DELETE' })
      if (cur?.serial_number === r.serial_number) setCur(null)
      loadRecords()
    } catch (e) { fail(e) }
  }

  async function sync() {
    setBusy('sync')
    try {
      const { synced } = await (await api('/api/sync', { method: 'POST' })).json()
      say(`Synced ${synced} records to cloud.`)
    } catch (e) { fail(e) } finally { setBusy('') }
    api('/api/auth/status').then((r) => r.json()).then(setAuth).catch(() => {}) // token may have expired
  }

  async function doLogin(email, password) {
    setAuth(await (await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) })).json())
  }
  async function doLogout() { setAuth(await (await api('/api/auth/logout', { method: 'POST' })).json()) }

  async function exportXlsx() {
    try {
      const blob = await (await api('/api/export')).blob()
      const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'DSC_Tracker.xlsx' })
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) { fail(e) }
  }

  const curDays = cur && (cur.days_remaining ?? Math.round((new Date(cur.expiry_date) - new Date().setHours(0, 0, 0, 0)) / 864e5))
  const curStatus = cur && statusOf(curDays)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  if (!auth) return null
  if (!auth.logged_in && !offline) return <Login onSubmit={doLogin} onSkip={() => setOffline(true)} />

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-4 sm:p-6">
      <header className="flex items-center gap-3">
        <h1 className="flex-1 text-2xl font-bold text-slate-800">RMH Advisors <span className="font-normal text-slate-500">· DSC Tracker</span></h1>
        {auth.logged_in ? (
          <button onClick={doLogout} className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800"><LogOut size={16} /> {auth.email}</button>
        ) : (
          <button onClick={() => setOffline(false)} className="text-sm text-blue-600 underline">Offline - log in to sync</button>
        )}
      </header>

      {toast && (
        <div className={`fixed right-4 top-4 z-10 max-w-sm rounded-md px-4 py-3 text-sm shadow-lg ${toast.error ? 'bg-red-600 text-white' : 'bg-slate-800 text-white'}`}>{toast.text}</div>
      )}

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold text-slate-800">USB DSC Extractor</h2>
          <button onClick={extract} disabled={busy === 'extract'} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
            <Usb size={18} /> {busy === 'extract' ? 'Reading…' : 'Extract from Pen Drive'}
          </button>
          {certs.length > 1 && (
            <select value={cur?.serial_number ?? ''} onChange={(e) => choose(certs.find((c) => c.serial_number === e.target.value))} className={input + ' !w-auto min-w-80'}>
              {certs.map((c) => (
                <option key={c.serial_number} value={c.serial_number}>
                  {c.already_registered ? `Registered (${c.existing_box_slot})` : '★ NEW'} — {c.holder_name} — exp {c.expiry_date}
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="mb-4 rounded-md border border-blue-200 bg-blue-50 text-sm text-blue-900">
          <button onClick={() => setHelp(!help)} className="flex w-full items-center gap-2 px-4 py-2 font-medium">
            <LifeBuoy size={18} /> First-Time Setup / Driver Help
            <ChevronDown size={16} className={`ml-auto transition-transform ${help ? 'rotate-180' : ''}`} />
          </button>
          {help && (
            <div className="space-y-2 border-t border-blue-200 px-4 py-3">
              {notFound && <p className="rounded bg-red-100 px-3 py-2 font-medium text-red-800">No DSC found: {notFound}</p>}
              <ol className="list-decimal space-y-2 pl-5">
                <li><b>Plug in</b> your DSC pen drive (HyperPKI HYP2003, ePass2003, ProxKey, mToken, etc.) into the laptop.</li>
                <li>Open <b>File Explorer → This PC</b>, double-click the virtual CD/USB drive that appears for your token and run its <b>Setup</b> installer. Choose <b>Private CSP</b> if asked, and click <b>Yes</b> on the <b>CCA India 2022</b> security prompt.</li>
                <li>Click <b>“Extract from Pen Drive”</b> above.</li>
              </ol>
              <p className="text-blue-700">Only needed once per laptop. Still not detected? Open <code>/api/debug-tokens</code> on the backend (localhost:8000) to see what was found.</p>
            </div>
          )}
        </div>

        {existing && (
          <div className="mb-4 flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900">
            <AlertTriangle size={18} /> Already registered in <b>{existing.box_slot}</b>. Saving will update the existing record.
          </div>
        )}

        {cur ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <dl className="grid grid-cols-[8rem_1fr] items-center gap-x-3 gap-y-2 text-sm">
              {[['Holder Name', cur.holder_name], ['Serial Number', cur.serial_number], ['Issuer', cur.issuer], ['Issue Date', cur.issue_date], ['Expiry Date', cur.expiry_date]].map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-slate-500">{k}</dt>
                  <dd className="break-all rounded bg-slate-100 px-2 py-1 font-medium text-slate-800">{v}</dd>
                </div>
              ))}
              <dt className="text-slate-500">Status</dt>
              <dd><Badge status={curStatus}>{curStatus} ({curDays} days)</Badge></dd>
            </dl>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Box / Slot Number *"><input className={input} placeholder="Box A - 14" value={form.box_slot} onChange={set('box_slot')} /></Field>
              <Field label="Client Phone (WhatsApp)"><input className={input} placeholder="9876543210" value={form.client_phone} onChange={set('client_phone')} /></Field>
              <Field label="Status">
                <select className={input} value={form.status} onChange={set('status')}>
                  <option>In Office</option><option>Handed to Client</option>
                </select>
              </Field>
              <div className="sm:col-span-2"><Field label="Portals / Notes"><input className={input} placeholder="IT, GST, MCA" value={form.notes} onChange={set('notes')} /></Field></div>
              <button onClick={save} disabled={busy === 'save'} className="inline-flex items-center justify-center gap-2 rounded-md bg-green-600 px-4 py-2 text-sm font-semibold text-white hover:bg-green-700 disabled:opacity-60 sm:col-span-2">
                <Save size={18} /> Save Record
              </button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-500">Plug in a DSC token and click “Extract from Pen Drive”, or click a row below to edit it.</p>
        )}
      </section>

      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[['Total DSCs', stats.total, 'text-slate-800'], ['Active', stats.active, 'text-green-700'], ['Expiring in 30 Days', stats.expiring, 'text-amber-600'], ['Expired', stats.expired, 'text-red-600']].map(([k, v, c]) => (
          <div key={k} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <div className="text-sm text-slate-500">{k}</div>
            <div className={`text-3xl font-bold ${c}`}>{v}</div>
          </div>
        ))}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="relative min-w-60 flex-1">
            <Search size={16} className="absolute left-3 top-3 text-slate-400" />
            <input className={input + ' pl-9'} placeholder="Search client name, box/slot or serial" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="flex overflow-hidden rounded-md border border-slate-300 text-sm">
            {TABS.map(([k, l]) => (
              <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 ${tab === k ? 'bg-blue-600 text-white' : 'bg-white text-slate-700 hover:bg-slate-100'}`}>{l}</button>
            ))}
          </div>
          <button onClick={exportXlsx} className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100"><Download size={16} /> Export Excel</button>
          <button onClick={sync} disabled={busy === 'sync'} className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100 disabled:opacity-60"><CloudUpload size={16} /> Sync All to Cloud</button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>{['Holder', 'Box / Slot', 'Status', 'Days Left', 'Expiry', 'Token', 'Phone', 'Serial', 'Notes', ''].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.serial_number} onClick={() => { setCur(r); setForm({ box_slot: r.box_slot, client_phone: r.client_phone, status: r.status, notes: r.notes }); window.scrollTo({ top: 0, behavior: 'smooth' }) }}
                  className={`cursor-pointer border-b border-slate-100 hover:brightness-95 ${ROW[r.expiry_status] || ''}`}>
                  <td className="px-3 py-2 font-medium">{r.holder_name}</td>
                  <td className="px-3 py-2">{r.box_slot}</td>
                  <td className="px-3 py-2"><Badge status={r.expiry_status} /></td>
                  <td className="px-3 py-2">{r.days_remaining}</td>
                  <td className="px-3 py-2">{r.expiry_date}</td>
                  <td className="px-3 py-2">{r.status}</td>
                  <td className="px-3 py-2">{r.client_phone}</td>
                  <td className="max-w-40 truncate px-3 py-2 font-mono text-xs" title={r.serial_number}>{r.serial_number}</td>
                  <td className="px-3 py-2">{r.notes}</td>
                  <td className="whitespace-nowrap px-3 py-2" onClick={(e) => e.stopPropagation()}>
                    {r.expiry_status !== 'Active' && r.client_phone && (
                      <a href={whatsappUrl(r)} target="_blank" rel="noreferrer" title="WhatsApp reminder" className="mr-2 inline-flex text-green-600 hover:text-green-800"><MessageCircle size={18} /></a>
                    )}
                    <button onClick={() => remove(r)} title="Delete" className="text-slate-400 hover:text-red-600"><Trash2 size={16} /></button>
                  </td>
                </tr>
              ))}
              {!shown.length && <tr><td colSpan={10} className="px-3 py-8 text-center text-slate-400">No records</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
