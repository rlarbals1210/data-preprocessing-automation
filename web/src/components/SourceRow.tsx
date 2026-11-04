import { ActionIcon, Alert, Code, Loader, Modal, Table, Text, Tooltip } from '@mantine/core';
import { IconFileSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, type Source } from '../api';

/** 원본 위치의 원문 행(AC-04-4). 강조할 원래 컬럼 이름을 받는다. */
export function SourceButton({ runId, source, highlight }: { runId: string; source?: Source | null; highlight?: string[] }) {
  const [open, setOpen] = useState(false);
  if (!source) return null;
  return (
    <>
      <Tooltip label="원문 보기">
        <ActionIcon variant="subtle" color="gray" size="sm" aria-label="원문 보기" onClick={() => setOpen(true)}>
          <IconFileSearch size={15} />
        </ActionIcon>
      </Tooltip>
      {open && <SourceModal runId={runId} source={source} highlight={highlight ?? []} onClose={() => setOpen(false)} />}
    </>
  );
}

function SourceModal({ runId, source, highlight, onClose }: { runId: string; source: Source; highlight: string[]; onClose: () => void }) {
  const row = useQuery({
    queryKey: ['source', runId, source.item_id, source.record],
    queryFn: () => api.sourceRow(runId, source.item_id, source.record),
  });
  const d = row.data;
  const title = !d
    ? '원문'
    : d.sheet
      ? `${d.file} · ${d.sheet} · ${d.record}행 · 엑셀 ${d.sheet_row}행`
      : `${d.file} · ${d.record}행 · ${d.line_start === d.line_end ? d.line_start : `${d.line_start}–${d.line_end}`}줄`;
  return (
    <Modal opened onClose={onClose} title={title} size="lg" centered>
      {row.isLoading && <Loader size="sm" />}
      {row.error && <Alert color="red">{(row.error as Error).message}</Alert>}
      {row.data && (
        <Table fz="sm" verticalSpacing={4} data-testid="source-row">
          <Table.Tbody>
            {row.data.cells.map((c) => (
              <Table.Tr key={c.original} bg={highlight.includes(c.original) ? 'yellow.1' : undefined}>
                {d?.sheet && (
                  <Table.Td c="dimmed" ff="monospace" fz="xs">
                    {c.cell}
                  </Table.Td>
                )}
                <Table.Td fw={600}>{c.original}</Table.Td>
                <Table.Td ff="monospace" c="dimmed">
                  {c.logical ?? '—'}
                </Table.Td>
                <Table.Td>
                  {c.value === '' ? (
                    <Text span c="dimmed">
                      ∅
                    </Text>
                  ) : (
                    <Code style={{ whiteSpace: 'pre' }}>{c.value}</Code>
                  )}
                  {c.type && c.type !== 'text' && c.type !== 'empty' && (
                    <Text span c="dimmed" fz="xs">
                      {' '}
                      {c.type}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Modal>
  );
}
