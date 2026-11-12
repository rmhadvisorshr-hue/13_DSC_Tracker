// Read-only phone view for Sir (VITE_APP_MODE=mobile): talks straight to cloud_api, no USB panel.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Capacitor } from '@capacitor/core'
import { PushNotifications } from '@capacitor/push-notifications'
import { LogOut, MessageCircle, RefreshCw, Search } from 'lucide-react'
import Login from './Login.jsx'
import { Badge, TABS, filterRecords, failText, input, whatsappUrl } from './shared.jsx'

const CLOUD = (import.meta.env.VITE_CLOUD_API_URL || '').replace(/\/$/, '')
const KEY = 'dsc_token'
const store = {
  get: () => { try { return localStorage.getItem(KEY) || '' } catch { return '' } },
  set: (v) => { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY) } catch { /* storage blocked */ } },
}
const FCM_KEY = 'dsc_fcm'
const label = () => 'Android ' + (navigator.userAgent.match(/Android [d.]+; ([^)]+))/)?.[1] || '')

// Ask permission, get the FCM token, register it with cloud_api. Safe no-op in a plain browser.
async function enablePush(token, { onTap, onForeground }) {
  if (!Capacitor.isNativePlatform()) return
  await PushNotifications.removeAllListeners()
  await PushNotifications.addListener('registration', async ({ value }) => {
    try {
      await cloud('/devices/register', token, { method: 'POST', body: JSON.stringify({ fcm_token: value, device_label: label() }) })
      localStorage.setItem(FCM_KEY, value)
    } catch { /* retried on next app start */ }
  })
  await PushNotifications.addListener('registrationError', (e) => console.warn('FCM registration failed', e))
  await PushNotifications.addListener('pushNotificationReceived', (n) => onForeground(n)) // app open
  await PushNotifications.addListener('pushNotificationActionPerformed', (a) => onTap(a.notification.data)) // tapped
  const perm = await PushNotifications.requestPermissions()
  if (perm.receive === 'granted') await PushNotifications.register()
}

const netMsg = (e) => (e.message === 'Failed to fetch' ? 'No internet connection' : e.message)

async function cloud(path, token, opts = {}) {
  const res = await fetch(CLOUD + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token && { Authorization: `Bearer ${token}` }) } })
  if (res.status === 401 || res.status === 403) throw Object.assign(new Error(await failText(res)), { auth: true })
  if (!res.ok) throw new Error(await failText(res))
  return res.json()
}

export default function Mobile() {
  const [token, setToken] = useState(store.get())
  const [records, setRecords] = useState([])
  const [q, setQ] = useState('')
  const [tab, setTab] = useState('all')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const [banner, setBanner] = useState(null)

  const logout = () => {
    try { // stop alerts to this phone
      const f = localStorage.getItem(FCM_KEY)
      if (f && token) cloud('/devices/unregister', token, { method: 'POST', body: JSON.stringify({ fcm_token: f }) }).catch(() => {})
      localStorage.removeItem(FCM_KEY)
    } catch { /* storage blocked */ }
    store.set(''); setToken(''); setRecords([])
  }
  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try { setRecords(await cloud('/records', token)) } catch (e) { e.auth ? logout() : setErr(netMsg(e)) } finally { setLoading(false) }
  }, [token])
  useEffect(() => { if (token) load() }, [token, load])
  useEffect(() => {
    if (!token) return
    enablePush(token, {
      onTap: () => { setQ(''); setTab('expiring'); load() }, // open straight to the expiring view
      onForeground: (n) => { setBanner(`${n.title}
${n.body}`); load() },
    }).catch((e) => console.warn('push setup failed', e))
  }, [token, load])
  useEffect(() => { if (banner) { const t = setTimeout(() => setBanner(null), 8000); return () => clearTimeout(t) } }, [banner])

  const shown = useMemo(() => filterRecords(records, q, tab), [records, q, tab])

  if (!token) {
    return <Login onSubmit={async (email, password) => {
      try {
        const r = await cloud('/auth/login', '', { method: 'POST', body: JSON.stringify({ email, password }) })
        store.set(r.token); setToken(r.token)
      } catch (e) { throw new Error(netMsg(e)) }
    }} />
  }

  return (
    <div className="mx-auto max-w-xl space-y-3 p-3">
      <header className="flex items-center gap-2">
        <h1 className="flex-1 text-lg font-bold text-slate-800">DSC Tracker <span className="font-normal text-slate-500">· {records.length}</span></h1>
        <button onClick={load} disabled={loading} aria-label="Refresh" className="rounded-md border border-slate-300 p-2"><RefreshCw size={18} className={loading ? 'animate-spin' : ''} /></button>
        <button onClick={logout} aria-label="Log out" className="rounded-md border border-slate-300 p-2"><LogOut size={18} /></button>
      </header>
      <div className="relative">
        <Search size={16} className="absolute left-3 top-3 text-slate-400" />
        <input className={input + ' pl-9'} placeholder="Search client, box/slot or serial" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="flex overflow-x-auto rounded-md border border-slate-300 text-sm">
        {TABS.map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`flex-1 whitespace-nowrap px-3 py-2 ${tab === k ? 'bg-blue-600 text-white' : 'bg-white text-slate-700'}`}>{l}</button>
        ))}
      </div>
      {banner && (
        <button onClick={() => { setBanner(null); setQ(''); setTab('expiring') }} className="w-full whitespace-pre-line rounded-md bg-amber-100 px-3 py-2 text-left text-sm font-medium text-amber-900">{banner}</button>
      )}
      {err && <p className="rounded bg-red-100 px-3 py-2 text-sm text-red-800">{err}</p>}
      {shown.map((r) => (
        <div key={r.serial_number} className="space-y-2 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="font-semibold text-slate-800">{r.holder_name}</div>
            <Badge status={r.expiry_status} />
          </div>
          <div className="rounded-md bg-blue-50 px-3 py-2 text-center text-xl font-bold text-blue-800">{r.box_slot}</div>
          <div className="flex justify-between text-sm text-slate-600">
            <span>Expiry {r.expiry_date} ({r.days_remaining} days)</span><span>{r.status}</span>
          </div>
          {r.notes && <div className="text-sm text-slate-500">{r.notes}</div>}
          {r.client_phone && (
            <a href={whatsappUrl(r)} target="_blank" rel="noreferrer" className="flex items-center justify-center gap-2 rounded-md bg-green-600 py-2 text-sm font-semibold text-white">
              <MessageCircle size={18} /> WhatsApp reminder
            </a>
          )}
        </div>
      ))}
      {!shown.length && !loading && <p className="py-8 text-center text-slate-400">No records</p>}
    </div>
  )
}
