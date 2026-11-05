import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  Loader,
  Modal,
  Pagination,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconArrowRight, IconCheck, IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FINISHED, api, type Cell, type PreviewData, type PreviewGroup, type Run } from '../api';
import { CATEGORY, CHANGE, CODE, PUBLISH_REASON, fmt, where } from '../labels';

const CELL_PAGE = 20;
const key = (c: Cell) => c.join('\u0000');

function GroupCells({
  run,
  group,
  data,
  disabled,
  onToggle,
}: {
  run: Run;
  group: PreviewGroup;
  data: PreviewData;
  disabled: boolean;
  onToggle: (cell: Cell, include: boolean) => void;
}) {
  const [page, setPage] = useState(1);
  const cells = useQuery({
    queryKey: ['preview-cells', run.id, group.id, page, data.plan_hash],
    queryFn: () => api.previewCells(run.id, group.id, (page - 1) * CELL_PAGE, CELL_PAGE),
  });
  if (cells.isLoading) return <Loader size="sm" m="sm" />;
  if (cells.error || !cells.data) return <Alert color="red">{(cells.error as Error)?.message}</Alert>;
  const excluded = new Set(data.excluded_cells.map(key));
  return (
    <Stack gap="xs" p="sm" bg="gray.0">
      <Table fz="sm" verticalSpacing={4}>
        <Table.Tbody>
          {cells.data.items.map((c) => (
            <Table.Tr key={key(c.cell)} data-testid="group-cell">
              <Table.Td w={40}>
                <Checkbox
                  size="xs"
                  aria-label={`${c.cell[1]}번째 레코드`}
                  checked={!c.held && group.approved && !excluded.has(key(c.cell))}
                  disabled={disabled || c.held || !group.approved}
                  onChange={(e) => onToggle(c.cell, e.currentTarget.checked)}
                />
              </Table.Td>
              <Table.Td c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                {where(c.source)}
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <Code>{c.before || '∅'}</Code>
                  <IconArrowRight size={12} />
                  <Code color="teal.1">{c.null ? 'null' : c.after || '∅'}</Code>
                </Group>
              </Table.Td>
              <Table.Td>
                {c.reuse && !c.held && (
                  <Badge size="sm" color="grape" variant="light" data-testid="cell-reuse">
                    재사용 규칙
                  </Badge>
                )}
                {c.held && (
                  <Group gap={4}>
                    <Badge size="sm" color="orange" variant="light">
                      보류
                    </Badge>
                    {c.reasons.map((r, i) => (
                      <Badge key={i} size="sm" variant="outline" color={CATEGORY[r.category]?.color}>
                        {CATEGORY[r.category]?.label} · {CODE[r.code] ?? r.code}
                      </Badge>
                    ))}
                  </Group>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {cells.data.total > CELL_PAGE && (
        <Pagination size="xs" total={Math.ceil(cells.data.total / CELL_PAGE)} value={page} onChange={setPage} />
      )}
    </Stack>
  );
}

export default function Preview({ run }: { run: Run }) {
  const p = useQuery({ queryKey: ['preview', run.id], queryFn: () => api.preview(run.id) });
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [reuseGroups, setReuseGroups] = useState<string[]>([]);
  const [reuseMappings, setReuseMappings] = useState<number[]>([]);
  const qc = useQueryClient();
  const nav = useNavigate();
  useEffect(() => {
    if (p.data && run.status === 'diagnosed') qc.invalidateQueries({ queryKey: ['run', run.id] });
  }, [p.data, run.status, run.id, qc]);
  const select = useMutation({
    mutationFn: (s: { groups: string[]; cells: Cell[] }) => api.select(run.id, s.groups, s.cells),
    onSuccess: (data) => qc.setQueryData(['preview', run.id], data),
  });
  const execute = useMutation({
    mutationFn: () =>
      api.execute(run.id, p.data!.approval, {
        groups: reuseGroups.filter((g) => p.data!.groups.some((x) => x.id === g && x.approved && x.cells > 0)),
        mappings: reuseMappings,
      }),
    onSuccess: () => {
      setOpen(false);
      ['run', 'result', 'changes'].forEach((k) => qc.invalidateQueries({ queryKey: [k, run.id] }));
      qc.invalidateQueries({ queryKey: ['runs'] });
      nav(`/runs/${run.id}/result`);
    },
    onError: () => qc.invalidateQueries({ queryKey: ['preview', run.id] }),
  });
  if (p.isLoading) return <Loader />;
  if (p.error || !p.data) return <Alert color="red">{(p.error as Error)?.message}</Alert>;
  const x = p.data;
  const selectable = x.groups.filter((g) => g.cells > 0);
  const approved = selectable.filter((g) => g.approved).map((g) => g.id);
  const setGroups = (groups: string[]) => select.mutate({ groups, cells: x.excluded_cells });
  const toggle = (id: string, on: boolean) => setGroups(on ? [...approved, id] : approved.filter((a) => a !== id));
  const toggleCell = (cell: Cell, include: boolean) =>
    select.mutate({
      groups: approved,
      cells: include ? x.excluded_cells.filter((c) => key(c) !== key(cell)) : [...x.excluded_cells, cell],
    });
  const allOn = approved.length === selectable.length;
  const finished = FINISHED.includes(run.status);
  const blocked = Object.entries(x.not_publishable ?? {});
  const targets = run.files.filter((f) => !f.read_error && f.mapping?.table);
  const flip = <T,>(xs: T[], v: T, on: boolean) => (on ? [...xs.filter((a) => a !== v), v] : xs.filter((a) => a !== v));
  const reuseCount = reuseGroups.filter((g) => approved.includes(g)).length + reuseMappings.length;

  return (
    <Stack>
      <Group justify="space-between">
        <Group gap="lg">
          <Text>
            규칙 묶음 <b>{approved.length}</b> / {selectable.length}
          </Text>
          <Text>
            변경 셀 <b data-testid="total-cells">{fmt(x.total_cells)}</b> / {fmt(x.candidates)}
          </Text>
          {x.held > 0 && (
            <Text c="orange.7">
              보류 <b>{fmt(x.held)}</b>
            </Text>
          )}
          {select.isPending && <Loader size="xs" />}
        </Group>
        <Button
          leftSection={<IconCheck size={16} />}
          disabled={finished || select.isPending}
          onClick={() => setOpen(true)}
        >
          최종 확인
        </Button>
      </Group>
      {blocked.length > 0 && (
        <Alert color="orange" title="발행 제외 테이블">
          {blocked.map(([t, reason]) => (
            <Text key={t} fz="sm">
              <Text span ff="monospace">
                {t}
              </Text>{' '}
              · {PUBLISH_REASON[reason] ?? reason}
            </Text>
          ))}
        </Alert>
      )}
      {select.error && <Alert color="red">{(select.error as Error).message}</Alert>}
      <Card withBorder p={0}>
        <Table.ScrollContainer minWidth={960} type="native">
        <Table verticalSpacing="sm" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={40}>
                <Checkbox
                  disabled={finished}
                  checked={allOn}
                  indeterminate={!allOn && approved.length > 0}
                  onChange={() => setGroups(allOn ? [] : selectable.map((g) => g.id))}
                  aria-label="전체"
                />
              </Table.Th>
              <Table.Th w={36} />
              <Table.Th>테이블</Table.Th>
              <Table.Th>컬럼</Table.Th>
              <Table.Th>규칙</Table.Th>
              <Table.Th ta="right">셀</Table.Th>
              <Table.Th>예시</Table.Th>
              <Table.Th ta="center">재사용</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {x.groups.map((g) => (
              <Fragment key={g.id}>
                <Table.Tr style={{ opacity: g.approved || !g.cells ? 1 : 0.5 }} data-testid="group-row">
                  <Table.Td>
                    <Checkbox
                      aria-label={g.id}
                      checked={g.approved}
                      disabled={finished || !g.cells}
                      onChange={(e) => toggle(g.id, e.currentTarget.checked)}
                    />
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      variant="subtle"
                      color="gray"
                      aria-label={`${g.id} 셀 보기`}
                      onClick={() => setExpanded(expanded === g.id ? null : g.id)}
                    >
                      {expanded === g.id ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                    </ActionIcon>
                  </Table.Td>
                  <Table.Td ff="monospace">{g.table}</Table.Td>
                  <Table.Td ff="monospace">{g.column}</Table.Td>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    <Badge variant="light" styles={{ root: { overflow: 'visible' }, label: { overflow: 'visible' } }}>
                      {CHANGE[g.code] ?? CODE[g.code] ?? g.code}
                    </Badge>
                    {g.reuse > 0 && (
                      <Badge ml={6} color="grape" variant="light" data-testid="group-reuse">
                        재사용 규칙 {fmt(g.reuse)}
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td ta="right" style={{ whiteSpace: 'nowrap' }}>
                    {fmt(g.cells - (g.excluded ?? 0))}
                    {g.excluded > 0 && (
                      <Text span c="dimmed" fz="xs">
                        {' '}
                        / {fmt(g.cells)}
                      </Text>
                    )}
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
                  <Table.Td ta="center">
                    <Checkbox
                      size="xs"
                      aria-label={`재사용 ${g.table} ${g.column} ${g.code}`}
                      data-testid="reuse-group"
                      checked={g.approved && reuseGroups.includes(g.id)}
                      disabled={finished || !g.cells || !g.approved}
                      onChange={(e) => setReuseGroups(flip(reuseGroups, g.id, e.currentTarget.checked))}
                    />
                  </Table.Td>
                </Table.Tr>
                {expanded === g.id && (
                  <Table.Tr>
                    <Table.Td colSpan={8} p={0}>
                      <GroupCells
                        run={run}
                        group={g}
                        data={x}
                        disabled={finished || select.isPending}
                        onToggle={toggleCell}
                      />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            ))}
          </Table.Tbody>
        </Table>
        </Table.ScrollContainer>
      </Card>
      <Modal opened={open} onClose={() => setOpen(false)} title="최종 확인" centered>
        <Stack>
          <Table fz="sm">
            <Table.Tbody>
              <Table.Tr>
                <Table.Td>적용</Table.Td>
                <Table.Td ta="right" fw={700} data-testid="confirm-apply">
                  {fmt(x.total_cells)}
                </Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>제외</Table.Td>
                <Table.Td ta="right">{fmt(x.not_approved)}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td>보류</Table.Td>
                <Table.Td ta="right">{fmt(x.held)}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
          {blocked.length > 0 && (
            <Alert color="orange" p="xs">
              발행 제외: {blocked.map(([t]) => t).join(', ')}
            </Alert>
          )}
          <Stack gap={4}>
            <Text fz="sm" fw={600}>
              재사용 규칙 <span data-testid="reuse-count">{reuseCount}</span>
            </Text>
            {targets.map((f) => (
              <Checkbox
                key={f.item_id}
                size="xs"
                data-testid="reuse-mapping"
                label={`${f.name}${f.sheet ? ` [${f.sheet}]` : ''} · 매핑`}
                checked={reuseMappings.includes(f.item_id)}
                onChange={(e) => setReuseMappings(flip(reuseMappings, f.item_id, e.currentTarget.checked))}
              />
            ))}
          </Stack>
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
