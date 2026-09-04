const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const { auditOutputIsComplete, runWithRetries } = require("./run-node-audit");
const runnerSource = fs.readFileSync(`${__dirname}/run-node-audit.js`, "utf8");

const registry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-exceptions.json`, "utf8"),
);
const approved = registry.exceptions[0];
const falsePositiveRegistry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-false-positives.json`, "utf8"),
);
const nanoidFalsePositive = falsePositiveRegistry.false_positives[0];

function advisoryLine({ advisoryId, cve, packageName, version, severity = "high", paths }) {
  return JSON.stringify({
    type: "auditAdvisory",
    data: {
      resolution: { path: paths[0] },
      advisory: {
        cves: [cve],
        findings: [{ paths, version }],
        github_advisory_id: advisoryId,
        module_name: packageName,
        severity,
      },
    },
  });
}

/** The exact image-size advisory the repository already governs. */
const GOVERNED_ADVISORY = advisoryLine({
  advisoryId: approved.advisory_id,
  cve: approved.cve,
  packageName: approved.package,
  version: approved.installed_version,
  severity: approved.severity,
  paths: approved.dependency_paths,
});

/** The exact Nano ID advisory the repository already treats as a false positive. */
const FALSE_POSITIVE_ADVISORY = advisoryLine({
  advisoryId: nanoidFalsePositive.advisory_id,
  cve: nanoidFalsePositive.cve,
  packageName: nanoidFalsePositive.package,
  version: nanoidFalsePositive.installed_version,
  severity: nanoidFalsePositive.severity,
  paths: nanoidFalsePositive.dependency_paths,
});

const UNKNOWN_HIGH_ADVISORY = advisoryLine({
  advisoryId: "GHSA-zzzz-zzzz-zzzz",
  cve: "CVE-2099-0001",
  packageName: "totally-new-package",
  version: "1.0.0",
  paths: ["app>totally-new-package"],
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

function run(overrides) {
  return runWithRetries({ backoffMs: 0, sleep: () => {}, log: () => {}, ...overrides });
}

// ---- what counts as a completed audit -------------------------------------

test("empty, blank and non-record output is not complete", () => {
  for (const text of ["", "   ", "\n\n", undefined, null, 42]) {
    assert.equal(auditOutputIsComplete(text), false);
  }
});

test("the observed outage output is not complete", () => {
  assert.equal(auditOutputIsComplete(OUTAGE_STDOUT), false);
});

test("a summary alone is complete", () => {
  // Zero vulnerabilities is a real answer and must never be mistaken for an
  // outage: it must not be retried, and it must not be rejected.
  assert.equal(auditOutputIsComplete(SUMMARY_LINE), true);
});

test("advisories plus a summary are complete", () => {
  assert.equal(auditOutputIsComplete(`${GOVERNED_ADVISORY}\n${SUMMARY_LINE}`), true);
});

test("advisories WITHOUT a summary are NOT complete", () => {
  // The defect this closes. Records arrived, but the scan never finished.
  assert.equal(auditOutputIsComplete(GOVERNED_ADVISORY), false);
  assert.equal(auditOutputIsComplete(FALSE_POSITIVE_ADVISORY), false);
  assert.equal(auditOutputIsComplete(UNKNOWN_HIGH_ADVISORY), false);
  assert.equal(
    auditOutputIsComplete(`${GOVERNED_ADVISORY}\n${FALSE_POSITIVE_ADVISORY}`),
    false,
  );
});

test("a malformed summary is not a completion marker", () => {
  for (const data of [null, "summary", [], { vulnerabilities: null }, { vulnerabilities: [] }, {}]) {
    assert.equal(auditOutputIsComplete(JSON.stringify({ type: "auditSummary", data })), false);
  }
});

test("non-JSON noise around a real summary stays complete", () => {
  assert.equal(auditOutputIsComplete(`warning something\n${SUMMARY_LINE}`), true);
});

// ---- retry policy ----------------------------------------------------------

test("a completed first attempt is never retried", () => {
  let calls = 0;
  const result = run({
    attempts: 3,
    runAudit: () => {
      calls += 1;
      return { stdout: `${GOVERNED_ADVISORY}\n${SUMMARY_LINE}`, stderr: "", status: 8 };
    },
  });
  assert.equal(calls, 1);
  assert.equal(result.attempts, 1);
  assert.equal(result.complete, true);
});

test("H: a completed unaccepted HIGH is passed straight through, not retried away", () => {
  const result = run({
    attempts: 5,
    runAudit: () => ({ stdout: `${UNKNOWN_HIGH_ADVISORY}\n${SUMMARY_LINE}`, stderr: "", status: 8 }),
  });
  assert.equal(result.attempts, 1);
  assert.equal(result.complete, true);
  assert.match(result.stdout, /GHSA-zzzz-zzzz-zzzz/);
});

test("C/D/E/F: a summary-less advisory stream is retried, never accepted", () => {
  for (const stdout of [
    GOVERNED_ADVISORY,
    FALSE_POSITIVE_ADVISORY,
    `${GOVERNED_ADVISORY}\n${FALSE_POSITIVE_ADVISORY}`,
    UNKNOWN_HIGH_ADVISORY,
  ]) {
    let calls = 0;
    const result = run({
      attempts: 5,
      runAudit: () => {
        calls += 1;
        return { stdout, stderr: "connection reset", status: 1 };
      },
    });
    assert.equal(calls, 5);
    assert.equal(result.complete, false);
    assert.equal(auditOutputIsComplete(result.stdout), false);
  }
});

test("G: a partial attempt is retried and a later completed attempt is used", () => {
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: GOVERNED_ADVISORY, stderr: "connection reset", status: 1 }
        : { stdout: `${GOVERNED_ADVISORY}\n${FALSE_POSITIVE_ADVISORY}\n${SUMMARY_LINE}`, stderr: "", status: 8 };
    },
  });
  assert.equal(calls, 2);
  assert.equal(result.complete, true);
  assert.match(result.stdout, /auditSummary/);
});

test("an outage is retried to the limit and then gives up incomplete", () => {
  let calls = 0;
  const result = run({
    attempts: 3,
    runAudit: () => {
      calls += 1;
      return { stdout: OUTAGE_STDOUT, stderr: "ResponseError: 504 Gateway Timeout", status: 1 };
    },
  });
  assert.equal(calls, 3);
  assert.equal(result.complete, false);
  assert.equal(auditOutputIsComplete(result.stdout), false);
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

test("each attempt is reported so an incomplete audit is legible in the CI log", () => {
  const { lines, log } = collector();
  runWithRetries({
    attempts: 2,
    backoffMs: 0,
    runAudit: () => ({ stdout: GOVERNED_ADVISORY, stderr: "connection reset", status: 1 }),
    sleep: () => {},
    log,
  });
  assert.match(lines[0], /attempt 1\/2: exit=1 completed=false/);
  assert.match(lines.join("\n"), /no auditSummary/);
});

test("the default attempt budget is the one the incident data justifies", () => {
  // Pinned deliberately: chosen from a measured ~38% per-attempt success rate
  // during the outage, not picked to make a build go green. Moving it should
  // mean new evidence, and should break this test.
  assert.match(runnerSource, /const DEFAULT_ATTEMPTS = 5;/);
  assert.match(runnerSource, /const DEFAULT_BACKOFF_MS = 5000;/);
  let calls = 0;
  run({
    runAudit: () => {
      calls += 1;
      return { stdout: "", stderr: "down", status: 1 };
    },
  });
  assert.equal(calls, 5);
});
