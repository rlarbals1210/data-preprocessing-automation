import { Alert, Badge, Button, Card, Group, Select, SimpleGrid, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconArrowRight, IconCheck } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type FileEntry, type Mapping as MappingT, type Proposal, type Run } from '../api';
import { GRADE, REASON, TABLE_BASIS, fmt } from '../labels';

const EXCLUDE = '__exclude__';
type Draft = MappingT & { proposal: Proposal | null };

const same = (a: MappingT, b: MappingT) => JSON.stringify(a) === JSON.stringify(b);

export default function Mapping({ run }: { run: Run }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const cat = run.catalog ?? null;
  const initial = useMemo(
    () =>
      Object.fromEntries(
        run.files.map((f) => [f.item_id, { ...f.mapping, columns: { ...f.mapping.columns }, proposal: f.proposal }]),
      ) as Record<number, Draft>,
    [run],
  );
  const [drafts, setDrafts] = useState(initial);
  const readable = run.files.filter((f) => !f.read_error);
  const dirty = readable.some((f) => !same({ table: drafts[f.item_id].table, columns: drafts[f.item_id].columns }, f.mapping));
  const allConfirmed = run.files.every((f) => f.confirmed);

  const tableCount: Record<string, number> = {};
  readable.forEach((f) => {
    const t = drafts[f.item_id].table;
    if (t) tableCount[t] = (tableCount[t] ?? 0) + 1;
  });
  const dupCols = (d: Draft) => {
    const c: Record<string, number> = {};
    Object.values(d.columns).forEach((v) => v && (c[v] = (c[v] ?? 0) + 1));
    return new Set(Object.keys(c).filter((k) => c[k] > 1));
  };
  const blocking =
    Object.values(tableCount).some((n) => n > 1) ||
    readable.some((f) => drafts[f.item_id].table && dupCols(drafts[f.item_id]).size > 0) ||
    !readable.some((f) => drafts[f.item_id].table);
  const pending = readable.reduce(
    (n, f) => n + (drafts[f.item_id].proposal?.columns.filter((c) => c.needs_confirmation).length ?? 0),
    0,
  );

  const invalidate = () => {
    ['run', 'diagnosis', 'preview', 'result', 'changes'].forEach((k) => qc.invalidateQueries({ queryKey: [k, run.id] }));
    qc.invalidateQueries({ queryKey: ['runs'] });
  };
  const confirm = useMutation({
    mutationFn: () =>
      api.confirmMapping(
        run.id,
        readable.map((f) => ({ item_id: f.item_id, table: drafts[f.item_id].table, columns: drafts[f.item_id].columns })),
      ),
    onSuccess: invalidate,
  });
  const diagnose = useMutation({
    mutationFn: () => api.diagnose(run.id),
    onSuccess: () => {
      invalidate();
      nav(`/runs/${run.id}/diagnosis`);
    },
  });
  const changeTable = async (f: FileEntry, table: string | null) => {
    if (!table || table === EXCLUDE) {
      setDrafts((d) => ({ ...d, [f.item_id]: { ...d[f.item_id], table: null, columns: {} } }));
      return;
    }
    const p = await api.propose(run.id, f.item_id, table);
    setDrafts((d) => ({
      ...d,
      [f.item_id]: { table, columns: Object.fromEntries(p.columns.map((c) => [c.original, c.logical])), proposal: p },
    }));
  };
  const setCol = (id: number, original: string, logical: string | null) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], columns: { ...d[id].columns, [original]: logical } } }));

  const mapped = readable.reduce((n, f) => n + Object.values(drafts[f.item_id].columns).filter(Boolean).length, 0);
  const total = readable.reduce((n, f) => n + f.headers.length, 0);

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="lg">
          <Text>
            파일 <b>{run.files.length}</b>
          </Text>
          <Text>
            행 <b>{fmt(run.rows)}</b>
          </Text>
          {cat && (
            <>
              <Text>
                매핑 <b>{mapped}</b> / {total}
              </Text>
              <Text c={pending ? 'orange.7' : undefined}>
                확인 필요 <b>{pending}</b>
              </Text>
            </>
          )}
        </Group>
        <Group>
          {cat && (
            <Button
              variant={dirty || !allConfirmed ? 'filled' : 'light'}
              leftSection={<IconCheck size={16} />}
              disabled={blocking || (!dirty && allConfirmed)}
              loading={confirm.isPending}
              onClick={() => confirm.mutate()}
            >
              매핑 확정
            </Button>
          )}
          <Button
            rightSection={<IconArrowRight size={16} />}
            disabled={!allConfirmed || dirty}
            loading={diagnose.isPending}
            onClick={() => diagnose.mutate()}
          >
            {run.status === 'uploaded' ? '진단 실행' : '다시 진단'}
          </Button>
        </Group>
      </Group>
      {(confirm.error || diagnose.error) && (
        <Alert color="red">{((confirm.error || diagnose.error) as Error).message}</Alert>
      )}
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        {run.files.map((f) => {
          const d = drafts[f.item_id];
          const dups = dupCols(d);
          const byOriginal = Object.fromEntries((d.proposal?.columns ?? []).map((c) => [c.original, c]));
          const required = d.table && cat ? cat[d.table].required : [];
          const missing = required.filter((r) => !Object.values(d.columns).includes(r));
          const used = new Set(Object.values(d.columns).filter(Boolean));
          return (
            <Card key={f.item_id} withBorder>
              <Group justify="space-between" mb="xs" wrap="nowrap">
                <div>
                  <Text fw={600}>{f.name}</Text>
                  <Text fz="sm" c="dimmed">
                    {fmt(f.rows)}행 · {f.headers.length}열
                    {d.proposal?.table.basis && ` · ${TABLE_BASIS[d.proposal.table.basis]}`}
                  </Text>
                </div>
                {cat && !f.read_error && (
                  <Select
                    w={220}
                    value={d.table ?? EXCLUDE}
                    onChange={(v) => changeTable(f, v)}
                    data={[{ value: EXCLUDE, label: '제외' }, ...Object.keys(cat).map((t) => ({ value: t, label: t }))]}
                    error={d.table && tableCount[d.table] > 1 ? '중복' : undefined}
                    allowDeselect={false}
                    styles={{ input: { fontFamily: 'monospace' } }}
                  />
                )}
              </Group>
              {f.read_error ? (
                <Alert color="red" p="xs">
                  {f.read_error}
                </Alert>
              ) : !cat ? (
                <Text fz="sm" ff="monospace">
                  {f.headers.join(', ')}
                </Text>
              ) : !d.table ? null : (
                <>
                  {missing.length > 0 && (
                    <Text fz="sm" c="red.7" mb="xs">
                      필수 컬럼 없음: {missing.join(', ')}
                    </Text>
                  )}
                  <Table fz="sm" verticalSpacing={4}>
                    <Table.Tbody>
                      {f.headers.map((h) => {
                        const p = byOriginal[h];
                        const v = d.columns[h] ?? null;
                        const edited = p && v !== p.logical;
                        return (
                          <Table.Tr key={h} bg={p?.needs_confirmation && !edited ? 'orange.0' : undefined}>
                            <Table.Td style={{ whiteSpace: 'nowrap' }}>{h}</Table.Td>
                            <Table.Td c="dimmed" w={20}>
                              →
                            </Table.Td>
                            <Table.Td>
                              <Select
                                size="xs"
                                value={v}
                                placeholder="추가 컬럼"
                                clearable
                                searchable
                                onChange={(val) => setCol(f.item_id, h, val)}
                                data={cat[d.table!].columns.map((c) => ({
                                  value: c,
                                  label: c,
                                  disabled: used.has(c) && c !== v,
                                }))}
                                error={v && dups.has(v) ? true : undefined}
                                styles={{ input: { fontFamily: 'monospace' } }}
                              />
                            </Table.Td>
                            <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>
                              {edited ? (
                                <Badge size="sm" variant="light" color="violet">
                                  수정
                                </Badge>
                              ) : p ? (
                                <Tooltip
                                  label={[
                                    ...p.reasons.map((r) => REASON[r] ?? r),
                                    ...p.candidates.map((c) => `${c.logical}${c.score ? ` ${c.score}` : ''}`),
                                  ].join(' · ')}
                                  disabled={!p.reasons.length && !p.candidates.length}
                                >
                                  <Badge
                                    size="sm"
                                    variant={p.needs_confirmation ? 'filled' : 'light'}
                                    color={p.needs_confirmation ? 'orange' : GRADE[p.grade].color}
                                  >
                                    {p.needs_confirmation ? `확인 필요 · ${GRADE[p.grade].label}` : GRADE[p.grade].label}
                                  </Badge>
                                </Tooltip>
                              ) : null}
                            </Table.Td>
                          </Table.Tr>
                        );
                      })}
                    </Table.Tbody>
                  </Table>
                </>
              )}
            </Card>
          );
        })}
      </SimpleGrid>
    </Stack>
  );
}
