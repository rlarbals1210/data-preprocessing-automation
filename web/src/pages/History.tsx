import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, Modal, Select, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { IconArrowBackUp, IconDownload } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, type ResultVersion } from '../api';
import { RESULT_STATUS, fmt, when } from '../labels';

const HASH: Record<string, string> = {
  config_hash: '설정',
  input_hash: '실행 대상',
  mapping_hash: '매핑',
  plan_hash: '실행 계획',
  view_hash: '표준화 뷰',
  candidate_hash: '표준화 후보',
  apply_hash: '적용 선택',
};

function Detail({ vid }: { vid: number }) {
  const d = useQuery({ queryKey: ['version', vid], queryFn: () => api.version(vid) });
  if (d.isLoading) return <Loader size="sm" />;
  if (!d.data) return null;
  const x = d.data;
  return (
    <Stack gap="xs" data-testid="version-detail">
      <Text fz="sm">
        데이터 해시 <Code>{x.data_hash.slice(0, 16)}</Code>
        {x.kind === 'restore' && (
          <Text span c="dimmed" fz="sm">
            {' '}
            · v{x.origin}과 같은 데이터
          </Text>
        )}
      </Text>
      <Table fz="xs" verticalSpacing={2}>
        <Table.Tbody>
          <Table.Tr>
            <Table.Td>설정 스냅샷</Table.Td>
            <Table.Td ff="monospace">{x.refs.config_snapshot?.slice(0, 16)}</Table.Td>
          </Table.Tr>
          <Table.Tr>
            <Table.Td>매핑 버전</Table.Td>
            <Table.Td ff="monospace">
              {x.refs.mapping_version ? `${x.refs.mapping_version.version} · ${x.refs.mapping_version.hash.slice(0, 16)}` : '—'}
            </Table.Td>
          </Table.Tr>
          {Object.entries(x.refs.approval_hashes).map(([k, v]) => (
            <Table.Tr key={k}>
              <Table.Td>실행 승인 · {HASH[k] ?? k}</Table.Td>
              <Table.Td ff="monospace">{v?.slice(0, 16)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group gap="sm">
        {x.files.map((f) => (
          <Anchor key={f.name} href={api.versionFileUrl(vid, f.name)} fz="sm">
            <Group gap={4}>
              <IconDownload size={14} />
              {f.name}
            </Group>
          </Anchor>
        ))}
        <Anchor href={api.versionFileUrl(vid, 'changes.csv')} fz="sm" c="dimmed">
          변경 내역
        </Anchor>
      </Group>
    </Stack>
  );
}

export default function History() {
  const [params, setParams] = useSearchParams();
  const sources = useQuery({ queryKey: ['sources'], queryFn: api.sources });
  const withVersions = (sources.data ?? []).filter((s) => s.versions > 0);
  const sid = Number(params.get('source')) || withVersions[0]?.id || null;
  const vs = useQuery({
    queryKey: ['versions', sid],
    queryFn: () => api.sourceVersions(sid!),
    enabled: !!sid,
  });
  const [open, setOpen] = useState<number | null>(null);
  const [target, setTarget] = useState<ResultVersion | null>(null);
  const qc = useQueryClient();
  const restore = useMutation({
    mutationFn: (vid: number) => api.restore(vid),
    onSuccess: (v) => {
      setTarget(null);
      setOpen(v.id);
      qc.invalidateQueries({ queryKey: ['versions', sid] });
      qc.invalidateQueries({ queryKey: ['sources'] });
    },
  });

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>실행 이력</Title>
        <Select
          w={260}
          placeholder="출처"
          value={sid ? String(sid) : null}
          onChange={(v) => v && setParams({ source: v })}
          data={withVersions.map((s) => ({ value: String(s.id), label: `${s.name} · ${s.versions}` }))}
          allowDeselect={false}
        />
      </Group>
      {sources.isLoading || vs.isLoading ? (
        <Loader />
      ) : !sid ? (
        <Text c="dimmed">—</Text>
      ) : (
        <Card withBorder p={0}>
          <Table verticalSpacing="sm" highlightOnHover data-testid="versions">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>버전</Table.Th>
                <Table.Th>종류</Table.Th>
                <Table.Th>시각</Table.Th>
                <Table.Th>실행</Table.Th>
                <Table.Th ta="right">변경 셀</Table.Th>
                <Table.Th>검사 범위</Table.Th>
                <Table.Th>남은 문제</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {[...(vs.data ?? [])].reverse().map((v) => (
                <Fragment key={v.id}>
                  <Table.Tr
                    data-testid="version-row"
                    style={{ cursor: 'pointer' }}
                    onClick={() => setOpen(open === v.id ? null : v.id)}
                  >
                    <Table.Td fw={700}>
                      v{v.id}
                      {v.current && (
                        <Badge ml={6} size="sm" color="teal" data-testid="current">
                          현재
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {v.kind === 'restore' ? (
                        <Badge variant="light" color="violet">
                          복원 ← v{v.restored_from}
                        </Badge>
                      ) : (
                        <Badge variant="light">정제</Badge>
                      )}
                    </Table.Td>
                    <Table.Td c="dimmed">{when(v.created_at)}</Table.Td>
                    <Table.Td>
                      {v.run && (
                        <Anchor component={Link} to={`/runs/${v.run}/result`} ff="monospace" fz="sm" onClick={(e) => e.stopPropagation()}>
                          {v.run}
                        </Anchor>
                      )}
                      <Badge ml={6} size="xs" variant="dot" color={RESULT_STATUS[v.run_status]?.color}>
                        {RESULT_STATUS[v.run_status]?.label}
                      </Badge>
                    </Table.Td>
                    <Table.Td ta="right">{fmt(v.changed_cells)}</Table.Td>
                    <Table.Td>
                      <Badge size="sm" variant="light" color={v.completeness === 'partial' ? 'orange' : 'teal'}>
                        {v.completeness === 'partial' ? '부분 검사' : '전체 검사'}
                      </Badge>
                    </Table.Td>
                    <Table.Td fz="sm">
                      위반 {fmt(v.remaining.business_rule_violation)} · 검토 {fmt(v.remaining.human_review)}
                      {!!v.integrity_after && ` · 정합성 ${fmt(v.integrity_after)}`}
                    </Table.Td>
                    <Table.Td>
                      {!v.current && (
                        <Tooltip label="이 버전으로 되돌리기">
                          <Button
                            size="compact-sm"
                            variant="light"
                            leftSection={<IconArrowBackUp size={14} />}
                            onClick={(e) => {
                              e.stopPropagation();
                              setTarget(v);
                            }}
                          >
                            되돌리기
                          </Button>
                        </Tooltip>
                      )}
                    </Table.Td>
                  </Table.Tr>
                  {open === v.id && (
                    <Table.Tr>
                      <Table.Td colSpan={8} bg="gray.0">
                        <Detail vid={v.id} />
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      <Modal opened={!!target} onClose={() => setTarget(null)} title="되돌리기" centered>
        <Stack>
          <Text fz="sm">
            v{target?.id}의 데이터로 새 복원 버전을 만듭니다. 지금까지의 버전과 승인 기록은 그대로 남고, 재사용 규칙은 바뀌지
            않습니다.
          </Text>
          <Alert color="gray" p="xs" fz="sm">
            이미 내려받은 파일은 회수되지 않습니다.
          </Alert>
          {restore.error && <Alert color="red">{(restore.error as Error).message}</Alert>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setTarget(null)}>
              취소
            </Button>
            <Button loading={restore.isPending} onClick={() => target && restore.mutate(target.id)}>
              되돌리기
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
