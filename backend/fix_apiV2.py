content = open('../frontend/src/services/apiV2.ts', 'r').read()

start_idx = content.find('export const readScanMemory')
if start_idx != -1:
    content = content[:start_idx]
    
new_funcs = '''export const readScanMemory = async (barcode: string): Promise<ScanDecisionMemory> => {
  const result = await api.get(/scan/verdict//memory);
  return result.data;
};

export const saveScanDecision = async (
  barcode: string,
  payload: { decision: string; label_version: number; content_fingerprint: string; note?: string | null }
): Promise<ScanDecisionEvent> => {
  const result = await api.post(/scan/verdict//memory, payload);
  return result.data;
};
'''
content += new_funcs
open('../frontend/src/services/apiV2.ts', 'w').write(content)
print("Fixed apiV2.ts for real")
