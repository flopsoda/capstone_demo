import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { DemoMonitor } from './monitor/DemoMonitor.tsx'

/**
 * Surfaces by URL path (no router):
 *   /monitor  — Demo Monitor: test / presentation observer of both backends' lifecycle
 *   /         — the platform UI
 */
const path = window.location.pathname
const surface = path.startsWith('/monitor') ? <DemoMonitor /> : <App />

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {surface}
  </StrictMode>,
)
