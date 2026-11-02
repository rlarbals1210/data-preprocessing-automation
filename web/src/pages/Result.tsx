import { Alert, Anchor, Badge, Card, Code, Group, Loader, Pagination, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { IconArrowRight, IconDownload } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Run } from '../api';
import { CATEGORY, CATEGORY_ORDER, CHANGE, CODE, RESULT_STATUS, fmt, where } from '../labels';

const PAGE = 25;

export default function Result({ run }: { run: Run }) {
  const r = useQuery({ queryKey: ['result', run.id], queryFn: () => api.result(run.id) });
  const [page, setPage] = useState(1);
  const changes = useQuery({
    queryKey: ['changes', run.id, page],
    queryFn: () => api.changes(run.id, (page - 1) * PAGE, PAGE),
    enabled: r.data?.status !== 'failed' && !!r.data,
  });
  if (r.isLoading) return <Loader />;
  if (r.error || !r.data) return <Alert color="red">{(r.error as Error)?.message}</Alert>;
  const x = r.data;

  return (
    <Stack>
      <Group>
        <Badge size="lg" variant="light" color={RESULT_STATUS[x.status]?.color}>
          {RESULT_STATUS[x.status]?.label}
        </Badge>
      </Group>
      {x.status === 'failed' ? (
        <Alert color="red">{x.error}</Alert>
      ) : (
        <>
          <SimpleGrid cols={{ base: 2, md: 4 }}>
            <Card withBorder>
              <Text fz="xs" c="dimmed" fw={600}>
                변경 셀
              </Text>
              <Text fz={26} fw={700} c="teal.7">
                {fmt(x.changed_cells)}
              </Text>
              {!!x.not_approved && (
                <Text fz="xs" c="dimmed">
                  승인 안 함 {fmt(x.not_approved)}
                </Text>
              )}
            </Card>
            <Card withBorder>
              <Text fz="xs" c="dimmed" fw={600}>
                행
              </Text>
              <Text fz={26} fw={700}>
                {fmt(x.rows)}
              </Text>
            </Card>
            <Card withBorder>
              <Text fz="xs" c="dimmed" fw={600}>
                남은 업무 규칙 위반
              </Text>
              <Text fz={26} fw={700} c="red.7">
                {fmt(x.after?.business_rule_violation)}
              </Text>
            </Card>
            <Card withBorder>
              <Text fz="xs" c="dimmed" fw={600}>
                남은 사람 검토
              </Text>
              <Text fz={26} fw={700} c="orange.7">
                {fmt(x.after?.human_review)}
              </Text>
            </Card>
          </SimpleGrid>

          <SimpleGrid cols={{ base: 1, md: 2 }}>
            <Card withBorder>
              <Title order={5} mb="xs">
                정제 전후
              </Title>
              <Table fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>분류</Table.Th>
                    <Table.Th ta="right">전</Table.Th>
                    <Table.Th ta="right">후</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {CATEGORY_ORDER.map((c) => (
                    <Table.Tr key={c}>
                      <Table.Td>
                        <Badge variant="light" color={CATEGORY[c].color}>
                          {CATEGORY[c].label}
                        </Badge>
                      </Table.Td>
                      <Table.Td ta="right">{fmt(x.before?.[c])}</Table.Td>
                      <Table.Td ta="right" fw={600}>
                        {fmt(x.after?.[c])}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Card>
            <Card withBorder>
              <Title order={5} mb="xs">
                다운로드
              </Title>
              <Group gap="xs">
                <Badge color="gray" variant="outline">
                  내부용
                </Badge>
              </Group>
              <Stack gap={4} mt="sm">
                {[...x.files, { name: 'changes.csv', bytes: 0 }].map((f) => (
                  <Anchor key={f.name} href={api.fileUrl(run.id, f.name)} fz="sm">
                    <Group gap={6}>
                      <IconDownload size={14} />
                      {f.name}
                      {f.bytes > 0 && (
                        <Text span c="dimmed" fz="xs">
                          {fmt(Math.ceil(f.bytes / 1024))} KB
                        </Text>
                      )}
                    </Group>
                  </Anchor>
                ))}
              </Stack>
            </Card>
          </SimpleGrid>

          <Card withBorder>
            <Title order={5} mb="sm">
              변경 내역 {changes.data && <Text span c="dimmed">{fmt(changes.data.total)}</Text>}
            </Title>
            <Table fz="sm" verticalSpacing={6} highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>테이블</Table.Th>
                  <Table.Th>원본 위치</Table.Th>
                  <Table.Th>컬럼</Table.Th>
                  <Table.Th>변경</Table.Th>
                  <Table.Th>규칙</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {changes.data?.items.map((c, k) => (
                  <Table.Tr key={k}>
                    <Table.Td ff="monospace">{c.table}</Table.Td>
                    <Table.Td c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                      {where(c.source)}
                    </Table.Td>
                    <Table.Td ff="monospace">{c.column}</Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Code>{c.before || '∅'}</Code>
                        <IconArrowRight size={12} />
                        <Code color="teal.1">{c.after || '∅'}</Code>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {c.codes.map((code) => (
                        <Badge key={code} variant="light" tt="none" mr={4}>
                          {CHANGE[code] ?? CODE[code] ?? code}
                        </Badge>
                      ))}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {(changes.data?.total ?? 0) > PAGE && (
              <Group justify="center" mt="md">
                <Pagination total={Math.ceil((changes.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} size="sm" />
              </Group>
            )}
          </Card>
        </>
      )}
    </Stack>
  );
}
