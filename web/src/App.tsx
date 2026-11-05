import { AppShell, Group, NavLink, Text, ThemeIcon } from '@mantine/core';
import { IconChartBar, IconHistory, IconHome, IconRecycle, IconTable, IconUpload } from '@tabler/icons-react';
import { Link, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import History from './pages/History';
import Home from './pages/Home';
import Reuse from './pages/Reuse';
import RunPage from './pages/Run';
import Upload from './pages/Upload';

const NAV = [
  { to: '/', label: '홈', icon: IconHome },
  { to: '/upload', label: '새 업로드', icon: IconUpload },
  { to: '/history', label: '실행 이력', icon: IconHistory },
  { to: '/reuse', label: '재사용 규칙', icon: IconRecycle },
  { to: '/quality', label: '품질 대시보드', icon: IconChartBar, disabled: true },
];

export default function App() {
  const { pathname } = useLocation();
  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 220, breakpoint: 'sm' }} padding="lg">
      <AppShell.Header>
        <Group h="100%" px="lg" gap="sm">
          <ThemeIcon variant="light" size="md">
            <IconTable size={18} />
          </ThemeIcon>
          <Text fw={700}>데이터 전처리</Text>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p="sm">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            component={Link}
            to={n.to}
            label={n.label}
            leftSection={<n.icon size={18} />}
            active={n.to === '/' ? pathname === '/' || pathname.startsWith('/runs') : pathname.startsWith(n.to)}
            disabled={n.disabled}
          />
        ))}
      </AppShell.Navbar>
      <AppShell.Main bg="gray.0">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/upload" element={<Upload />} />
          <Route path="/history" element={<History />} />
          <Route path="/reuse" element={<Reuse />} />
          <Route path="/runs/:id/*" element={<RunPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
  );
}
