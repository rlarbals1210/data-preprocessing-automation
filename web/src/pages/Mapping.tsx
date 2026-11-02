import { Alert, Badge, Button, Card, Group, SimpleGrid, Stack, Table, Text } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, type Run } from '../api';
import { fmt } from '../labels';

const BASIS: Record<string, { label: string; color: string }> = {
  legacy_map: { label: '대응표', color: 'teal' },
  exact: { label: '정확 일치', color: 'blue' },
};

export default function Mapping({ run }: { run: Run }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const diagnose = useMutation({
    mutationFn: () => api.diagnose(run.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run', run.id] });
      qc.invalidateQueries({ queryKey: ['diagnosis', run.id] });
      qc.invalidateQueries({ queryKey: ['runs'] });
      nav(`/runs/${run.id}/diagnosis`);
    },
  });
  const mapped = run.files.reduce((n, f) => n + f.headers.filter((h) => h.logical).length, 0);
  const total = run.files.reduce((n, f) => n + f.headers.length, 0);

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
          <Text>
            매핑된 컬럼 <b>{mapped}</b> / {total}
          </Text>
        </Group>
        <Button rightSection={<IconArrowRight size={16} />} loading={diagnose.isPending} onClick={() => diagnose.mutate()}>
          {run.status === 'uploaded' ? '진단 실행' : '다시 진단'}
        </Button>
      </Group>
      {diagnose.error && <Alert color="red">{(diagnose.error as Error).message}</Alert>}
      <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>
        {run.files.map((f) => (
          <Card key={f.item_id} withBorder>
            <Group justify="space-between" mb="xs">
              <Text fw={600}>{f.name}</Text>
              <Badge variant="light" tt="none" ff="monospace" color={f.read_error ? 'red' : f.known_table ? 'teal' : 'gray'}>
                {f.table}
              </Badge>
            </Group>
            {f.read_error ? (
              <Alert color="red" p="xs">
                {f.read_error}
              </Alert>
            ) : (
              <>
                <Text fz="sm" c="dimmed" mb="xs">
                  {fmt(f.rows)}행 · {f.columns}열
                </Text>
                <Table fz="sm" verticalSpacing={4}>
                  <Table.Tbody>
                    {f.headers.map((h) => (
                      <Table.Tr key={h.original}>
                        <Table.Td style={{ whiteSpace: 'nowrap' }}>{h.original}</Table.Td>
                        <Table.Td c="dimmed" w={20}>
                          →
                        </Table.Td>
                        <Table.Td ff="monospace" style={{ wordBreak: 'break-all' }}>{h.logical ?? '—'}</Table.Td>
                        <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>
                          <Text fz="xs" fw={600} c={h.basis ? `${BASIS[h.basis].color}.7` : 'dimmed'}>
                            {h.basis ? BASIS[h.basis].label : '미매핑'}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </>
            )}
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  );
}
