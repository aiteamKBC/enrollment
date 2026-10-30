import { StrictMode } from 'react'
import './i18n'
import { createRoot } from 'react-dom/client'
import './index.css'
import './learner-theme.css'
import App from './App.tsx'
import './coach-theme.css'
import { installAuditRequestContext } from './lib/auditRequestContext'
import { installPerformanceDiagnostics } from './lib/performanceDiagnostics'

installPerformanceDiagnostics()
installAuditRequestContext()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
