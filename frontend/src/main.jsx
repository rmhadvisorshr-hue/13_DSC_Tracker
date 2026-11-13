import { Component } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Mobile from './Mobile.jsx'

// Shows startup/render errors on screen instead of a blank page
class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    const e = this.state.error
    return e ? <pre style={{ padding: 16, whiteSpace: 'pre-wrap', color: '#b91c1c' }}>App error: {String(e.stack || e.message || e)}</pre> : this.props.children
  }
}

// `vite build --mode mobile` (see .env.mobile) -> phone/Capacitor build; everything else = desktop app
createRoot(document.getElementById('root')).render(
  <ErrorBoundary>{import.meta.env.VITE_APP_MODE === 'mobile' ? <Mobile /> : <App />}</ErrorBoundary>
)
