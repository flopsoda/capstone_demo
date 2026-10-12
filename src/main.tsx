import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { DemoMonitor } from './monitor/DemoMonitor.tsx'
import { GROUP_PLATFORM, K_PLATFORM } from './platform/config.ts'
import { PlatformApp } from './platform/PlatformApp.tsx'

/**
 * Surfaces by URL path (no router):
 *   /group    — Group platform UI (product)          ← also the default
 *   /k        — K Electronics platform UI (product)
 *   /monitor  — Demo Monitor: test / presentation observer of both backends' lifecycle
 */
function surface() {
  const path = window.location.pathname
  if (path.startsWith('/monitor')) return <DemoMonitor />
  if (path.startsWith('/k')) return <PlatformApp config={K_PLATFORM} />
  return <PlatformApp config={GROUP_PLATFORM} />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {surface()}
  </StrictMode>,
)
