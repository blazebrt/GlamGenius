/**
 * "Take me back to a fresh scanner", passed from a Product Result to the scanner.
 *
 * The scanner keeps its last result on screen so that going back shows what
 * was scanned. "Scan another product" means something different — start over —
 * so it leaves a one-shot request here and the scanner honours it the next
 * time it is focused. Nothing is stored and nothing leaves the phone.
 */
let freshScanRequested = false;

export function requestFreshScan(): void {
  freshScanRequested = true;
}

/** True once per request. */
export function consumeFreshScanRequest(): boolean {
  const requested = freshScanRequested;
  freshScanRequested = false;
  return requested;
}
