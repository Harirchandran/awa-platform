import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';
import './index.css';
import App from './App.tsx';
import { DisplayPreferencesProvider } from './components/accessibility/DisplayPreferences';
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <DisplayPreferencesProvider>
        <Toaster position="top-right" />
        <App />
      </DisplayPreferencesProvider>
    </QueryClientProvider>
  </StrictMode>
);