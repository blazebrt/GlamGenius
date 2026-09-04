const assert = require("node:assert/strict");
const test = require("node:test");

const { auditOutputIsUsable, runWithRetries } = require("./run-node-audit");
const runnerSource = require("node:fs").readFileSync(`${__dirname}/run-node-audit.js`, "utf8");

const ADVISORY_LINE = JSON.stringify({
  type: "auditAdvisory",
  data: {
    advisory: {
      github_advisory_id: "GHSA-test-test-test",
      module_name: "example",
      severity: "high",
      cves: ["CVE-2026-00000"],
      findings: [{ version: "1.0.0", paths: ["example"] }],
    },
  },
});

const SUMMARY_LINE = JSON.stringify({
  type: "auditSummary",
  data: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0 } },
});

// The exact stdout Yarn left behind when the advisory endpoint failed on
// 2026-09-04: one informational record and no audit data at all.
const OUTAGE_STDOUT = JSON.stringify({
  type: "info",
  data: "Visit https://yarnpkg.com/en/docs/cli/audit for documentation about this command.",
});

function collector() {
  const lines = [];
  return { lines, log: (line) => lines.push(line) };
}

test("empty, blank and non-record output is not usable", () => {
  for (const text of ["", "   ", "\n\n", undefined, null, 42]) {
    assert.equal(auditOutputIsUsable(text), false);
  }
});

test("the observed outage output is not usable", () => {
  assert.equal(auditOutputIsUsable(OUTAGE_STDOUT), false);
});

test("a clean audit that emits only a summary is usable", () => {
  // This is the case that must never be confused with an outage: zero
  // vulnerabilities is a real answer, and it must not be retried or rejected.
  assert.equal(auditOutputIsUsable(SUMMARY_LINE), true);
});

test("advisory records are usable", () => {
  assert.equal(auditOutputIsUsable(`${ADVISORY_LINE}\n${SUMMARY_LINE}`), true);
});

test("non-JSON noise around a real record stays usable", () => {
  assert.equal(auditOutputIsUsable(`warning something\n${SUMMARY_LINE}`), true);
});

test("a usable first attempt is never retried", () => {
  let calls = 0;
  const result = runWithRetries({
    attempts: 3,
    backoffMs: 0,
    runAudit: () => {
      calls += 1;
      return { stdout: `${ADVISORY_LINE}\n${SUMMARY_LINE}`, stderr: "", status: 8 };
    },
    sleep: () => {},
    log: () => {},
  });
  assert.equal(calls, 1);
  assert.equal(result.attempts, 1);
  assert.equal(result.usable, true);
});

test("a HIGH finding is passed straight through, not retried away", () => {
  // The whole point: a non-zero exit caused by a real advisory must reach the
  // validator unchanged. Retrying here could only hide a finding.
  const result = runWithRetries({
    attempts: 3,
    backoffMs: 0,
    runAudit: () => ({ stdout: ADVISORY_LINE, stderr: "", status: 8 }),
    sleep: () => {},
    log: () => {},
  });
  assert.equal(result.attempts, 1);
  assert.equal(result.stdout, ADVISORY_LINE);
});

test("an outage is retried up to the attempt limit and then gives up unusable", () => {
  let calls = 0;
  const result = runWithRetries({
    attempts: 3,
    backoffMs: 0,
    runAudit: () => {
      calls += 1;
      return { stdout: OUTAGE_STDOUT, stderr: "ResponseError: 504 Gateway Timeout", status: 1 };
    },
    sleep: () => {},
    log: () => {},
  });
  assert.equal(calls, 3);
  assert.equal(result.usable, false);
  // Giving up must leave the output unusable so the gate fails closed.
  assert.equal(auditOutputIsUsable(result.stdout), false);
});

test("a recovered attempt after an outage is used", () => {
  let calls = 0;
  const result = runWithRetries({
    attempts: 3,
    backoffMs: 0,
    runAudit: () => {
      calls += 1;
      return calls < 3
        ? { stdout: "", stderr: "504", status: 1 }
        : { stdout: SUMMARY_LINE, stderr: "", status: 0 };
    },
    sleep: () => {},
    log: () => {},
  });
  assert.equal(calls, 3);
  assert.equal(result.usable, true);
  assert.equal(result.stdout, SUMMARY_LINE);
});

test("backoff grows and is skipped after the final attempt", () => {
  const waits = [];
  runWithRetries({
    attempts: 3,
    backoffMs: 100,
    runAudit: () => ({ stdout: "", stderr: "", status: 1 }),
    sleep: (ms) => waits.push(ms),
    log: () => {},
  });
  assert.deepEqual(waits, [100, 200]);
});

test("each attempt is reported so an outage is legible in the CI log", () => {
  const { lines, log } = collector();
  runWithRetries({
    attempts: 2,
    backoffMs: 0,
    runAudit: () => ({ stdout: "", stderr: "ResponseError: 504 Gateway Timeout", status: 1 }),
    sleep: () => {},
    log,
  });
  assert.match(lines[0], /attempt 1\/2: exit=1 usable_records=false/);
  assert.match(lines.join("\n"), /504 Gateway Timeout/);
});

test("the default attempt budget is the one the incident data justifies", () => {
  // Pinned deliberately: this number was chosen from a measured ~38% per-attempt
  // success rate during the outage, not picked to make a build go green. Moving
  // it should mean new evidence, and should break this test.
  assert.match(runnerSource, /const DEFAULT_ATTEMPTS = 5;/);
  let calls = 0;
  runWithRetries({
    backoffMs: 0,
    runAudit: () => {
      calls += 1;
      return { stdout: "", stderr: "down", status: 1 };
    },
    sleep: () => {},
    log: () => {},
  });
  assert.equal(calls, 5);
});
