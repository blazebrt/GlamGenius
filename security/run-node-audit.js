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
 * one and only gate. If every attempt fails, the file is unusable and the
 * validator still fails closed — the same red build as before, reached the
 * same way, just not on the first hiccup.
 *
 * Retrying can only ever turn "no data" into "data". It cannot turn a finding
 * into a pass: when Yarn does return advisory records, the first attempt is
 * usable, no retry happens, and the validator sees byte-for-byte what it would
 * have seen before this script existed.
 */

const fs = require("node:fs");
const { spawnSync } = require("node:child_process");

/** The record types a real audit emits. A clean audit still emits a summary,
 *  which is why "no records at all" means the audit did not run — not that
 *  the tree is clean. */
const AUDIT_RECORD_TYPES = new Set(["auditAdvisory", "auditSummary"]);

const DEFAULT_ATTEMPTS = 3;
const DEFAULT_BACKOFF_MS = 5000;

/**
 * Did this run produce anything the gate can actually reason about?
 *
 * Deliberately permissive: one recognisable record is enough. Judging the
 * *content* is the validator's job, and a second opinion here could only
 * disagree with it.
 */
function auditOutputIsUsable(text) {
  if (typeof text !== "string" || !text.trim()) return false;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      // Yarn prints non-JSON noise on its error paths. Not a usable record,
      // but not proof the whole run failed either — keep looking.
      continue;
    }
    if (record && AUDIT_RECORD_TYPES.has(record.type)) return true;
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
 * Run the audit up to `attempts` times, stopping at the first usable output.
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
    const usable = auditOutputIsUsable(last.stdout);
    log(
      `yarn audit attempt ${attempt}/${attempts}: exit=${last.status} ` +
        `usable_records=${usable}`,
    );
    if (usable) return { ...last, attempts: attempt, usable: true };
    if (attempt < attempts) {
      const excerpt = (last.stderr || "").trim().split("\n")[0] || "(no stderr)";
      log(`  advisory service did not return audit records: ${excerpt}`);
      // Linear backoff. The failure mode is an overloaded upstream, so the
      // point is to wait, not to be clever about how long.
      sleep(backoffMs * attempt);
    }
  }
  return { ...last, attempts, usable: false };
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

  if (!result.usable) {
    console.error(
      `The advisory service returned no audit records after ${result.attempts} ` +
        "attempt(s). The security gate will now fail closed, which is correct: " +
        "an audit that did not run cannot clear a dependency tree.",
    );
    if (result.stderr) console.error(result.stderr.trim().slice(0, 2000));
  }

  // Always 0. validate-node-audit.js is the gate; this is only its plumbing.
  return 0;
}

if (require.main === module) process.exitCode = main(process.argv);

module.exports = {
  AUDIT_RECORD_TYPES,
  auditOutputIsUsable,
  runWithRetries,
};
