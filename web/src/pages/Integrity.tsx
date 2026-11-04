import { Alert, Badge, Card, Code, Group, Loader, Pagination, SegmentedControl, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { FINISHED, api, type IntegrityBasis, type Run } from '../api';
import { SourceButton } from '../components/SourceRow';
import { INTEGRITY_BASIS, INTEGRITY_REASON, fmt, where } from '../labels';

const PAGE = 15;
const short = (id: string) => id.split('_')[0];

export default function Integrity({ run }: { run: Run }) {
  const published = FINISHED.includes(run.status) && run.status !== 'failed';
  const [basis, setBasis] = useState<IntegrityBasis>(published ? 'result' : 'virtual');
  const [page, setPage] = useState(1);
  const r = useQuery({
    queryKey: ['integrity', run.id, basis, page],
    queryFn: () => api.integrity(run.id, basis, (page - 1) * PAGE, PAGE),
  });
  const bases: IntegrityBasis[] = published ? ['raw', 'virtual', 'result'] : ['raw', 'virtual', 'approved'];

  return (
    <Stack>
      <Group justify="space-between">
        <SegmentedControl
          value={basis}
          onChange={(v) => {
            setBasis(v as IntegrityBasis);
            setPage(1);
          }}
          data={bases.map((b) => ({ value: b, label: INTEGRITY_BASIS[b] }))}
        />
        {r.data && (
          <Text>
            위반 묶음 <b data-testid="integrity-total">{fmt(r.data.total)}</b>
          </Text>
        )}
      </Group>
      {r.isLoading && <Loader />}
      {r.error && <Alert color="red">{(r.error as Error).message}</Alert>}
      {r.data && (
        <>
          <Card withBorder p={0}>
            <Table fz="sm" verticalSpacing={6} data-testid="integrity-rules">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>규칙</Table.Th>
                  <Table.Th>설명</Table.Th>
                  <Table.Th>기준</Table.Th>
                  <Table.Th ta="right">정상</Table.Th>
                  <Table.Th ta="right">위반</Table.Th>
                  <Table.Th ta="right">미적용</Table.Th>
                  <Table.Th>검사 불가</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.data.rules.map((rule) => (
                  <Table.Tr key={rule.id}>
                    <Table.Td>
                      <Tooltip label={rule.id}>
                        <Badge variant="outline" color="gray">
                          {short(rule.id)}
                        </Badge>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td maw={420}>{rule.description}</Table.Td>
                    <Table.Td ff="monospace">{rule.anchor}</Table.Td>
                    {rule.status === 'ran' ? (
                      <>
                        <Table.Td ta="right">{fmt(rule.counts.ok)}</Table.Td>
                        <Table.Td ta="right" c={rule.counts.violation ? 'red.7' : 'dimmed'} fw={rule.counts.violation ? 700 : 400}>
                          {fmt(rule.counts.violation)}
                        </Table.Td>
                        <Table.Td ta="right" c="dimmed">
                          {fmt(rule.counts.not_applicable)}
                        </Table.Td>
                        <Table.Td>
                          <Group gap={4}>
                            {Object.entries(rule.counts.not_computable).map(([k, n]) => (
                              <Badge key={k} size="sm" variant="light" color="gray">
                                {INTEGRITY_REASON[k] ?? k} {fmt(n)}
                              </Badge>
                            ))}
                          </Group>
                        </Table.Td>
                      </>
                    ) : (
                      <Table.Td colSpan={4}>
                        <Badge variant="light" color={rule.status === 'disabled' ? 'gray' : 'orange'}>
                          {rule.status === 'disabled' ? '꺼짐' : `미실행 · ${INTEGRITY_REASON[rule.reason ?? ''] ?? rule.reason}`}
                        </Badge>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Card>

          <Card withBorder>
            <Title order={5} mb="sm">
              위반 묶음
            </Title>
            {r.data.total === 0 ? (
              <Text c="dimmed" fz="sm">
                없음
              </Text>
            ) : (
              <Table fz="sm" verticalSpacing={6} data-testid="integrity-violations">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>규칙</Table.Th>
                    <Table.Th>계산값</Table.Th>
                    <Table.Th>기대값</Table.Th>
                    <Table.Th>관련 행</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {r.data.items.map((v, k) => (
                    <Table.Tr key={k}>
                      <Table.Td>
                        <Tooltip label={v.rule}>
                          <Badge color="red" variant="light">
                            {short(v.rule)}
                          </Badge>
                        </Tooltip>
                      </Table.Td>
                      <Table.Td>
                        <Code>{v.left === null ? '—' : String(v.left)}</Code>
                      </Table.Td>
                      <Table.Td>
                        <Code>{v.right === null ? '—' : String(v.right)}</Code>
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={2}>
                          {v.sources.map((s, i) => (
                            <Group key={i} gap={4} wrap="nowrap" c="dimmed">
                              <Text span ff="monospace" fz="xs" c={s.table === v.table ? 'dark' : 'dimmed'}>
                                {s.table}
                              </Text>
                              {where(s)}
                              <SourceButton runId={run.id} source={s} />
                            </Group>
                          ))}
                        </Stack>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
            {r.data.total > PAGE && (
              <Group justify="center" mt="md">
                <Pagination size="sm" total={Math.ceil(r.data.total / PAGE)} value={page} onChange={setPage} />
              </Group>
            )}
          </Card>
        </>
      )}
    </Stack>
  );
}
