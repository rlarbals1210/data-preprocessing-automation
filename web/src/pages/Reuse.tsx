import { ActionIcon, Alert, Anchor, Badge, Button, Card, Code, Group, Loader, Modal, Stack, Table, Text, Textarea, Title } from '@mantine/core';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type ReuseRule } from '../api';
import { CHANGE, CODE, REUSE_KIND, REUSE_STATE, when } from '../labels';

function Spec({ r }: { r: ReuseRule }) {
  if (r.kind === 'mapping')
    return (
      <Table fz="xs" verticalSpacing={2} data-testid="rule-spec">
        <Table.Tbody>
          {Object.entries(r.spec.columns ?? {}).map(([orig, logical]) => (
            <Table.Tr key={orig}>
              <Table.Td>{orig}</Table.Td>
              <Table.Td ff="monospace">{logical ?? '—'}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    );
  return (
    <Group gap={4} data-testid="rule-spec">
      <Code>{r.transform_id}</Code>
      {(r.spec.signatures ?? []).map((s) => (
        <Code key={s} color="gray.1">
          {s || '∅'}
        </Code>
      ))}
    </Group>
  );
}

export default function Reuse() {
  const units = useQuery({ queryKey: ['reuse-rules'], queryFn: api.reuseRules });
  const [open, setOpen] = useState<number | null>(null);
  const [target, setTarget] = useState<ReuseRule | null>(null);
  const [reason, setReason] = useState('');
  const [affected, setAffected] = useState<string[]>([]);
  const qc = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => api.revokeReuseRule(target!.id, reason),
    onSuccess: (res) => {
      setTarget(null);
      setReason('');
      setAffected(res.runs);
      qc.invalidateQueries({ queryKey: ['reuse-rules'] });
      qc.invalidateQueries({ queryKey: ['preview'] });
      qc.invalidateQueries({ queryKey: ['run'] });
    },
  });

  return (
    <Stack>
      <Title order={3}>재사용 규칙</Title>
      {affected.length > 0 && (
        <Alert color="yellow" title="다시 확인할 실행" withCloseButton onClose={() => setAffected([])} data-testid="revoke-affected">
          <Group gap="sm">
            {affected.map((id) => (
              <Anchor key={id} component={Link} to={`/runs/${id}`} ff="monospace" fz="sm">
                {id}
              </Anchor>
            ))}
          </Group>
        </Alert>
      )}
      {units.isLoading ? (
        <Loader />
      ) : units.error ? (
        <Alert color="red">{(units.error as Error).message}</Alert>
      ) : !units.data?.length ? (
        <Text c="dimmed">—</Text>
      ) : (
        units.data.map((u) => (
          <Card key={u.template_id} withBorder p={0} data-testid="reuse-unit">
            <Group p="sm" gap="sm">
              <Text fw={600}>{u.source.name}</Text>
              <Badge variant="light">{u.table}</Badge>
              <Text fz="xs" c="dimmed" ff="monospace" style={{ wordBreak: 'break-all' }}>
                {u.headers.join(', ')}
              </Text>
            </Group>
            <Table verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th w={40} />
                  <Table.Th>종류</Table.Th>
                  <Table.Th>규칙</Table.Th>
                  <Table.Th>버전</Table.Th>
                  <Table.Th>상태</Table.Th>
                  <Table.Th>승인</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {u.rules.map((r) => (
                  <Fragment key={r.id}>
                    <Table.Tr data-testid="reuse-rule">
                      <Table.Td>
                        <ActionIcon
                          variant="subtle"
                          color="gray"
                          aria-label={`규칙 ${r.id} 보기`}
                          onClick={() => setOpen(open === r.id ? null : r.id)}
                        >
                          {open === r.id ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                        </ActionIcon>
                      </Table.Td>
                      <Table.Td>{REUSE_KIND[r.kind]}</Table.Td>
                      <Table.Td>
                        {r.kind === 'mapping' ? (
                          <Text fz="sm">{r.spec.table}</Text>
                        ) : (
                          <Group gap={6} wrap="nowrap">
                            <Text fz="sm" ff="monospace">
                              {r.spec.column}
                            </Text>
                            <Badge variant="light" size="sm">
                              {CHANGE[r.spec.code!] ?? CODE[r.spec.code!] ?? r.spec.code}
                            </Badge>
                          </Group>
                        )}
                      </Table.Td>
                      <Table.Td>v{r.version}</Table.Td>
                      <Table.Td>
                        <Badge color={REUSE_STATE[r.state].color} variant="light" data-testid="rule-state">
                          {REUSE_STATE[r.state].label}
                        </Badge>
                        {r.revoked_reason && (
                          <Text fz="xs" c="dimmed">
                            {r.revoked_reason}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td fz="sm" style={{ whiteSpace: 'nowrap' }}>
                        {when(r.approved_at)}
                        {r.run && (
                          <Anchor component={Link} to={`/runs/${r.run}`} fz="xs" ml={6} ff="monospace">
                            {r.run}
                          </Anchor>
                        )}
                      </Table.Td>
                      <Table.Td ta="right">
                        {r.state === 'effective' && (
                          <Button size="xs" variant="light" color="red" onClick={() => setTarget(r)}>
                            승인 취소
                          </Button>
                        )}
                      </Table.Td>
                    </Table.Tr>
                    {open === r.id && (
                      <Table.Tr>
                        <Table.Td />
                        <Table.Td colSpan={6}>
                          <Spec r={r} />
                        </Table.Td>
                      </Table.Tr>
                    )}
                  </Fragment>
                ))}
              </Table.Tbody>
            </Table>
          </Card>
        ))
      )}
      <Modal opened={!!target} onClose={() => setTarget(null)} title="승인 취소">
        {target && (
          <Stack>
            <Text fz="sm">
              {REUSE_KIND[target.kind]} · {target.kind === 'mapping' ? target.spec.table : target.rule_key} · v{target.version}
            </Text>
            <Textarea
              label="사유"
              required
              autosize
              minRows={2}
              maxLength={500}
              value={reason}
              onChange={(e) => setReason(e.currentTarget.value)}
            />
            {revoke.error && <Alert color="red">{(revoke.error as Error).message}</Alert>}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setTarget(null)}>
                닫기
              </Button>
              <Button color="red" disabled={!reason.trim()} loading={revoke.isPending} onClick={() => revoke.mutate()}>
                승인 취소
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
