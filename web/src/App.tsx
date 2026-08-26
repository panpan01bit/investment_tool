import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import BriefingPage from './pages/BriefingPage';
import PortfolioPage from './pages/PortfolioPage';
import ResearchPage from './pages/ResearchPage';
import QuantPage from './pages/QuantPage';
import ReportsPage from './pages/ReportsPage';
import ChatPage from './pages/ChatPage';
import SettingsPage from './pages/SettingsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<BriefingPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/quant" element={<QuantPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
