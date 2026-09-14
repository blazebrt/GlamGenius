content = open('../frontend/app/verdict.tsx', 'r').read()

imports = '''import { ScanDecisionMemorySection } from '../src/components/shopping/ScanDecisionMemorySection';
import { readScanMemory, type ScanDecisionMemory } from '../src/services/apiV2';'''

if 'ScanDecisionMemorySection' not in content:
    content = content.replace('import { buildVerdictShareText }', imports + '\nimport { buildVerdictShareText }')

state = '''
  const [memory, setMemory] = useState<ScanDecisionMemory | null>(null);
  const loadMemory = useCallback(async () => {
    if (!barcode || !signedIn || referenceView) return;
    try {
      const data = await readScanMemory(barcode);
      setMemory(data);
    } catch {
      setMemory(null);
    }
  }, [barcode, signedIn, referenceView]);
  
  useEffect(() => {
    if (loadState === 'ready') {
      void loadMemory();
    }
  }, [loadState, loadMemory]);
'''

if 'loadMemory' not in content:
    content = content.replace('const view = useMemo(() => (source ? buildVerdict(source) : null), [source]);', state + '\n  const view = useMemo(() => (source ? buildVerdict(source) : null), [source]);')

render_block = '''
              <CommunityObservations observations={source.communityObservations} onOpen={openCommunityReport} />
              
              {signedIn && !referenceView && source.labelVersion && memory && (
                <ScanDecisionMemorySection
                  barcode={barcode}
                  labelVersion={source.labelVersion.versionNumber}
                  contentFingerprint={source.labelVersion.contentFingerprint}
                  memory={memory}
                  onMemoryUpdated={() => void loadMemory()}
                />
              )}
'''

if 'ScanDecisionMemorySection' not in content.split('loadMemory')[1]:
    content = content.replace('<CommunityObservations observations={source.communityObservations} onOpen={openCommunityReport} />', render_block)

open('../frontend/app/verdict.tsx', 'w').write(content)
print("Updated verdict.tsx")
