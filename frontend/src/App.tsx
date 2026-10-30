import { BrowserRouter } from 'react-router-dom';
import { ThemeProvider } from '@/hooks/useTheme';
import { AuthProvider } from '@/hooks/useAuth';
import { ToastProvider } from '@/hooks/useToast';
import { AiSettingsProvider } from '@/hooks/useAiSettings';
import { ToastContainer } from '@/components/feature/ToastContainer';
import { AppRoutes } from '@/router';

export default function App() {
  return <ThemeProvider><BrowserRouter basename={__BASE_PATH__}>
    <ToastProvider><AiSettingsProvider><AuthProvider>
      <AppRoutes /><ToastContainer />
    </AuthProvider></AiSettingsProvider></ToastProvider>
  </BrowserRouter></ThemeProvider>;
}
