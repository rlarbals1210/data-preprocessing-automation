import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, Select, Stack, Table, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api, type DriftChange, type DriftItem, type Run } from '../api';
import { DRIFT_KIND, DRIFT_STATE } from '../labels';

const NEW = 'new';

const list = (xs?: string[]) => (xs && xs.length ? xs.join(', ') : '—');
const range = (r: unknown) => {
  const v = r as { min: string | null; max: string | null } | undefined;
  return v ? `${v.min ?? '—'} ~ ${v.max ?? '—'}` : '—';
};

function cells(c: DriftChange): [string, string, string] {
  switch (c.kind) {
    case 'raw_headers':
      return [c.order_only ? '순서' : list([...(c.removed ?? []), ...(c.added ?? [])]), list(c.before as string[]), list(c.after as string[])];
    case 'column_removed':
      return [c.column ?? '', c.column ?? '', '—'];
    case 'column_added':
      return [c.column ?? '', '—', c.column ?? ''];
    case 'rename_suspected':
      return [`${c.before} → ${c.after}`, `${c.before} (${c.dominant})`, `${c.after} · 겹침 ${c.overlap}`];
    case 'rename_undetermined':
    case 'shape_undetermined':
      return [
        c.kind === 'rename_undetermined' ? `${c.before} → ${c.after}` : c.column ?? '',
        `표본 ${c.filled?.before ?? 0}`,
        `표본 ${c.filled?.after ?? 0}`,
      ];
    case 'value_shape':
      return [c.column ?? '', String(c.before ?? '—'), `${c.after ?? '—'} · 새 모양 ${c.new_ratio}`];
    case 'value_range':
      return [
        c.column ?? '',
        range(c.before),
        `${range(c.after)}${c.unparsed ? ` · 해석 불가 ${c.unparsed}` : ''}${c.tz_naive ? ` · 시간대 없음 ${c.tz_naive}` : ''}`,
      ];
    case 'category_new':
      return [c.column ?? '', '—', `${list(c.values)}${(c.count ?? 0) > (c.values?.length ?? 0) ? ` 외 ${(c.count ?? 0) - (c.values?.length ?? 0)}` : ''}`];
    default:
      return [c.column ?? '', '', ''];
  }
}

function ItemCard({ run, item, editable }: { run: Run; item: DriftItem; editable: boolean }) {
  const qc = useQueryClient();
  const select = useMutation({
    mutationFn: (v: string | null) => api.driftSelect(run.id, item.item_id, v === NEW || v === null ? null : Number(v)),
    onSuccess: (d) => {
      qc.setQueryData(['drift', run.id], d);
      qc.invalidateQueries({ queryKey: ['run', run.id] });
    },
  });
  const state = DRIFT_STATE[item.state];
  const value = item.template_id === null ? (item.state === 'choose' ? null : NEW) : String(item.template_id);
  return (
    <Card withBorder data-testid={`drift-item-${item.item_id}`}>
      <Group justify="space-between" mb="sm">
        <Group gap="sm">
          <Text fw={600}>
            {item.name}
            {item.sheet ? ` [${item.sheet}]` : ''}
          </Text>
          <Badge variant="light" color={state.color} data-testid="drift-state">
            {state.label}
          </Badge>
          {item.std_table && (
            <Badge variant="outline" color="gray">
              {item.std_table}
            </Badge>
          )}
        </Group>
        <Group gap="sm">
          {item.baseline && (
            <Text fz="sm" c="dimmed">
              기준 결과 버전 #{item.baseline.result_version_id}
              {item.baseline.run && (
                <>
                  {' · '}
                  <Anchor component={Link} to={`/runs/${item.baseline.run}/result`} fz="sm">
                    {item.baseline.run}
                  </Anchor>
                </>
              )}
            </Text>
          )}
          {item.candidates.length > 0 && (
            <Select
              size="xs"
              w={260}
              placeholder="비교 기준"
              aria-label="비교 기준"
              data-testid="drift-select"
              disabled={!editable || select.isPending}
              value={value}
              onChange={(v) => v && select.mutate(v)}
              data={[
                ...item.candidates.map((c) => ({
                  value: String(c.template_id),
                  label: `양식 #${c.template_id} · ${c.std_table} · 겹침 ${c.overlap}/${c.size}`,
                })),
                { value: NEW, label: '기준 생성' },
              ]}
            />
          )}
        </Group>
      </Group>
      {select.error && (
        <Alert color="red" mb="sm">
          {(select.error as Error).message}
        </Alert>
      )}
      {item.changes.length > 0 && (
        <Table fz="sm" verticalSpacing={4} data-testid="drift-changes">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>변화</Table.Th>
              <Table.Th>컬럼</Table.Th>
              <Table.Th>이전</Table.Th>
              <Table.Th>이번</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {item.changes.map((c, i) => {
              const [col, before, after] = cells(c);
              return (
                <Table.Tr key={i}>
                  <Table.Td>
                    <Badge size="sm" variant="light" color={c.kind.endsWith('undetermined') ? 'gray' : 'orange'}>
                      {DRIFT_KIND[c.kind] ?? c.kind}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Code>{col}</Code>
                  </Table.Td>
                  <Table.Td>{before}</Table.Td>
                  <Table.Td>{after}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}

export default function Drift({ run }: { run: Run }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const d = useQuery({ queryKey: ['drift', run.id], queryFn: () => api.drift(run.id) });
  const confirm = useMutation({
    mutationFn: () => api.driftConfirm(run.id),
    onSuccess: async (data) => {
      qc.setQueryData(['drift', run.id], data);
      await qc.invalidateQueries({ queryKey: ['run', run.id] }); // 확인 상태가 반영된 뒤 매핑으로 간다
      nav(`/runs/${run.id}/mapping`);
    },
  });
  if (d.isLoading) return <Loader />;
  if (d.error || !d.data) return <Alert color="red">{(d.error as Error)?.message}</Alert>;
  const mappingConfirmed = run.files.some((f) => f.confirmed && !f.read_error);
  const editable = run.status === 'needs_confirmation' && !mappingConfirmed;
  const pending = d.data.items.some((i) => i.state === 'choose');
  return (
    <Stack>
      {d.data.items.map((item) => (
        <ItemCard key={item.item_id} run={run} item={item} editable={editable} />
      ))}
      {confirm.error && <Alert color="red">{(confirm.error as Error).message}</Alert>}
      <Group justify="flex-end">
        {d.data.required && !d.data.confirmed ? (
          <Button data-testid="drift-confirm" disabled={!editable || pending} loading={confirm.isPending} onClick={() => confirm.mutate()}>
            변경 확인
          </Button>
        ) : (
          <Button variant="light" data-testid="drift-next" onClick={() => nav(`/runs/${run.id}/mapping`)}>
            매핑 확인
          </Button>
        )}
      </Group>
    </Stack>
  );
}
