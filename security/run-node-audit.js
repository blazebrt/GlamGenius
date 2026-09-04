#!/usr/bin/env node
"use strict";

/**
 * Run `yarn audit` and leave its output where the security gate can read it.
 *
 * This exists because the advisory service is a third party that sometimes
 * fails. On 2026-09-04 `registry.yarnpkg.com/-/npm/v1/security/audits`
 * answered `504 Gateway Timeout`; Yarn crashed, wrote no advisory records, and
 * the gate failed closed with "Audit output is empty". The gate was right —
 * an audit that never ran has proved nothing, and treating it as clean is
 * exactly the hole a security gate exists to close. But a single request
 * against a flaky endpoint turns main red for a reason that has nothing to do
 * with the code being merged.
 *
 * So this retries, and does nothing else.
 *
 * **It never decides pass or fail.** It always exits 0 and always leaves the
 * final output at the requested path, so `validate-node-audit.js` remains the
 * one and only gate. If every attempt fails, the output is incomplete and the
 * validator still fails closed — the same red build as before, reached the
 * same way, just not on the first hiccup.
 *
 * What counts as a finished attempt is a valid `auditSummary`, and nothing
 * else. Advisory records are not a completion marker: a scan that emitted a
 * few advisories and then died looks identical, by record count, to one that
 * finished. Stopping on advisories alone would mean a truncated scan whose
 * partial findings all happen to be governed could be reported as clean.
 *
 * Retrying can only ever turn an unfinished audit into a finished one. It
 * cannot turn a finding into a pass: a completed audit satisfies the marker on
 * its first attempt, no retry happens, and the validator sees byte-for-byte
 * what it would have seen before this script existed.
 */

const fs = require("node:fs");
const { spawnSync } = require("node:child_process");

// Borrowed from the authority rather than restated. The validator decides what
// a completion marker is; this only asks it. Two copies of that definition
// could drift, and the direction of drift that matters -- the runner accepting
// something the validator would reject -- is exactly the bug being closed here.
const { auditSummaryIsValid } = require("./validate-node-audit");

// Sized to measured behaviour, not to taste. During the 2026-09-04 incident the
// endpoint recovered into an intermittent state rather than coming straight
// back: 3 of 8 probes succeeded. At that rate 3 attempts clear it ~76% of the
// time and 5 attempts ~90%, which is the difference between a gate that
// annoys people into ignoring it and one they trust. Raising this never
// weakens the gate -- an audit that never returns still fails closed.
const DEFAULT_ATTEMPTS = 5;
const DEFAULT_BACKOFF_MS = 5000;

/**
 * Did this attempt run the audit to completion?
 *
 * Completion means one thing: a valid `auditSummary`. Advisory records do not
 * count, however many arrive. An audit that emitted three advisories and then
 * died is indistinguishable, by record count alone, from one that found three
 * advisories and finished -- and if those three happen to be already governed
 * by exceptions, treating the truncated stream as finished would report a
 * clean bill of health for every dependency the scanner never reached.
 *
 * So an advisory-only stream is incomplete and gets retried. This still does
 * not judge findings: whether the advisories pass is the validator's call, and
 * the validator independently rejects a summary-less stream too.
 */
function auditOutputIsComplete(text) {
  if (typeof text !== "string" || !text.trim()) return false;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      // Yarn prints non-JSON noise on its error paths. Not a completion marker,
      // but not proof the whole run failed either -- keep looking.
      continue;
    }
    if (auditSummaryIsValid(record)) return true;
  }
  return false;
}

function sleepSync(milliseconds) {
  if (milliseconds <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, milliseconds);
}

function positiveIntFromEnv(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Run the audit up to `attempts` times, stopping at the first completed audit.
 * `runAudit` is injected so the retry policy can be tested without a network.
 */
function runWithRetries({
  attempts = DEFAULT_ATTEMPTS,
  backoffMs = DEFAULT_BACKOFF_MS,
  runAudit,
  sleep = sleepSync,
  log = console.error,
} = {}) {
  let last = { stdout: "", stderr: "", status: null };
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    last = runAudit(attempt);
    const complete = auditOutputIsComplete(last.stdout);
    log(
      `yarn audit attempt ${attempt}/${attempts}: exit=${last.status} ` +
        `completed=${complete}`,
    );
    if (complete) return { ...last, attempts: attempt, complete: true };
    if (attempt < attempts) {
      const excerpt = (last.stderr || "").trim().split("\n")[0] || "(no stderr)";
      log(`  audit did not run to completion (no auditSummary): ${excerpt}`);
      // Linear backoff. The failure mode is an overloaded upstream, so the
      // point is to wait, not to be clever about how long.
      sleep(backoffMs * attempt);
    }
  }
  return { ...last, attempts, complete: false };
}

function main(argv) {
  const outputPath = argv[2] || "/tmp/yarn-audit.json";
  const attempts = positiveIntFromEnv(process.env.NODE_AUDIT_ATTEMPTS, DEFAULT_ATTEMPTS);
  const backoffMs = positiveIntFromEnv(process.env.NODE_AUDIT_BACKOFF_MS, DEFAULT_BACKOFF_MS);

  const result = runWithRetries({
    attempts,
    backoffMs,
    runAudit: () =>
      spawnSync("yarn", ["audit", "--level", "high", "--json"], {
        encoding: "utf8",
        maxBuffer: 64 * 1024 * 1024,
      }),
  });

  fs.writeFileSync(outputPath, result.stdout || "");

  if (!result.complete) {
    console.error(
      `The audit did not run to completion after ${result.attempts} attempt(s): ` +
        "no auditSummary was returned. The raw output is kept for diagnostics, " +
        "but the security gate will now fail closed, which is correct -- an " +
        "audit that did not finish cannot clear a dependency tree, and a " +
        "partial advisory stream must never be mistaken for a finished one.",
    );
    if (result.stderr) console.error(result.stderr.trim().slice(0, 2000));
  }

  // Always 0. validate-node-audit.js is the gate; this is only its plumbing.
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv);

module.exports = {
  auditOutputIsComplete,
  runWithRetries,
};
