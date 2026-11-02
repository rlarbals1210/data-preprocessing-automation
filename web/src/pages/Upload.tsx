import { ActionIcon, Alert, Button, Card, Group, SegmentedControl, Stack, Table, Text, Title } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { IconFileTypeCsv, IconUpload, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { fmt } from '../labels';

export default function Upload() {
  const [files, setFiles] = useState<File[]>([]);
  const [set, setSet] = useState('shop');
  const sets = useQuery({ queryKey: ['sets'], queryFn: api.sets });
  const qc = useQueryClient();
  const nav = useNavigate();
  const create = useMutation({
    mutationFn: () => api.createRun(files, set),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ['runs'] });
      nav(`/runs/${run.id}/mapping`);
    },
  });
  const add = (incoming: File[]) =>
    setFiles((prev) => {
      const byName = new Map(prev.map((f) => [f.name, f]));
      incoming.forEach((f) => byName.set(f.name, f));
      return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
    });

  return (
    <Stack maw={880}>
      <Title order={3}>새 업로드</Title>
      <Card withBorder>
        <Stack>
          <Group>
            <Text fw={600} w={64}>
              설정
            </Text>
            <SegmentedControl
              value={set}
              onChange={setSet}
              data={(sets.data ?? [{ id: 'shop', label: '쇼핑몰 표준' }]).map((s) => ({ value: s.id, label: s.label }))}
            />
          </Group>
          <Dropzone onDrop={add} accept={{ 'text/csv': ['.csv'] }} multiple>
            <Stack align="center" gap={6} py="xl" style={{ pointerEvents: 'none' }}>
              <Dropzone.Accept>
                <IconUpload size={40} />
              </Dropzone.Accept>
              <Dropzone.Reject>
                <IconX size={40} />
              </Dropzone.Reject>
              <Dropzone.Idle>
                <IconFileTypeCsv size={40} stroke={1.4} />
              </Dropzone.Idle>
              <Text fw={600}>CSV</Text>
            </Stack>
          </Dropzone>
          {files.length > 0 && (
            <Table>
              <Table.Tbody>
                {files.map((f) => (
                  <Table.Tr key={f.name}>
                    <Table.Td>{f.name}</Table.Td>
                    <Table.Td ta="right" c="dimmed">
                      {fmt(Math.ceil(f.size / 1024))} KB
                    </Table.Td>
                    <Table.Td w={40}>
                      <ActionIcon variant="subtle" color="gray" onClick={() => setFiles(files.filter((x) => x !== f))}>
                        <IconX size={16} />
                      </ActionIcon>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          {create.error && <Alert color="red">{(create.error as Error).message}</Alert>}
          <Group justify="flex-end">
            <Button disabled={!files.length} loading={create.isPending} onClick={() => create.mutate()}>
              업로드 ({files.length})
            </Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
