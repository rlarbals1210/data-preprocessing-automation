import { Alert, Anchor, Badge, Button, Group, Loader, Popover, Stepper, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconMinus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { FINISHED, api, type Run } from '../api';
import { APPROVAL_KIND, RUN_STATUS, SET_LABEL, invalidated, when } from '../labels';
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
          {r.retry_of && (
            <Anchor component={Link} to={`/runs/${r.retry_of}`} fz="sm" c="dimmed">
              ← 재시도 원본
            </Anchor>
          )}
        </Group>
        <Group gap="sm">
          <Text c="dimmed" fz="sm">
            {r.source} · {SET_LABEL[r.set]} · {when(r.created_at)}
          </Text>
          {!!r.approvals?.length && (
            <Popover position="bottom-end" shadow="md" width={420}>
              <Popover.Target>
                <Button size="compact-xs" variant="subtle" color="gray">
                  승인 기록 {r.approvals.length}
                </Button>
              </Popover.Target>
              <Popover.Dropdown>
                <Table fz="xs" data-testid="approvals">
                  <Table.Tbody>
                    {r.approvals.map((a) => (
                      <Table.Tr key={a.id}>
                        <Table.Td>{APPROVAL_KIND[a.kind] ?? a.kind}</Table.Td>
                        <Table.Td c="dimmed">{when(a.created_at)}</Table.Td>
                        <Table.Td>
                          {a.invalidated_at ? (
                            <Badge size="xs" color="gray" variant="light">
                              무효 · {invalidated(a.invalidated_reason)}
                            </Badge>
                          ) : (
                            <Badge size="xs" color="teal" variant="light">
                              유효
                            </Badge>
                          )}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Popover.Dropdown>
            </Popover>
          )}
        </Group>
      </Group>
      {r.status === 'needs_confirmation' && r.status_reason === 'mapping_changed' && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />} mb="md" data-testid="invalidated">
          매핑이 바뀌어 이전 진단·미리보기·승인이 무효가 됐습니다. 다시 진단하세요.
        </Alert>
      )}
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
