/**
 * The shelf-capture outbox, which must not lose a tap.
 *
 * Taps are recorded locally and flushed in the background so a slow network
 * never sits between two of them. The hazard is the tap made *while* a request
 * is in flight: if the flush that finishes does not come back for it, and the
 * "everything decided" path treats a skipped flush as a completed one, those
 * decisions are never sent and the products never reach the shelf.
 *
 * This exercises the drain directly rather than through the screen, because
 * the ordering is the thing being tested.
 */

type Entry = { candidate_id: string; accept: boolean };

/** A promise the test releases when it chooses. */
function deferred(): { promise: Promise<void>; resolve: () => void } {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => { resolve = r; });
  return { promise, resolve };
}

/**
 * The same drain the screen runs. Kept in step with app/inventory-batch.tsx:
 * join an in-flight run rather than skipping it, and loop until empty.
 */
function makeFlusher(send: (rows: Entry[]) => Promise<void>) {
  const outbox: { current: Entry[] } = { current: [] };
  const flushing: { current: Promise<void> | null } = { current: null };

  const flush = (): Promise<void> => {
    if (flushing.current) return flushing.current;
    if (outbox.current.length === 0) return Promise.resolve();
    const run = (async () => {
      while (outbox.current.length > 0) {
        const sending = outbox.current;
        outbox.current = [];
        try {
          await send(sending);
        } catch {
          outbox.current = [...sending, ...outbox.current];
          return;
        }
      }
    })().finally(() => { flushing.current = null; });
    flushing.current = run;
    return run;
  };

  return { outbox, flush };
}

describe('the shelf-capture outbox', () => {
  it('sends a tap made while a request was already in flight', async () => {
    const gate = deferred();
    const sent: Entry[][] = [];
    const send = (rows: Entry[]) => {
      sent.push(rows);
      // The first request hangs until the test lets it go.
      return sent.length === 1 ? gate.promise : Promise.resolve();
    };

    const { outbox, flush } = makeFlusher(send);

    outbox.current.push({ candidate_id: 'a', accept: true });
    const first = flush();
    await Promise.resolve();

    // A second tap lands while the first request is still open.
    outbox.current.push({ candidate_id: 'b', accept: true });
    const second = flush();

    gate.resolve();
    await Promise.all([first, second]);

    expect(sent.flat().map((row) => row.candidate_id)).toEqual(['a', 'b']);
    expect(outbox.current).toHaveLength(0);
  });

  it('waits for the run in progress instead of resolving straight away', async () => {
    const gate = deferred();
    let finished = false;
    const send = () => gate.promise.then(() => { finished = true; });

    const { outbox, flush } = makeFlusher(send);
    outbox.current.push({ candidate_id: 'a', accept: true });
    void flush();
    await Promise.resolve();

    // A caller asking again must not be told "nothing to do" and move on.
    const joined = flush().then(() => finished);
    gate.resolve();
    expect(await joined).toBe(true);
  });

  it('keeps decisions when the request fails, rather than dropping them', async () => {
    const send = () => Promise.reject(new Error('offline'));
    const { outbox, flush } = makeFlusher(send);
    outbox.current.push({ candidate_id: 'a', accept: true });
    outbox.current.push({ candidate_id: 'b', accept: false });

    await flush();

    expect(outbox.current.map((row) => row.candidate_id)).toEqual(['a', 'b']);
  });
});
