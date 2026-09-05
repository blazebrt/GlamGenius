const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const { advisoryLinesIn, auditOutputIsComplete, runWithRetries } = require("./run-node-audit");
const { validateAuditText } = require("./validate-node-audit");
const runnerSource = fs.readFileSync(`${__dirname}/run-node-audit.js`, "utf8");

// Frozen fixtures rather than the live registries, which are empty now that
// both governed advisories are remediated. The retry policy still has to be
// shown carrying a governed advisory forward, so it needs a governed advisory
// to carry. See node-audit-fixtures.js.
const {
  exceptionRegistry,
  falsePositiveRegistry: buildFalsePositiveRegistry,
} = require("./node-audit-fixtures");

const registry = exceptionRegistry();
const approved = registry.exceptions[0];
const falsePositiveRegistry = buildFalsePositiveRegistry();
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
  assert.match(lines.join("\n"), /incomplete \(no valid auditSummary\)/);
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


// ---- sticky advisory evidence ---------------------------------------------
//
// Retries recover completeness. They must never erase evidence. These drive the
// REAL validator with whatever the runner finally produces, because the only
// thing that matters is the verdict at the end of the pipe.

const TODAY = { today: "2026-08-16" };

function attemptsOf(...outputs) {
  let i = 0;
  return () => {
    const stdout = outputs[Math.min(i, outputs.length - 1)];
    i += 1;
    return { stdout, stderr: stdout ? "connection reset" : "504", status: stdout ? 1 : 1 };
  };
}

function gate(stdout) {
  return validateAuditText(stdout, registry, falsePositiveRegistry, TODAY);
}

test("advisoryLinesIn extracts advisories verbatim and ignores everything else", () => {
  const text = `${OUTAGE_STDOUT}\n${GOVERNED_ADVISORY}\nnot json\n${SUMMARY_LINE}`;
  assert.deepEqual(advisoryLinesIn(text), [GOVERNED_ADVISORY]);
  assert.deepEqual(advisoryLinesIn(""), []);
  assert.deepEqual(advisoryLinesIn(SUMMARY_LINE), []);
});

test("J: an unknown HIGH seen on a partial attempt is NOT erased by a later clean audit", () => {
  // The load-bearing regression. Attempt 1 reports a real vulnerability and
  // dies; attempt 2 completes clean. Before sticky evidence the HIGH vanished
  // with attempt 1 and the gate passed on a finding it had been told about.
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: UNKNOWN_HIGH_ADVISORY, stderr: "connection reset", status: 1 }
        : { stdout: SUMMARY_LINE, stderr: "", status: 0 };
    },
  });
  assert.equal(calls, 2);
  assert.equal(result.complete, true);
  assert.match(result.stdout, /GHSA-zzzz-zzzz-zzzz/);
  assert.match(result.stdout, /auditSummary/);
  assert.throws(() => gate(result.stdout), /Unaccepted HIGH advisory GHSA-zzzz-zzzz-zzzz/);
});

test("K: a governed advisory seen on a partial attempt survives and is judged by the registry", () => {
  const result = run({
    attempts: 5,
    runAudit: attemptsOf(GOVERNED_ADVISORY, SUMMARY_LINE),
  });
  assert.equal(result.attempts, 2);
  assert.equal(result.complete, true);
  assert.match(result.stdout, new RegExp(approved.advisory_id));
  // Passes only because the registry explicitly governs it. The runner knows
  // nothing about that; it simply did not lose the evidence.
  const verdict = gate(result.stdout);
  assert.deepEqual(
    verdict.acceptedExceptions.map((e) => e.advisory_id),
    [approved.advisory_id],
  );
});

test("L: a false-positive advisory seen on a partial attempt survives and is judged by the registry", () => {
  const result = run({
    attempts: 5,
    runAudit: attemptsOf(FALSE_POSITIVE_ADVISORY, SUMMARY_LINE),
  });
  assert.equal(result.attempts, 2);
  assert.match(result.stdout, new RegExp(nanoidFalsePositive.advisory_id));
  const verdict = gate(result.stdout);
  assert.equal(verdict.falsePositiveFindings.length, 1);
  assert.equal(verdict.findings.length, 1);
});

test("M: advisories from several partial attempts all survive into the validator input", () => {
  const result = run({
    attempts: 5,
    runAudit: attemptsOf(GOVERNED_ADVISORY, FALSE_POSITIVE_ADVISORY, SUMMARY_LINE),
  });
  assert.equal(result.attempts, 3);
  assert.equal(result.carriedAdvisories, 2);
  assert.match(result.stdout, new RegExp(approved.advisory_id));
  assert.match(result.stdout, new RegExp(nanoidFalsePositive.advisory_id));
  const verdict = gate(result.stdout);
  assert.equal(verdict.acceptedExceptions.length, 1);
  assert.equal(verdict.falsePositiveFindings.length, 1);
});

test("evidence survives even when every attempt is incomplete", () => {
  // attempt 1 sees a real HIGH; attempts 2-5 are pure outage. The gate must
  // fail for incompleteness AND the finding must still be in the record.
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: UNKNOWN_HIGH_ADVISORY, stderr: "connection reset", status: 1 }
        : { stdout: OUTAGE_STDOUT, stderr: "ESOCKETTIMEDOUT", status: 1 };
    },
  });
  assert.equal(result.complete, false);
  assert.match(result.stdout, /GHSA-zzzz-zzzz-zzzz/);
  assert.doesNotMatch(result.stdout, /auditSummary/); // nothing manufactured
  // Fails closed. The trailing info record trips the record-type rule before
  // the completeness rule is reached -- either way the gate refuses, and the
  // finding is still in the record for whoever reads it.
  assert.throws(() => gate(result.stdout), /unsupported record type/);
});

test("all attempts incomplete with clean outage output fails specifically for incompleteness", () => {
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: UNKNOWN_HIGH_ADVISORY, stderr: "connection reset", status: 1 }
        : { stdout: "", stderr: "ESOCKETTIMEDOUT", status: 1 };
    },
  });
  assert.equal(result.complete, false);
  assert.match(result.stdout, /GHSA-zzzz-zzzz-zzzz/);
  assert.throws(() => gate(result.stdout), /did not run to completion/);
});

test("a first-attempt completed audit is handed over byte-for-byte", () => {
  const stdout = `${GOVERNED_ADVISORY}\n${SUMMARY_LINE}`;
  const result = run({ attempts: 5, runAudit: () => ({ stdout, stderr: "", status: 8 }) });
  assert.equal(result.attempts, 1);
  assert.equal(result.carriedAdvisories, 0);
  assert.equal(result.stdout, stdout);
});

test("an info-only outage carries nothing forward and recovers normally", () => {
  const result = run({ attempts: 5, runAudit: attemptsOf(OUTAGE_STDOUT, SUMMARY_LINE) });
  assert.equal(result.attempts, 2);
  assert.equal(result.carriedAdvisories, 0);
  assert.equal(result.stdout, SUMMARY_LINE);
  assert.equal(gate(result.stdout).findings.length, 0);
});

test("duplicate advisories across partial attempts are carried once", () => {
  const result = run({
    attempts: 5,
    runAudit: attemptsOf(GOVERNED_ADVISORY, GOVERNED_ADVISORY, SUMMARY_LINE),
  });
  assert.equal(result.carriedAdvisories, 1);
});

test("the runner reads neither security registry", () => {
  // Structural: the moment this file learns what an exception is, there are two
  // security authorities and they will disagree.
  // Asserted against what the file *does*, not what its comments say: the
  // prose deliberately names exceptions and expiry to explain what this file
  // leaves alone, and a test that forbade the words would forbid the
  // explanation too.
  const code = runnerSource
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  assert.doesNotMatch(code, /node-audit-exceptions/);
  assert.doesNotMatch(code, /node-audit-false-positives/);
  assert.doesNotMatch(code, /require\(["'][^"']*exception/i);
  assert.doesNotMatch(code, /severity|critical|expiry|false_positive/i);
  // The single import from the authority is the classification helper only.
  assert.match(code, /require\("\.\/validate-node-audit"\)/);
});


// ---- the runner agrees with the authority on what "finished" means ---------

test("a summary that is not the final record is not complete", () => {
  // Trailing content means this is not one tidy end-of-audit stream. The runner
  // retries rather than accepting it, and the validator would refuse it too.
  assert.equal(auditOutputIsComplete(`${SUMMARY_LINE}\n${GOVERNED_ADVISORY}`), false);
  assert.equal(auditOutputIsComplete(`${SUMMARY_LINE}\n${OUTAGE_STDOUT}`), false);
  assert.equal(auditOutputIsComplete(`${GOVERNED_ADVISORY}\n${SUMMARY_LINE}`), true);
});

test("two summaries are not complete", () => {
  assert.equal(auditOutputIsComplete(`${SUMMARY_LINE}\n${SUMMARY_LINE}`), false);
});

test("a summary with a malformed severity counter is not a completion marker", () => {
  for (const high of ["1", null, -1, 1.5, true, undefined]) {
    const line = JSON.stringify({
      type: "auditSummary",
      data: { vulnerabilities: { info: 0, low: 0, moderate: 0, critical: 0, high } },
    });
    assert.equal(auditOutputIsComplete(line), false, `high=${String(high)}`);
  }
});

test("a summary missing a canonical severity is not a completion marker", () => {
  const line = JSON.stringify({
    type: "auditSummary",
    data: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0 } },
  });
  assert.equal(auditOutputIsComplete(line), false);
});

test("carried advisories are prepended, so the summary stays the final record", () => {
  // The ordering invariant and the sticky evidence have to coexist: carrying a
  // finding forward must not itself break completeness.
  const result = run({
    attempts: 5,
    runAudit: attemptsOf(UNKNOWN_HIGH_ADVISORY, SUMMARY_LINE),
  });
  assert.equal(result.complete, true);
  assert.equal(auditOutputIsComplete(result.stdout), true);
  assert.match(result.stdout.trim().split("\n").pop(), /auditSummary/);
  assert.throws(() => gate(result.stdout), /Unaccepted HIGH advisory GHSA-zzzz-zzzz-zzzz/);
});


// ---- retry provenance: a carried finding cannot explain a later summary ----
//
// The masking defect. Attempt 1 reports a governed HIGH and dies; attempt 2
// returns a well-formed summary claiming high=1 but no HIGH advisory of its
// own. Concatenate the two and the combined stream looks like one coherent,
// substantiated, finished audit -- and because the carried HIGH is governed,
// the gate would pass on a scan that never completed. Sticky evidence is
// evidence that a finding EXISTS; it is not evidence about a later attempt.

function summaryClaiming(overrides) {
  return JSON.stringify({
    type: "auditSummary",
    data: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0, ...overrides } },
  });
}

const CRITICAL_ADVISORY = advisoryLine({
  advisoryId: "GHSA-crit-crit-crit",
  cve: "CVE-2099-0002",
  packageName: "critical-package",
  version: "2.0.0",
  severity: "critical",
  paths: ["app>critical-package"],
});

test("a summary claiming HIGH with no HIGH of its own is not a complete attempt", () => {
  assert.equal(auditOutputIsComplete(summaryClaiming({ high: 1 })), false);
  assert.equal(auditOutputIsComplete(summaryClaiming({ critical: 1 })), false);
  // Same-attempt detail is what substantiates it.
  assert.equal(auditOutputIsComplete(`${UNKNOWN_HIGH_ADVISORY}\n${summaryClaiming({ high: 1 })}`), true);
  // ...and the severities must actually match.
  assert.equal(auditOutputIsComplete(`${UNKNOWN_HIGH_ADVISORY}\n${summaryClaiming({ critical: 1 })}`), false);
});

test("A: a carried governed HIGH cannot complete a later detail-less summary", () => {
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      if (calls === 1) return { stdout: GOVERNED_ADVISORY, stderr: "reset", status: 1 };
      if (calls === 2) return { stdout: summaryClaiming({ high: 1 }), stderr: "", status: 8 };
      return { stdout: SUMMARY_LINE, stderr: "", status: 0 };
    },
  });
  // Attempt 2 must NOT have been accepted; the runner had to go on to 3.
  assert.equal(calls, 3);
  assert.equal(result.complete, true);
  // The carried governed advisory survived and is judged on its own merits.
  assert.match(result.stdout, new RegExp(approved.advisory_id));
  assert.equal(gate(result.stdout).acceptedExceptions.length, 1);
});

test("B: exhausted masking — a carried governed HIGH must not rescue an incomplete run", () => {
  // THE load-bearing case. Every attempt after the first claims high=1 with no
  // detail. Nothing ever completes, so the gate must refuse -- and must not be
  // talked round by the fact that the only HIGH it can see is an excepted one.
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: GOVERNED_ADVISORY, stderr: "reset", status: 1 }
        : { stdout: summaryClaiming({ high: 1 }), stderr: "", status: 8 };
    },
  });
  assert.equal(calls, 5);
  assert.equal(result.complete, false);
  // Evidence preserved, not discarded.
  assert.match(result.stdout, new RegExp(approved.advisory_id));
  assert.match(result.stdout, /auditSummary/);
  // And the gate refuses it.
  assert.throws(() => gate(result.stdout), /continues after its auditSummary/);
});

test("C: a carried false positive cannot substantiate a later summary either", () => {
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: FALSE_POSITIVE_ADVISORY, stderr: "reset", status: 1 }
        : { stdout: summaryClaiming({ high: 1 }), stderr: "", status: 8 };
    },
  });
  assert.equal(calls, 5);
  assert.equal(result.complete, false);
  assert.match(result.stdout, new RegExp(nanoidFalsePositive.advisory_id));
  assert.throws(() => gate(result.stdout), /continues after its auditSummary/);
});

test("D: carried HIGH does not substantiate critical, and carried CRITICAL does not substantiate a later one", () => {
  for (const carriedLine of [UNKNOWN_HIGH_ADVISORY, CRITICAL_ADVISORY]) {
    let calls = 0;
    const result = run({
      attempts: 3,
      runAudit: () => {
        calls += 1;
        return calls === 1
          ? { stdout: carriedLine, stderr: "reset", status: 1 }
          : { stdout: summaryClaiming({ critical: 1 }), stderr: "", status: 16 };
      },
    });
    assert.equal(calls, 3, "a detail-less critical summary must never complete");
    assert.equal(result.complete, false);
    assert.throws(() => gate(result.stdout), /continues after its auditSummary/);
  }
});

test("E: a same-attempt non-zero audit completes and is evaluated normally", () => {
  // Guards against over-tightening: real audits report non-zero counts and are
  // perfectly complete when they carry their own detail.
  const stdout = `${GOVERNED_ADVISORY}\n${FALSE_POSITIVE_ADVISORY}\n${summaryClaiming({ high: 3 })}`;
  const result = run({ attempts: 5, runAudit: () => ({ stdout, stderr: "", status: 8 }) });
  assert.equal(result.attempts, 1);
  assert.equal(result.complete, true);
  assert.equal(result.stdout, stdout, "nothing carried, so byte-for-byte");
  const verdict = gate(result.stdout);
  assert.equal(verdict.acceptedExceptions.length, 1);
  assert.equal(verdict.falsePositiveFindings.length, 1);
});

test("F: the sticky zero-count asymmetry is unchanged", () => {
  let calls = 0;
  const result = run({
    attempts: 5,
    runAudit: () => {
      calls += 1;
      return calls === 1
        ? { stdout: UNKNOWN_HIGH_ADVISORY, stderr: "reset", status: 1 }
        : { stdout: SUMMARY_LINE, stderr: "", status: 0 };
    },
  });
  assert.equal(calls, 2);
  assert.equal(result.complete, true, "a clean zero-count summary is still a real answer");
  assert.throws(() => gate(result.stdout), /Unaccepted HIGH advisory GHSA-zzzz-zzzz-zzzz/);
});

test("completion is judged before carrying, never after", () => {
  // Structural: the raw attempt is what gets tested for completeness.
  const code = runnerSource.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.match(code, /const completion = auditStreamCompletion\(last\.stdout\);/);
  assert.doesNotMatch(code, /auditStreamCompletion\([^)]*[Cc]arried/);
  assert.doesNotMatch(code, /auditOutputIsComplete\([^)]*[Cc]arried/);
});
