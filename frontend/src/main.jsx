import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Mobile from './Mobile.jsx'

// `vite build --mode mobile` (see .env.mobile) -> phone/Capacitor build; everything else = desktop app
createRoot(document.getElementById('root')).render(import.meta.env.VITE_APP_MODE === 'mobile' ? <Mobile /> : <App />)
