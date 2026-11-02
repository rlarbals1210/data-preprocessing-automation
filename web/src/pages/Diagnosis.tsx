import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Pagination,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type Category, type Run, type SkippedRule } from '../api';
import { CATEGORY, CATEGORY_ORDER, CODE, PUBLISH_REASON, RULE_KIND, SKIP_REASON, fmt } from '../labels';

const PAGE = 25;

type Group = { table: string; columns: string[]; target?: string; reason: string; kinds: string[] };
function groupSkipped(rules: SkippedRule[]): Group[] {
  const map = new Map<string, Group>();
  rules.forEach((r) => {
    const key = `${r.table}|${r.columns.join(',')}|${r.target ?? ''}|${r.reason}`;
    const g = map.get(key) ?? { table: r.table, columns: r.columns, target: r.target, reason: r.reason, kinds: [] };
    g.kinds.push(r.kind);
    map.set(key, g);
  });
  return [...map.values()];
}

function Stat({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <Card withBorder padding="md">
      <Text fz="xs" c="dimmed" fw={600}>
        {label}
      </Text>
      <Text fz={26} fw={700} c={color}>
        {typeof value === 'number' ? fmt(value) : value}
      </Text>
    </Card>
  );
}

export default function Diagnosis({ run }: { run: Run }) {
  const d = useQuery({ queryKey: ['diagnosis', run.id], queryFn: () => api.diagnosis(run.id) });
  const [category, setCategory] = useState<Category>('business_rule_violation');
  const [table, setTable] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const issues = useQuery({
    queryKey: ['issues', run.id, category, table, page],
    queryFn: () => api.issues(run.id, { category, table, offset: (page - 1) * PAGE, limit: PAGE }),
    enabled: !!d.data,
  });
  if (d.isLoading) return <Loader />;
  if (d.error || !d.data) return <Alert color="red">{(d.error as Error)?.message}</Alert>;
  const x = d.data;

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="sm">
          <Badge size="lg" variant="light" color={x.completeness === 'full' ? 'teal' : 'orange'}>
            {x.completeness === 'full' ? '전체 검사' : '부분 검사'}
          </Badge>
          <Badge size="lg" variant="outline" color="gray">
            {x.mode === 'configured' ? '규칙 검사' : '프로파일만'}
          </Badge>
        </Group>
        <Button component={Link} to={`/runs/${run.id}/preview`} rightSection={<IconArrowRight size={16} />}>
          변경 미리보기
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 2, md: 4 }}>
        {CATEGORY_ORDER.map((c) => (
          <Stat key={c} label={CATEGORY[c].label} value={x.category_counts[c]} color={`${CATEGORY[c].color}.7`} />
        ))}
      </SimpleGrid>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <Stat label="행" value={x.rows_read} />
        <Stat label="테이블" value={x.tables_read} />
        <Stat label="중복 그룹" value={x.duplicate_groups} />
        <Stat label="실행 규칙" value={`${fmt(x.rules.executed)} / ${fmt(x.rules.executed + x.rules.not_executed)}`} />
      </SimpleGrid>

      {(x.skipped_rules.length > 0 || x.errors.length > 0) && (
        <Card withBorder>
          <Title order={5} mb="xs">
            실행하지 않은 규칙
          </Title>
          <Table fz="sm">
            <Table.Tbody>
              {x.errors.map((e, i) => (
                <Table.Tr key={`e${i}`}>
                  <Table.Td>{e.table}</Table.Td>
                  <Table.Td colSpan={3}>{e.error}</Table.Td>
                </Table.Tr>
              ))}
              {groupSkipped(x.skipped_rules).map((g, i) => (
                <Table.Tr key={i}>
                  <Table.Td ff="monospace">{g.table}</Table.Td>
                  <Table.Td ff="monospace">
                    {g.columns.join(', ')}
                    {g.target ? ` → ${g.target}` : ''}
                  </Table.Td>
                  <Table.Td>{g.kinds.map((k) => RULE_KIND[k] ?? k).join(' · ')}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="gray">
                      {SKIP_REASON[g.reason] ?? g.reason}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      <Card withBorder>
        <Title order={5} mb="sm">
          테이블별
        </Title>
        <Table fz="sm" striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>테이블</Table.Th>
              <Table.Th ta="right">행</Table.Th>
              <Table.Th>발행</Table.Th>
              {CATEGORY_ORDER.map((c) => (
                <Table.Th key={c} ta="right">
                  {CATEGORY[c].label}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {x.tables.map((t) => (
              <Table.Tr key={t.table}>
                <Table.Td ff="monospace">{t.table}</Table.Td>
                <Table.Td ta="right">{fmt(t.rows)}</Table.Td>
                <Table.Td>
                  {x.plan_tables[t.table] &&
                    (x.plan_tables[t.table].publishable ? (
                      <Badge size="sm" variant="light" color="teal">
                        가능
                      </Badge>
                    ) : (
                      <Badge size="sm" variant="light" color="red">
                        제외 · {PUBLISH_REASON[x.plan_tables[t.table].reason ?? ''] ?? x.plan_tables[t.table].reason}
                      </Badge>
                    ))}
                </Table.Td>
                {CATEGORY_ORDER.map((c) => (
                  <Table.Td key={c} ta="right" c={t.counts[c] ? `${CATEGORY[c].color}.7` : 'dimmed'}>
                    {fmt(t.counts[c])}
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>

      <Card withBorder>
        <Group justify="space-between" mb="sm">
          <SegmentedControl
            value={category}
            onChange={(v) => {
              setCategory(v as Category);
              setPage(1);
            }}
            data={CATEGORY_ORDER.map((c) => ({ value: c, label: `${CATEGORY[c].label} ${fmt(x.category_counts[c])}` }))}
          />
          <Select
            placeholder="전체 테이블"
            clearable
            value={table}
            onChange={(v) => {
              setTable(v);
              setPage(1);
            }}
            data={x.tables.map((t) => t.table)}
            w={220}
          />
        </Group>
        {issues.isLoading ? (
          <Loader size="sm" />
        ) : (
          <>
            <Table fz="sm" verticalSpacing={6} highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>테이블</Table.Th>
                  <Table.Th ta="right">레코드</Table.Th>
                  <Table.Th>컬럼</Table.Th>
                  <Table.Th>값</Table.Th>
                  <Table.Th>규칙</Table.Th>
                  <Table.Th>제안</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {issues.data?.items.map((i, k) => (
                  <Table.Tr key={k}>
                    <Table.Td ff="monospace">{i.table}</Table.Td>
                    <Table.Td ta="right">{i.record}</Table.Td>
                    <Table.Td ff="monospace">{i.columns.length > 3 ? `${i.columns.length}개 컬럼` : i.columns.join(', ')}</Table.Td>
                    <Table.Td maw={260} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {i.columns.length === 1 ? <Code>{i.values[i.columns[0]] || '∅'}</Code> : '—'}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={CATEGORY[i.category].color}>
                        {CODE[i.code] ?? i.code}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {i.category === 'standardization_candidate' && (
                        <Code color="teal.1">{i.suggested_value ?? '∅'}</Code>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {(issues.data?.total ?? 0) > PAGE && (
              <Group justify="center" mt="md">
                <Pagination total={Math.ceil((issues.data?.total ?? 0) / PAGE)} value={page} onChange={setPage} size="sm" />
              </Group>
            )}
          </>
        )}
      </Card>
    </Stack>
  );
}
