import { Badge, Button, Card, Group, Loader, Table, Text, Title } from '@mantine/core';
import { IconUpload } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { RESULT_STATUS, RUN_STATUS, SET_LABEL, fmt, when } from '../labels';

export default function Home() {
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs });
  const nav = useNavigate();
  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={3}>실행</Title>
        <Button component={Link} to="/upload" leftSection={<IconUpload size={16} />}>
          새 업로드
        </Button>
      </Group>
      <Card withBorder p={0}>
        {runs.isLoading ? (
          <Group p="xl" justify="center">
            <Loader size="sm" />
          </Group>
        ) : !runs.data?.length ? (
          <Text c="dimmed" p="xl" ta="center">
            —
          </Text>
        ) : (
          <Table highlightOnHover verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>실행</Table.Th>
                <Table.Th>시각</Table.Th>
                <Table.Th>출처</Table.Th>
                <Table.Th>설정</Table.Th>
                <Table.Th ta="right">파일</Table.Th>
                <Table.Th ta="right">행</Table.Th>
                <Table.Th>상태</Table.Th>
                <Table.Th>결과</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {runs.data.map((r) => (
                <Table.Tr key={r.id} style={{ cursor: 'pointer' }} onClick={() => nav(`/runs/${r.id}`)}>
                  <Table.Td ff="monospace" fz="sm">
                    {r.id}
                  </Table.Td>
                  <Table.Td>{when(r.created_at)}</Table.Td>
                  <Table.Td>{r.source}</Table.Td>
                  <Table.Td>{SET_LABEL[r.set] ?? r.set}</Table.Td>
                  <Table.Td ta="right">{fmt(r.file_count)}</Table.Td>
                  <Table.Td ta="right">{fmt(r.rows)}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={RUN_STATUS[r.status]?.color}>
                      {RUN_STATUS[r.status]?.label ?? r.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {r.result && (
                      <Badge variant="dot" color={RESULT_STATUS[r.result.status]?.color}>
                        {RESULT_STATUS[r.result.status]?.label}
                      </Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
