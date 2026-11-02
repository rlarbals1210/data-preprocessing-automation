import { Badge, Group, Loader, Stepper, Text, Title } from '@mantine/core';
import { IconCheck, IconMinus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { FINISHED, api, type Run } from '../api';
import { RUN_STATUS, SET_LABEL, when } from '../labels';
import Diagnosis from './Diagnosis';
import Mapping from './Mapping';
import Preview from './Preview';
import Result from './Result';

const DIAGNOSED: Run['status'][] = ['diagnosed', 'awaiting_approval', ...FINISHED];

type Step = { key: string; label: string; ready?: (r: Run) => boolean; disabled?: boolean };
const STEPS: Step[] = [
  { key: 'upload', label: '업로드', disabled: true },
  { key: 'drift', label: '스키마 변경', disabled: true },
  { key: 'mapping', label: '매핑 확인', ready: () => true },
  { key: 'diagnosis', label: '진단', ready: (r) => DIAGNOSED.includes(r.status) },
  { key: 'integrity', label: '정합성', disabled: true },
  { key: 'preview', label: '미리보기·승인', ready: (r) => DIAGNOSED.includes(r.status) },
  { key: 'result', label: '결과', ready: (r) => FINISHED.includes(r.status) },
];

export default function RunPage() {
  const { id = '' } = useParams();
  const { pathname } = useLocation();
  const nav = useNavigate();
  const run = useQuery({ queryKey: ['run', id], queryFn: () => api.run(id) });
  if (run.isLoading) return <Loader />;
  if (run.error || !run.data) return <Text c="red">{(run.error as Error)?.message}</Text>;
  const r = run.data;
  const current = pathname.split('/')[3] ?? '';
  const active = STEPS.findIndex((s) => s.key === current);
  const start = FINISHED.includes(r.status)
    ? 'result'
    : r.status === 'awaiting_approval'
      ? 'preview'
      : r.status === 'diagnosed'
        ? 'diagnosis'
        : 'mapping';

  return (
    <>
      <Group justify="space-between" mb="md">
        <Group gap="sm">
          <Title order={3} ff="monospace" fz="lg">
            {r.id}
          </Title>
          <Badge variant="light" color={RUN_STATUS[r.status]?.color}>
            {RUN_STATUS[r.status]?.label}
          </Badge>
        </Group>
        <Text c="dimmed" fz="sm">
          {r.source} · {SET_LABEL[r.set]} · {when(r.created_at)}
        </Text>
      </Group>
      <Stepper
        active={active === -1 ? 0 : active}
        size="xs"
        iconSize={26}
        wrap={false}
        mb="lg"
        onStepClick={(i) => {
          const s = STEPS[i];
          if (!s.disabled && s.ready?.(r)) nav(`/runs/${id}/${s.key}`);
        }}
        styles={{ separator: { marginInline: 6, minWidth: 8 }, stepLabel: { whiteSpace: 'nowrap' } }}
      >
        {STEPS.map((s, i) => (
          <Stepper.Step
            key={s.key}
            label={s.label}
            color={s.disabled ? 'gray' : undefined}
            completedIcon={s.disabled ? <IconMinus size={14} /> : <IconCheck size={14} />}
            allowStepSelect={!s.disabled && !!s.ready?.(r) && i !== active}
            style={s.disabled ? { opacity: 0.45 } : undefined}
          />
        ))}
      </Stepper>
      <Routes>
        <Route index element={<Navigate to={start} replace />} />
        <Route path="mapping" element={<Mapping key={r.id} run={r} />} />
        <Route path="diagnosis" element={<Diagnosis run={r} />} />
        <Route path="preview" element={<Preview run={r} />} />
        <Route path="result" element={<Result run={r} />} />
        <Route path="*" element={<Navigate to={start} replace />} />
      </Routes>
    </>
  );
}
