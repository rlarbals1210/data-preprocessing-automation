import { Alert, Badge, Button, Card, Checkbox, Code, Group, Loader, Modal, Stack, Table, Text } from '@mantine/core';
import { IconArrowRight, IconCheck } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Run } from '../api';
import { CHANGE, CODE, fmt } from '../labels';

export default function Preview({ run }: { run: Run }) {
  const p = useQuery({ queryKey: ['preview', run.id], queryFn: () => api.preview(run.id) });
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const nav = useNavigate();
  const select = useMutation({
    mutationFn: (ids: string[]) => api.select(run.id, ids),
    onSuccess: (data) => qc.setQueryData(['preview', run.id], data),
  });
  const execute = useMutation({
    mutationFn: () => api.execute(run.id),
    onSuccess: () => {
      setOpen(false);
      ['run', 'result', 'changes'].forEach((k) => qc.invalidateQueries({ queryKey: [k, run.id] }));
      qc.invalidateQueries({ queryKey: ['runs'] });
      nav(`/runs/${run.id}/result`);
    },
  });
  if (p.isLoading) return <Loader />;
  if (p.error || !p.data) return <Alert color="red">{(p.error as Error)?.message}</Alert>;
  const x = p.data;
  const selectable = x.groups.filter((g) => g.cells > 0);
  const approved = selectable.filter((g) => g.approved).map((g) => g.id);
  const toggle = (id: string, on: boolean) =>
    select.mutate(on ? [...approved, id] : approved.filter((a) => a !== id));
  const allOn = approved.length === selectable.length;

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="lg">
          <Text>
            규칙 묶음 <b>{approved.length}</b> / {selectable.length}
          </Text>
          <Text>
            변경 셀 <b>{fmt(x.total_cells)}</b> / {fmt(x.candidates)}
          </Text>
          {x.held > 0 && (
            <Text c="orange.7">
              보류 <b>{fmt(x.held)}</b>
            </Text>
          )}
          {select.isPending && <Loader size="xs" />}
        </Group>
        <Button leftSection={<IconCheck size={16} />} disabled={!x.total_cells || select.isPending} onClick={() => setOpen(true)}>
          최종 확인
        </Button>
      </Group>
      {select.error && <Alert color="red">{(select.error as Error).message}</Alert>}
      <Card withBorder p={0}>
        <Table verticalSpacing="sm" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40}>
                <Checkbox
                  checked={allOn}
                  indeterminate={!allOn && approved.length > 0}
                  onChange={() => select.mutate(allOn ? [] : selectable.map((g) => g.id))}
                  aria-label="전체"
                />
              </Table.Th>
              <Table.Th>테이블</Table.Th>
              <Table.Th>컬럼</Table.Th>
              <Table.Th>규칙</Table.Th>
              <Table.Th ta="right">셀</Table.Th>
              <Table.Th>예시</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {x.groups.map((g) => (
              <Table.Tr key={g.id} style={{ opacity: g.approved || !g.cells ? 1 : 0.5 }}>
                <Table.Td>
                  <Checkbox checked={g.approved} disabled={!g.cells} onChange={(e) => toggle(g.id, e.currentTarget.checked)} />
                </Table.Td>
                <Table.Td ff="monospace">{g.table}</Table.Td>
                <Table.Td ff="monospace">{g.column}</Table.Td>
                <Table.Td>
                  <Badge variant="light">{CHANGE[g.code] ?? CODE[g.code] ?? g.code}</Badge>
                </Table.Td>
                <Table.Td ta="right">
                  {fmt(g.cells)}
                  {g.held > 0 && (
                    <Text span c="orange.7" fz="xs">
                      {' '}
                      +보류 {fmt(g.held)}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Stack gap={2}>
                    {g.examples.map((e, i) => (
                      <Group key={i} gap={6} wrap="nowrap">
                        <Code>{e.before || '∅'}</Code>
                        <IconArrowRight size={12} />
                        <Code color="teal.1">{e.after || '∅'}</Code>
                      </Group>
                    ))}
                  </Stack>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      <Modal opened={open} onClose={() => setOpen(false)} title="최종 확인" centered>
        <Stack>
          <Group gap="lg">
            <Text>
              규칙 묶음 <b>{approved.length}</b>
            </Text>
            <Text>
              변경 셀 <b>{fmt(x.total_cells)}</b>
            </Text>
          </Group>
          {execute.error && <Alert color="red">{(execute.error as Error).message}</Alert>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button loading={execute.isPending} onClick={() => execute.mutate()}>
              실행
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
