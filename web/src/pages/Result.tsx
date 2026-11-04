import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Pagination,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowRight, IconDownload, IconRefresh } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { api, type Run } from '../api';
import { SourceButton } from '../components/SourceRow';
import { CATEGORY, CATEGORY_ORDER, CHANGE, CODE, PUBLISH_REASON, RESULT_STATUS, fmt, where } from '../labels';

const PAGE = 25;
const EVIDENCE: Record<string, string> = {
  'changes.csv': '변경 내역 (CSV)',
  'changes.json': '변경 내역 (JSON, 논리 null 구분)',
  'diagnosis-before.json': '정제 전 진단',
  'diagnosis-after.json': '정제 후 진단',
  'integrity.json': '정합성 재검사',
  'held.json': '보류 목록',
  'absent-cells.json': '원래 없던 셀',
  'manifest.json': 'manifest',
};

function Remaining({ run, counts }: { run: Run; counts?: Record<string, number> }) {
  const [category, setCategory] = useState<'business_rule_violation' | 'human_review'>(
    counts?.business_rule_violation ? 'business_rule_violation' : 'human_review',
  );
  const [page, setPage] = useState(1);
  const r = useQuery({
    queryKey: ['remaining', run.id, category, page],
    queryFn: () => api.remaining(run.id, { category, offset: (page - 1) * PAGE, limit: PAGE }),
  });
  return (
    <Card withBorder>
      <Group justify="space-between" mb="sm">
        <Title order={5}>남은 문제</Title>
        <SegmentedControl
          value={category}
          onChange={(v) => {
            setCategory(v as typeof category);
            setPage(1);
          }}
          data={(['business_rule_violation', 'human_review'] as const).map((c) => ({
            value: c,
            label: `${CATEGORY[c].label} ${fmt(counts?.[c])}`,
          }))}
        />
      </Group>
      <Table fz="sm" verticalSpacing={6} data-testid="remaining">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>테이블</Table.Th>
            <Table.Th>원본 위치</Table.Th>
            <Table.Th>컬럼</Table.Th>
            <Table.Th>값</Table.Th>
            <Table.Th>규칙</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {r.data?.items.map((i, k) => (
            <Table.Tr key={k}>
              <Table.Td ff="monospace">{i.table}</Table.Td>
              <Table.Td c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                <Group gap={4} wrap="nowrap">
                  {where(i.source)}
                  <SourceButton
                    runId={run.id}
                    source={i.source}
                    highlight={i.columns.map((c) => i.source?.columns[c] ?? '').filter(Boolean)}
                  />
                </Group>
              </Table.Td>
              <Table.Td ff="monospace">{i.columns.length > 3 ? `${i.columns.length}개 컬럼` : i.columns.join(', ')}</Table.Td>
              <Table.Td>{i.columns.length === 1 ? <Code>{i.values[i.columns[0]] || '∅'}</Code> : '—'}</Table.Td>
              <Table.Td>
                <Badge variant="light" color={CATEGORY[i.category].color}>
                  {CODE[i.code] ?? i.code}
                </Badge>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {(r.data?.total ?? 0) > PAGE && (
        <Group justify="center" mt="md">
          <Pagination total={Math.ceil((r.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} size="sm" />
        </Group>
      )}
    </Card>
  );
}

export default function Result({ run }: { run: Run }) {
  const r = useQuery({ queryKey: ['result', run.id], queryFn: () => api.result(run.id) });
  const [page, setPage] = useState(1);
  const qc = useQueryClient();
  const nav = useNavigate();
  const retry = useMutation({
    mutationFn: () => api.retry(run.id),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['runs'] });
      qc.invalidateQueries({ queryKey: ['run', run.id] });
      nav(`/runs/${r.id}/mapping`);
    },
  });
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
        {x.result_version && (
          <Badge size="lg" variant="outline" color="teal">
            결과 버전 {x.result_version}
          </Badge>
        )}
        {x.attempt && (
          <Badge size="lg" variant="outline" color="gray">
            시도 {x.attempt}
          </Badge>
        )}
        {x.result_version && (
          <Anchor component={Link} to="/history" fz="sm">
            실행 이력
          </Anchor>
        )}
        {x.audit?.passed && (
          <Badge size="lg" variant="light" color="teal">
            전체 셀 대조 통과 · {fmt(x.audit.checked_cells)}셀
          </Badge>
        )}
      </Group>
      {x.status === 'failed' ? (
        <Stack>
          <Alert color="red">{x.error}</Alert>
          <Anchor href={api.failureReportUrl(run.id)} fz="sm">
            <Group gap={6}>
              <IconDownload size={14} />
              실패 진단 리포트
            </Group>
          </Anchor>
          <Group>
            {run.retried_by ? (
              <Button component={Link} to={`/runs/${run.retried_by}`} variant="light" rightSection={<IconArrowRight size={16} />}>
                다시 시도한 실행
              </Button>
            ) : (
              <Button leftSection={<IconRefresh size={16} />} loading={retry.isPending} onClick={() => retry.mutate()}>
                다시 시도
              </Button>
            )}
          </Group>
          {retry.error && <Alert color="red">{(retry.error as Error).message}</Alert>}
        </Stack>
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
                  원문 포함 — 내부 검토용
                </Badge>
              </Group>
              <Stack gap={4} mt="sm">
                {x.files.map((f) => (
                  <Anchor key={f.name} href={api.fileUrl(run.id, f.name)} fz="sm">
                    <Group gap={6}>
                      <IconDownload size={14} />
                      {f.name}
                      {f.rows >= 0 && (
                        <Text span c="dimmed" fz="xs">
                          {fmt(f.rows)}행
                        </Text>
                      )}
                    </Group>
                  </Anchor>
                ))}
                {x.evidence.map((name) => (
                  <Anchor key={name} href={api.fileUrl(run.id, name)} fz="sm" c="dimmed">
                    <Group gap={6}>
                      <IconDownload size={14} />
                      {EVIDENCE[name] ?? name}
                    </Group>
                  </Anchor>
                ))}
                {Object.entries(x.excluded_tables ?? {}).map(([t, reason]) => (
                  <Text key={t} fz="sm" c="dimmed">
                    <Text span ff="monospace">
                      {t}
                    </Text>{' '}
                    발행 제외 · {PUBLISH_REASON[reason] ?? reason}
                  </Text>
                ))}
              </Stack>
            </Card>
          </SimpleGrid>

          {!!x.integrity_after && (
            <Alert color="orange" data-testid="integrity-after">
              <Group justify="space-between">
                <Text fz="sm">
                  정합성 위반 묶음 <b>{fmt(x.integrity_after)}</b>이 결과에 남아 있습니다(진단 때 확인한 위반, 자동 수정 안 함).
                </Text>
                <Anchor component={Link} to={`/runs/${run.id}/integrity`} fz="sm">
                  정합성 검토
                </Anchor>
              </Group>
            </Alert>
          )}
          {x.status === 'completed_with_issues' && <Remaining run={run} counts={x.after} />}

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
