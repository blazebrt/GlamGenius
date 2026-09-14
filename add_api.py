import re

content = open('../frontend/src/services/apiV2.ts', 'r').read()

new_api = '''
export interface ScanDecisionEvent {
  id: string;
  decision: 'BUY' | 'WAIT' | 'SKIP';
  note: string | null;
  occurred_at: string | null;
}

export interface ScanDecisionMemory {
  scan_decision_memory_version: string;
  decision: ScanDecisionEvent | null;
  history: ScanDecisionEvent[];
}

export const readScanMemory = async (barcode: string): Promise<ScanDecisionMemory> => {
  const result = await fetchApi(/scan/verdict//memory, { method: 'GET' });
  if (!result.ok) throw new Error('Failed to read scan memory');
  return result.json();
};

export const saveScanDecision = async (
  barcode: string,
  payload: { decision: string; label_version: number; content_fingerprint: string; note?: string | null }
): Promise<ScanDecisionEvent> => {
  const result = await fetchApi(/scan/verdict//memory, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!result.ok) throw new Error('Failed to save scan decision');
  return result.json();
};
'''

if 'export const readScanMemory' not in content:
    content += '\n' + new_api
    open('../frontend/src/services/apiV2.ts', 'w').write(content)
    print("Added memory API to apiV2.ts")
