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
  const execute = useMutation({
    mutationFn: () => api.execute(run.id),
    onSuccess: () => {
      setOpen(false);
      qc.invalidateQueries({ queryKey: ['run', run.id] });
      qc.invalidateQueries({ queryKey: ['result', run.id] });
      qc.invalidateQueries({ queryKey: ['changes', run.id] });
      qc.invalidateQueries({ queryKey: ['runs'] });
      nav(`/runs/${run.id}/result`);
    },
  });
  if (p.isLoading) return <Loader />;
  if (p.error || !p.data) return <Alert color="red">{(p.error as Error)?.message}</Alert>;

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="lg">
          <Text>
            규칙 묶음 <b>{p.data.groups.length}</b>
          </Text>
          <Text>
            변경 셀 <b>{fmt(p.data.total_cells)}</b>
          </Text>
        </Group>
        <Button leftSection={<IconCheck size={16} />} disabled={!p.data.total_cells} onClick={() => setOpen(true)}>
          최종 확인
        </Button>
      </Group>
      <Card withBorder p={0}>
        <Table verticalSpacing="sm" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40}>
                <Checkbox checked readOnly aria-label="전체" />
              </Table.Th>
              <Table.Th>테이블</Table.Th>
              <Table.Th>컬럼</Table.Th>
              <Table.Th>규칙</Table.Th>
              <Table.Th ta="right">셀</Table.Th>
              <Table.Th>예시</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {p.data.groups.map((g) => (
              <Table.Tr key={`${g.table}.${g.column}.${g.code}`}>
                <Table.Td>
                  <Checkbox checked readOnly />
                </Table.Td>
                <Table.Td ff="monospace">{g.table}</Table.Td>
                <Table.Td ff="monospace">{g.column}</Table.Td>
                <Table.Td>
                  <Badge variant="light" tt="none">{CHANGE[g.code] ?? CODE[g.code] ?? g.code}</Badge>
                </Table.Td>
                <Table.Td ta="right">{fmt(g.cells)}</Table.Td>
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
              규칙 묶음 <b>{p.data.groups.length}</b>
            </Text>
            <Text>
              변경 셀 <b>{fmt(p.data.total_cells)}</b>
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
