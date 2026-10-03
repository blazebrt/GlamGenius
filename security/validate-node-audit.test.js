const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const {
  formatPass,
  validateAuditText: rawValidateAuditText,
  validateFalsePositiveRegistry,
  validateRegistry,
} = require("./validate-node-audit");

// Frozen fixtures rather than the live registries. The earlier image-size and
// Nano ID findings were remediated, so their validator rules still need
// fixtures independent of today's separate node-forge and braces exceptions.
// See node-audit-fixtures.js.
const {
  exceptionRegistry,
  falsePositiveRegistry: buildFalsePositiveRegistry,
} = require("./node-audit-fixtures");

const registry = exceptionRegistry();
const approved = registry.exceptions[0];
const falsePositiveRegistry = buildFalsePositiveRegistry();
const nanoidFalsePositive = falsePositiveRegistry.false_positives[0];

// The registries the gate actually reads, for the tests that assert on their
// shipped contents rather than on validator behaviour.
const shippedExceptionRegistry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-exceptions.json`, "utf8"),
);
const shippedFalsePositiveRegistry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-false-positives.json`, "utf8"),
);

test("Yarn is the sole committed frontend dependency authority", () => {
  const root = path.resolve(__dirname, "..");
  const frontend = path.join(root, "frontend");
  const manifest = JSON.parse(fs.readFileSync(path.join(frontend, "package.json"), "utf8"));
  assert.match(manifest.packageManager, /^yarn@/);
  assert.equal(fs.existsSync(path.join(frontend, "yarn.lock")), true);
  assert.equal(fs.existsSync(path.join(frontend, "package-lock.json")), false);
  assert.match(fs.readFileSync(path.join(root, ".gitignore"), "utf8"), /^\/frontend\/package-lock\.json$/m);
});

function auditAdvisory({
  advisoryId,
  cve,
  packageName,
  version,
  severity = "high",
  paths = ["unknown>path"],
}) {
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

function cleanAudit(overrides = {}) {
  return JSON.stringify({
    type: "auditSummary",
    data: {
      // All five canonical counters, as Yarn Classic actually emits them.
      vulnerabilities: { info: 0, low: 0, moderate: 0, high: 0, critical: 0, ...overrides },
      dependencies: 1200,
    },
  });
}

/** A summary claiming findings, for the "counts but no detail" contradiction. */
function summaryClaiming(overrides) {
  return cleanAudit(overrides);
}

// Every test that passes advisory text is about advisory *content*, and a real
// audit always ends with a summary record. Fixtures therefore get one appended
// so those tests exercise the content rules rather than tripping over the
// completeness rule. Completeness has its own tests, at the bottom of this
// file, which call the validator directly with no summary added.
function validateAuditText(auditText, ...rest) {
  const text = auditText.includes('"auditSummary"')
    ? auditText
    : `${auditText}\n${cleanAudit()}`;
  return rawValidateAuditText(text, ...rest);
}

function nanoidAudit(overrides = {}) {
  return auditAdvisory({
    advisoryId: nanoidFalsePositive.advisory_id,
    cve: nanoidFalsePositive.cve,
    packageName: nanoidFalsePositive.package,
    version: nanoidFalsePositive.installed_version,
    paths: nanoidFalsePositive.dependency_paths,
    ...overrides,
  });
}

test("clean audit passes with no exceptions", () => {
  const result = validateAuditText(cleanAudit(), registry, { today: "2026-08-08" });
  assert.equal(result.findings.length, 0);
  assert.match(formatPass(result), /Unaccepted HIGH: 0/);
  assert.match(formatPass(result), /Known scanner false positives: 0/);
});

test("exact approved image-size advisory passes with an exception report", () => {
  const audit = auditAdvisory({
    advisoryId: approved.advisory_id,
    cve: approved.cve,
    packageName: approved.package,
    version: approved.installed_version,
    paths: approved.dependency_paths,
  });
  const result = validateAuditText(audit, registry, { today: "2026-08-08" });
  assert.deepEqual(result.acceptedExceptions.map((exception) => exception.advisory_id), [approved.advisory_id]);
  assert.match(formatPass(result), /Accepted temporary exceptions: 1/);
  assert.match(formatPass(result), /Known scanner false positives: 0/);
});

test("unknown HIGH advisory fails", () => {
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: "GHSA-unknown-high",
          cve: "CVE-2099-0001",
          packageName: "other-package",
          version: "1.0.0",
        }),
        registry,
        { today: "2026-08-08" },
      ),
    /Unaccepted HIGH advisory GHSA-unknown-high/,
  );
});

test("unknown CRITICAL advisory fails", () => {
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: "GHSA-unknown-critical",
          cve: "CVE-2099-0002",
          packageName: "other-package",
          version: "1.0.0",
          severity: "critical",
        }),
        registry,
        { today: "2026-08-08" },
      ),
    /Unaccepted CRITICAL advisory GHSA-unknown-critical/,
  );
});

test("same package with a different advisory fails", () => {
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: "GHSA-different-image-size",
          cve: "CVE-2099-0003",
          packageName: "image-size",
          version: "1.2.1",
          paths: approved.dependency_paths,
        }),
        registry,
        { today: "2026-08-08" },
      ),
    /Unaccepted HIGH advisory GHSA-different-image-size/,
  );
});

test("same advisory with a different package version fails", () => {
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: approved.advisory_id,
          cve: approved.cve,
          packageName: approved.package,
          version: "1.2.2",
          paths: approved.dependency_paths,
        }),
        registry,
        { today: "2026-08-08" },
      ),
    /does not match installed version/,
  );
});

test("expired exception fails on the expiry boundary", () => {
  const expiredRegistry = JSON.parse(JSON.stringify(registry));
  expiredRegistry.exceptions[0].created_date = "2026-08-06";
  expiredRegistry.exceptions[0].review_date = "2026-08-07";
  expiredRegistry.exceptions[0].expiry_date = "2026-08-08";
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: approved.advisory_id,
          cve: approved.cve,
          packageName: approved.package,
          version: approved.installed_version,
          paths: approved.dependency_paths,
        }),
        expiredRegistry,
        { today: "2026-08-08" },
      ),
    /expired on 2026-08-08/,
  );
});

test("malformed exception registry fails", () => {
  const malformed = { schema_version: 1, exceptions: [{}] };
  assert.throws(() => validateRegistry(malformed), /missing required field advisory_id/);
});

test("missing owner, date, or removal condition fails", () => {
  for (const field of ["owner", "created_date", "review_date", "expiry_date", "removal_condition"]) {
    const incomplete = JSON.parse(JSON.stringify(registry));
    delete incomplete.exceptions[0][field];
    assert.throws(() => validateRegistry(incomplete), new RegExp(`missing required field ${field}`));
  }
});

test("exact real Nano ID finding passes specifically as a scanner false positive", () => {
  const result = validateAuditText(
    nanoidAudit(),
    registry,
    falsePositiveRegistry,
    { today: "2026-08-16" },
  );
  assert.equal(result.acceptedExceptions.length, 0);
  assert.deepEqual(result.falsePositiveFindings.map((finding) => finding.advisory_id), [nanoidFalsePositive.advisory_id]);
  assert.match(formatPass(result), /Accepted temporary exceptions: 0/);
  assert.match(formatPass(result), /Known scanner false positives: 1/);
  assert.match(formatPass(result), /scanner false positive/);
});

test("Nano ID 3.3.16 with the real GHSA fails closed", () => {
  assert.throws(
    () =>
      validateAuditText(
        nanoidAudit({ version: "3.3.16" }),
        registry,
        falsePositiveRegistry,
        { today: "2026-08-16" },
      ),
    /does not match installed version/,
  );
});

test("another Nano ID version fails closed", () => {
  assert.throws(
    () => validateAuditText(nanoidAudit({ version: "3.3.18" }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /does not match installed version/,
  );
});

test("same Nano ID version with another HIGH advisory fails", () => {
  assert.throws(
    () => validateAuditText(auditAdvisory({
      advisoryId: "GHSA-other-nanoid-advisory", cve: "CVE-2099-0017", packageName: "nanoid",
      version: "3.3.17", paths: nanoidFalsePositive.dependency_paths,
    }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /Unaccepted HIGH advisory GHSA-other-nanoid-advisory/,
  );
});

test("same GHSA and version with another package fails", () => {
  assert.throws(
    () => validateAuditText(auditAdvisory({
      advisoryId: nanoidFalsePositive.advisory_id, cve: nanoidFalsePositive.cve, packageName: "other-package",
      version: nanoidFalsePositive.installed_version, paths: nanoidFalsePositive.dependency_paths,
    }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /does not match advisory\/package\/severity evidence/,
  );
});

test("wrong Nano ID CVE fails when CVE evidence is supplied", () => {
  assert.throws(
    () => validateAuditText(nanoidAudit({ cve: "CVE-2099-0017" }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /does not match reported CVE/,
  );
});

test("wrong Nano ID severity fails", () => {
  assert.throws(
    () => validateAuditText(nanoidAudit({ severity: "critical" }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /does not match advisory\/package\/severity evidence/,
  );
});

test("unexpected Nano ID dependency path fails", () => {
  assert.throws(
    () => validateAuditText(nanoidAudit({ paths: [...nanoidFalsePositive.dependency_paths, "unexpected>path"] }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /does not allow the observed dependency paths/,
  );
});

test("expired false-positive record fails", () => {
  const expired = JSON.parse(JSON.stringify(falsePositiveRegistry));
  expired.false_positives[0].created_date = "2026-08-14";
  expired.false_positives[0].review_date = "2026-08-15";
  expired.false_positives[0].expiry_date = "2026-08-16";
  assert.throws(
    () => validateAuditText(nanoidAudit(), registry, expired, { today: "2026-08-16" }),
    /Scanner false-positive record GHSA-2v37-7h3g-55p8 expired/,
  );
});

test("malformed false-positive registry fails", () => {
  assert.throws(
    () => validateFalsePositiveRegistry({ schema_version: 1, false_positives: [{}] }),
    /missing required field advisory_id/,
  );
});

test("duplicate false-positive advisory records fail", () => {
  const duplicate = JSON.parse(JSON.stringify(falsePositiveRegistry));
  duplicate.false_positives.push(JSON.parse(JSON.stringify(duplicate.false_positives[0])));
  assert.throws(() => validateFalsePositiveRegistry(duplicate), /at most the approved Nano ID record/);
});

test("missing false-positive provenance fields fail", () => {
  const incomplete = JSON.parse(JSON.stringify(falsePositiveRegistry));
  delete incomplete.false_positives[0].authoritative_source;
  assert.throws(() => validateFalsePositiveRegistry(incomplete), /missing required field authoritative_source/);
});

test("false-positive and accepted-exception output remain distinct", () => {
  const audit = [
    auditAdvisory({
      advisoryId: approved.advisory_id,
      cve: approved.cve,
      packageName: approved.package,
      version: approved.installed_version,
      paths: approved.dependency_paths,
    }),
    nanoidAudit(),
  ].join("\n");
  const result = validateAuditText(audit, registry, falsePositiveRegistry, { today: "2026-08-16" });
  assert.equal(result.acceptedExceptions.length, 1);
  assert.equal(result.falsePositiveFindings.length, 1);
  const formatted = formatPass(result);
  assert.match(formatted, /Accepted temporary exceptions: 1/);
  assert.match(formatted, /Known scanner false positives: 1/);
});

test("existing image-size dependency-path contract remains strict", () => {
  assert.throws(
    () => validateAuditText(auditAdvisory({
      advisoryId: approved.advisory_id, cve: approved.cve, packageName: approved.package,
      version: approved.installed_version, paths: [...approved.dependency_paths, "unexpected>path"],
    }), registry, { today: "2026-08-16" }),
    /does not allow dependency path/,
  );
});

// ---- The remediated state -------------------------------------------------
// September 2026: metro 0.83.8 dropped its `image-size` dependency and the
// nanoid resolution moved to the patched 3.3.18. October's real node-forge
// and braces HIGHs are separately governed below; the false-positive registry
// remains empty.

test("the shipped registries govern exactly node-forge and braces", () => {
  assert.deepEqual(
    validateRegistry(shippedExceptionRegistry).map((exception) => exception.advisory_id),
    ["GHSA-86w9-cpqp-85rv", "GHSA-vfj7-8cjw-p6xm"],
  );
  assert.deepEqual(validateFalsePositiveRegistry(shippedFalsePositiveRegistry), []);
});

test("a clean audit passes against the shipped registries with nothing governed", () => {
  const result = validateAuditText(
    cleanAudit(),
    shippedExceptionRegistry,
    shippedFalsePositiveRegistry,
    { today: "2026-09-05" },
  );
  assert.equal(result.findings.length, 0);
  assert.equal(result.acceptedExceptions.length, 0);
  assert.equal(result.falsePositiveFindings.length, 0);
  assert.equal(result.reviewWarnings.length, 0);
});

test("removing image-size exceptions does not open the gate to image-size findings", () => {
  // The whole point of removing them is that the package is gone. If it ever
  // comes back, an ungoverned HIGH against it must fail, not pass quietly.
  assert.throws(
    () => validateAuditText(
      auditAdvisory({
        advisoryId: approved.advisory_id,
        cve: approved.cve,
        packageName: approved.package,
        version: approved.installed_version,
        paths: approved.dependency_paths,
      }),
      shippedExceptionRegistry,
      shippedFalsePositiveRegistry,
      { today: "2026-09-05" },
    ),
    /Unaccepted HIGH advisory GHSA-w3rx-r6r6-pgpr/,
  );
});

test("removing the Nano ID record does not open the gate to Nano ID findings", () => {
  assert.throws(
    () => validateAuditText(
      nanoidAudit(),
      shippedExceptionRegistry,
      shippedFalsePositiveRegistry,
      { today: "2026-09-05" },
    ),
    /Unaccepted HIGH advisory GHSA-2v37-7h3g-55p8/,
  );
});

test("image-size exceptions come as the approved pair or not at all", () => {
  // Both advisories are against the same package, so no remediation can clear
  // one and leave the other. Half a pair means the registry was edited by
  // hand, which is exactly what the allowlist exists to catch.
  for (const kept of [0, 1]) {
    assert.throws(
      () => validateRegistry({
        schema_version: 1,
        exceptions: [registry.exceptions[kept]],
      }),
      /either no image-size advisories or exactly the two approved ones/,
    );
  }
});

test("an unapproved image-size advisory is still refused, empty registry or not", () => {
  const smuggled = {
    ...JSON.parse(JSON.stringify(approved)),
    advisory_id: "GHSA-0000-0000-0000",
    cve: "CVE-2026-00000",
  };
  assert.throws(
    () => validateRegistry({ schema_version: 1, exceptions: [smuggled] }),
    /unapproved image-size advisory/,
  );
  assert.throws(
    () => validateRegistry({
      schema_version: 1,
      exceptions: [...JSON.parse(JSON.stringify(registry.exceptions)), smuggled],
    }),
    /unapproved image-size advisory/,
  );
});

test("the approved image-size pair still validates, so the fixture is a real contract", () => {
  assert.equal(validateRegistry(exceptionRegistry()).length, 2);
});

test("missing HIGH audit path evidence fails closed", () => {
  assert.throws(
    () => validateAuditText(JSON.stringify({
      type: "auditAdvisory",
      data: { advisory: {
        cves: [nanoidFalsePositive.cve], github_advisory_id: nanoidFalsePositive.advisory_id,
        module_name: nanoidFalsePositive.package, severity: nanoidFalsePositive.severity,
        findings: [{ version: nanoidFalsePositive.installed_version, paths: [] }],
      } },
    }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /without dependency paths/,
  );
});


// ---- audit completeness: the scan must prove it finished --------------------
//
// These call the validator directly, with no summary appended, because the
// absence of a summary is exactly what is under test.

test("an audit with no auditSummary fails closed", () => {
  assert.throws(
    () => rawValidateAuditText(cleanAudit().replace("auditSummary", "auditAdvisory"), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /missing advisory data/,
  );
});

test("C: a governed advisory WITHOUT a summary fails as an incomplete audit", () => {
  // The defect. These advisories are individually acceptable, so before the
  // completeness rule this partial stream reported PASS -- clearing every
  // dependency the crashed scan never reached.
  const audit = auditAdvisory({
    advisoryId: approved.advisory_id,
    cve: approved.cve,
    packageName: approved.package,
    version: approved.installed_version,
    paths: approved.dependency_paths,
  });
  assert.throws(
    () => rawValidateAuditText(audit, registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /did not run to completion/,
  );
});

test("D: a false-positive advisory WITHOUT a summary fails as an incomplete audit", () => {
  assert.throws(
    () => rawValidateAuditText(nanoidAudit(), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /did not run to completion/,
  );
});

test("E: the full known partial stream WITHOUT a summary must not pass", () => {
  const audit = [
    auditAdvisory({
      advisoryId: approved.advisory_id,
      cve: approved.cve,
      packageName: approved.package,
      version: approved.installed_version,
      paths: approved.dependency_paths,
    }),
    nanoidAudit(),
  ].join("\n");
  assert.throws(
    () => rawValidateAuditText(audit, registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /did not run to completion/,
  );
});

test("F: an unknown HIGH WITHOUT a summary fails, and for incompleteness", () => {
  assert.throws(
    () => rawValidateAuditText(auditAdvisory({
      advisoryId: "GHSA-unknown-partial", cve: "CVE-2099-0031",
      packageName: "other-package", version: "1.0.0",
    }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /did not run to completion/,
  );
});

test("A: a summary alone with no findings passes", () => {
  const result = rawValidateAuditText(cleanAudit(), registry, falsePositiveRegistry, { today: "2026-08-16" });
  assert.equal(result.findings.length, 0);
  assert.match(formatPass(result), /Unaccepted HIGH: 0/);
});

test("B: governed advisories plus a summary are evaluated normally", () => {
  const audit = [
    auditAdvisory({
      advisoryId: approved.advisory_id,
      cve: approved.cve,
      packageName: approved.package,
      version: approved.installed_version,
      paths: approved.dependency_paths,
    }),
    nanoidAudit(),
    cleanAudit(),
  ].join("\n");
  const result = rawValidateAuditText(audit, registry, falsePositiveRegistry, { today: "2026-08-16" });
  assert.deepEqual(
    result.acceptedExceptions.map((exception) => exception.advisory_id),
    [approved.advisory_id],
  );
  assert.equal(result.falsePositiveFindings.length, 1);
});

test("H: a completed unaccepted HIGH still fails on the finding, not on completeness", () => {
  assert.throws(
    () => rawValidateAuditText([
      auditAdvisory({
        advisoryId: "GHSA-unknown-complete", cve: "CVE-2099-0032",
        packageName: "other-package", version: "1.0.0",
      }),
      cleanAudit(),
    ].join("\n"), registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /Unaccepted HIGH advisory GHSA-unknown-complete/,
  );
});

test("a malformed summary is rejected rather than counted as completion", () => {
  for (const data of [null, "summary", [], { vulnerabilities: null }, { vulnerabilities: [] }, {}]) {
    assert.throws(
      () => rawValidateAuditText(JSON.stringify({ type: "auditSummary", data }), registry, falsePositiveRegistry, { today: "2026-08-16" }),
      /missing vulnerability data/,
    );
  }
});

test("more than one auditSummary is rejected", () => {
  assert.throws(
    () => rawValidateAuditText(`${cleanAudit()}\n${cleanAudit()}`, registry, falsePositiveRegistry, { today: "2026-08-16" }),
    /expected exactly one/,
  );
});


// ---- summary integrity: the completion marker must be worth trusting -------

const CANONICAL = ["info", "low", "moderate", "high", "critical"];

function summaryWith(vulnerabilities) {
  return JSON.stringify({ type: "auditSummary", data: { vulnerabilities } });
}

function gate(text) {
  return rawValidateAuditText(text, registry, falsePositiveRegistry, { today: "2026-08-16" });
}

test("A: an empty vulnerabilities object is not a valid summary", () => {
  assert.throws(() => gate(summaryWith({})), /missing vulnerability data/);
});

test("B: a summary missing any one canonical severity is rejected", () => {
  for (const omitted of CANONICAL) {
    const counts = {};
    for (const severity of CANONICAL) if (severity !== omitted) counts[severity] = 0;
    assert.throws(
      () => gate(summaryWith(counts)),
      /missing vulnerability data/,
      `omitting ${omitted} should be rejected`,
    );
  }
});

test("C/D/E/F: non-integer, negative, null and stringly counters are rejected", () => {
  const bad = ["1", null, -1, 1.5, true, [], {}, NaN, Infinity, undefined];
  for (const value of bad) {
    const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, high: value };
    assert.throws(
      () => gate(summaryWith({ ...counts, high: value })),
      /missing vulnerability data/,
      `high=${String(value)} should be rejected`,
    );
    assert.throws(
      () => gate(summaryWith({ info: 0, low: 0, moderate: 0, high: 0, critical: value })),
      /missing vulnerability data/,
      `critical=${String(value)} should be rejected`,
    );
  }
});

test("a well-formed summary with non-zero counts and matching detail is accepted", () => {
  // Guards against over-tightening: real audits do report non-zero counts.
  const audit = [
    auditAdvisory({
      advisoryId: approved.advisory_id,
      cve: approved.cve,
      packageName: approved.package,
      version: approved.installed_version,
      paths: approved.dependency_paths,
    }),
    cleanAudit({ high: 2 }),
  ].join("\n");
  assert.equal(gate(audit).acceptedExceptions.length, 1);
});

test("G: a summary counting a HIGH with no HIGH advisory detail fails closed", () => {
  assert.throws(
    () => gate(summaryClaiming({ high: 1 })),
    /reports 1 HIGH .* no HIGH advisory record/s,
  );
});

test("H: a summary counting a CRITICAL with no CRITICAL advisory detail fails closed", () => {
  assert.throws(
    () => gate(summaryClaiming({ critical: 1 })),
    /reports 1 CRITICAL .* no CRITICAL advisory record/s,
  );
});

test("a HIGH count is not satisfied by a CRITICAL advisory, or vice versa", () => {
  const high = auditAdvisory({
    advisoryId: approved.advisory_id, cve: approved.cve, packageName: approved.package,
    version: approved.installed_version, paths: approved.dependency_paths,
  });
  assert.throws(() => gate(`${high}\n${cleanAudit({ high: 1, critical: 1 })}`), /no CRITICAL advisory record/s);
});

test("I: a summary followed by an advisory is not a completed stream", () => {
  const audit = [
    cleanAudit(),
    auditAdvisory({
      advisoryId: approved.advisory_id, cve: approved.cve, packageName: approved.package,
      version: approved.installed_version, paths: approved.dependency_paths,
    }),
  ].join("\n");
  assert.throws(() => gate(audit), /continues after its auditSummary/);
});

test("J: a summary followed by an info record is not a completed clean audit", () => {
  const info = JSON.stringify({ type: "info", data: "done" });
  // The unsupported-record rule catches this one first; either way it is refused
  // and never read as a clean finished audit.
  assert.throws(() => gate(`${cleanAudit()}\n${info}`), /unsupported record type/);
});

test("the sticky asymmetry survives: zero counts do not discard a carried finding", () => {
  // A HIGH carried from an earlier incomplete attempt, alongside a later clean
  // summary that never saw it. The finding must still be judged, not dropped.
  const carried = auditAdvisory({
    advisoryId: "GHSA-carried-high", cve: "CVE-2099-0044",
    packageName: "other-package", version: "1.0.0",
  });
  assert.throws(
    () => gate(`${carried}\n${cleanAudit()}`),
    /Unaccepted HIGH advisory GHSA-carried-high/,
  );
});

test("a carried governed finding with a zero-count summary is still governed normally", () => {
  const carried = auditAdvisory({
    advisoryId: approved.advisory_id, cve: approved.cve, packageName: approved.package,
    version: approved.installed_version, paths: approved.dependency_paths,
  });
  const result = gate(`${carried}\n${cleanAudit()}`);
  assert.deepEqual(
    result.acceptedExceptions.map((e) => e.advisory_id),
    [approved.advisory_id],
  );
});

// ---- GHSA-86w9-cpqp-85rv: the governed node-forge exception -----------------
//
// node-forge 1.4.0 (CVE-2026-85393, HIGH) has no patched release. It is
// accepted as a temporary exception -- a real vulnerability, not a scanner
// false positive -- because it is reachable only inside Expo CLI build tooling.
// These tests run against the registries the gate actually reads, and pin
// that the shipped record accepts exactly the real finding and nothing next to
// it. Delete this section together with the exception once it is remediated.

const NODE_FORGE = {
  advisoryId: "GHSA-86w9-cpqp-85rv",
  cve: "CVE-2026-85393",
  packageName: "node-forge",
  version: "1.4.0",
  severity: "high",
  paths: [
    "expo>@expo/cli>node-forge",
    "expo>@expo/cli>@expo/code-signing-certificates>node-forge",
  ],
};
const NODE_FORGE_DAY = "2026-10-02";

function shippedNodeForgeException() {
  return shippedExceptionRegistry.exceptions.find(
    (exception) => exception.advisory_id === NODE_FORGE.advisoryId,
  );
}

// Yarn Classic writes one auditAdvisory per resolution path, and each record's
// findings list every vulnerable path for that version. The real CI stream has
// exactly this shape: two HIGH records followed by the summary.
function nodeForgeAudit(overrides = {}) {
  const finding = { ...NODE_FORGE, ...overrides };
  return finding.paths.map((resolutionPath) => JSON.stringify({
    type: "auditAdvisory",
    data: {
      resolution: { id: 1240912, path: resolutionPath, dev: false, optional: false, bundled: false },
      advisory: {
        cves: [finding.cve],
        findings: [{ version: finding.version, paths: finding.paths }],
        github_advisory_id: finding.advisoryId,
        module_name: finding.packageName,
        severity: finding.severity,
      },
    },
  })).join("\n");
}

function completedNodeForgeAudit(overrides = {}) {
  const severity = overrides.severity || NODE_FORGE.severity;
  const paths = overrides.paths || NODE_FORGE.paths;
  return `${nodeForgeAudit(overrides)}\n${cleanAudit({ low: 1, moderate: 37, [severity]: paths.length })}`;
}

function shippedRegistryWith(mutate) {
  const copy = JSON.parse(JSON.stringify(shippedExceptionRegistry));
  mutate(copy.exceptions.find((exception) => exception.advisory_id === NODE_FORGE.advisoryId), copy);
  return copy;
}

test("node-forge: the shipped record is exactly the CI finding, with the proven reachability", () => {
  const exception = shippedNodeForgeException();
  assert.ok(exception, "the shipped registry must govern GHSA-86w9-cpqp-85rv");
  assert.equal(shippedExceptionRegistry.exceptions.length, 2);
  assert.equal(exception.cve, NODE_FORGE.cve);
  assert.equal(exception.package, NODE_FORGE.packageName);
  assert.equal(exception.installed_version, NODE_FORGE.version);
  assert.equal(exception.severity, NODE_FORGE.severity);
  assert.deepEqual(exception.dependency_paths, NODE_FORGE.paths);
  assert.equal(exception.production_runtime_reachable, false);
  assert.equal(exception.production_user_input_reachable, false);
  assert.equal(exception.build_ci_reachable, true);
  // Short-lived by construction: review in 7 days, expiry in 14.
  assert.deepEqual(
    [exception.created_date, exception.review_date, exception.expiry_date],
    ["2026-10-02", "2026-10-09", "2026-10-16"],
  );
  assert.match(exception.upstream_tracking, /digitalbazaar\/forge#1152/);
  assert.match(exception.reason, /not a scanner false positive/);
  for (const statement of [
    "not imported by GlamGenius application source",
    "not bundled into the shipped Android application JS or the shipped web application JS",
    "GlamGenius does not install expo-updates",
    "No production or user-controlled certificate, RSA public key or signature reaches the affected verifier",
  ]) {
    assert.ok(exception.reachability_assessment.includes(statement), `assessment must state: ${statement}`);
  }
  // It is governed as an accepted exception, never as a false positive.
  assert.equal(shippedFalsePositiveRegistry.false_positives.length, 0);
});

test("node-forge: the exact real finding is accepted only as a visible temporary exception", () => {
  const result = rawValidateAuditText(
    completedNodeForgeAudit(),
    shippedExceptionRegistry,
    shippedFalsePositiveRegistry,
    { today: NODE_FORGE_DAY },
  );
  assert.deepEqual(result.acceptedExceptions.map((exception) => exception.advisory_id), [NODE_FORGE.advisoryId]);
  assert.equal(result.falsePositiveFindings.length, 0);
  assert.equal(result.findings.length, 1);
  assert.deepEqual([...result.findings[0].paths].sort(), [...NODE_FORGE.paths].sort());
  assert.deepEqual(result.reviewWarnings, []);
  const formatted = formatPass(result);
  assert.match(formatted, /^Node security gate PASS$/m);
  assert.match(formatted, /^Unaccepted HIGH: 0$/m);
  assert.match(formatted, /^Unaccepted CRITICAL: 0$/m);
  assert.match(formatted, /^Accepted temporary exceptions: 1$/m);
  assert.match(formatted, /^Known scanner false positives: 0$/m);
  assert.match(formatted, /^GHSA-86w9-cpqp-85rv$/m);
  assert.match(formatted, /^node-forge@1\.4\.0$/m);
  assert.match(formatted, /^expires 2026-10-16$/m);
});

test("node-forge 1: same package with a different advisory fails", () => {
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ advisoryId: "GHSA-0000-node-forge-other", cve: "CVE-2099-0101" }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /Unaccepted HIGH advisory GHSA-0000-node-forge-other for node-forge \(1\.4\.0\)/,
  );
});

test("node-forge 2: same advisory against a different package fails", () => {
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ packageName: "node-forge-fork" }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /Unaccepted HIGH advisory GHSA-86w9-cpqp-85rv for node-forge-fork/,
  );
});

test("node-forge 3: same advisory with a different installed version fails", () => {
  for (const version of ["1.3.3", "1.4.1"]) {
    assert.throws(
      () => rawValidateAuditText(
        completedNodeForgeAudit({ version }),
        shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
      ),
      new RegExp(`does not match installed version\\(s\\): ${version.replace(/\./g, "\\.")}`),
    );
  }
  // A second copy at another version beside 1.4.0 is drift too.
  const mixed = `${nodeForgeAudit()}\n${nodeForgeAudit({ version: "1.3.3" })}\n${cleanAudit({ high: 4 })}`;
  assert.throws(
    () => rawValidateAuditText(mixed, shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY }),
    /does not match installed version/,
  );
});

test("node-forge 4: an additional dependency path fails", () => {
  const paths = [...NODE_FORGE.paths, "expo>@expo/cli>@expo/devcert>node-forge"];
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ paths }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /does not allow dependency path expo>@expo\/cli>@expo\/devcert>node-forge/,
  );
  // A path reaching node-forge from the application itself is refused the same way.
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ paths: ["node-forge"] }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /does not allow dependency path node-forge/,
  );
});

test("node-forge 5: the same advisory reported as CRITICAL fails", () => {
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ severity: "critical" }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /Unaccepted CRITICAL advisory GHSA-86w9-cpqp-85rv for node-forge/,
  );
});

test("node-forge: a substituted CVE fails", () => {
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit({ cve: "CVE-2026-33894" }),
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /does not match reported CVE/,
  );
});

test("node-forge 6: the exception warns from review_date and fails from expiry_date", () => {
  const review = rawValidateAuditText(
    completedNodeForgeAudit(),
    shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: "2026-10-09" },
  );
  assert.deepEqual(review.reviewWarnings, [
    "SECURITY EXCEPTION REVIEW DUE: GHSA-86w9-cpqp-85rv review_date=2026-10-09 expiry_date=2026-10-16",
  ]);
  assert.match(formatPass(review), /SECURITY EXCEPTION REVIEW DUE: GHSA-86w9-cpqp-85rv/);
  for (const today of ["2026-10-16", "2026-10-17", "2027-01-01"]) {
    assert.throws(
      () => rawValidateAuditText(
        completedNodeForgeAudit(),
        shippedExceptionRegistry, shippedFalsePositiveRegistry, { today },
      ),
      /Security exception GHSA-86w9-cpqp-85rv expired on 2026-10-16/,
    );
  }
});

test("node-forge 7: an exception missing any reachability field fails", () => {
  for (const field of [
    "reachability_assessment",
    "production_runtime_reachable",
    "production_user_input_reachable",
    "build_ci_reachable",
    "compensating_controls",
    "upstream_tracking",
    "removal_condition",
  ]) {
    const incomplete = shippedRegistryWith((exception) => { delete exception[field]; });
    assert.throws(() => validateRegistry(incomplete), new RegExp(`missing required field ${field}`));
    assert.throws(
      () => rawValidateAuditText(completedNodeForgeAudit(), incomplete, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY }),
      new RegExp(`missing required field ${field}`),
    );
  }
  for (const field of ["production_runtime_reachable", "production_user_input_reachable", "build_ci_reachable"]) {
    const stringly = shippedRegistryWith((exception) => { exception[field] = "false"; });
    assert.throws(() => validateRegistry(stringly), new RegExp(`${field} must be boolean`));
  }
  const blank = shippedRegistryWith((exception) => { exception.reachability_assessment = ""; });
  assert.throws(() => validateRegistry(blank), /reachability_assessment must be non-empty/);
});

test("node-forge 8: recording it in the false-positive registry instead fails", () => {
  const exception = shippedNodeForgeException();
  const asFalsePositive = {
    schema_version: 1,
    false_positives: [{
      advisory_id: exception.advisory_id,
      cve: exception.cve,
      package: exception.package,
      installed_version: exception.installed_version,
      severity: exception.severity,
      dependency_paths: exception.dependency_paths,
      authoritative_affected_range: "<= 1.4.0",
      authoritative_patched_version: "1.4.1",
      reason: "Misfiled as a false positive.",
      authoritative_source: "https://github.com/advisories/GHSA-86w9-cpqp-85rv",
      owner: exception.owner,
      created_date: exception.created_date,
      review_date: exception.review_date,
      expiry_date: exception.expiry_date,
      removal_condition: exception.removal_condition,
    }],
  };
  assert.throws(() => validateFalsePositiveRegistry(asFalsePositive), /frozen Nano ID identity contract/);
  assert.throws(
    () => rawValidateAuditText(
      completedNodeForgeAudit(),
      { schema_version: 1, exceptions: [] }, asFalsePositive, { today: NODE_FORGE_DAY },
    ),
    /frozen Nano ID identity contract/,
  );
});

test("node-forge 9: an incomplete audit stream fails even though the finding is governed", () => {
  // No summary at all: the scan never proved it finished.
  assert.throws(
    () => rawValidateAuditText(nodeForgeAudit(), shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY }),
    /no auditSummary: the scan did not run to completion/,
  );
  // A summary that is not the last record.
  assert.throws(
    () => rawValidateAuditText(
      `${cleanAudit({ high: 2 })}\n${nodeForgeAudit()}`,
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /continues after its auditSummary/,
  );
  // A summary claiming a CRITICAL that the stream never names.
  assert.throws(
    () => rawValidateAuditText(
      `${nodeForgeAudit()}\n${cleanAudit({ high: 2, critical: 1 })}`,
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY },
    ),
    /reports 1 CRITICAL vulnerability\/ies but the stream carries no CRITICAL advisory record/,
  );
});

test("node-forge 10: removing the registry entry while the vulnerable audit remains fails", () => {
  const removed = shippedRegistryWith((exception, copy) => {
    copy.exceptions = copy.exceptions.filter((candidate) => candidate !== exception);
  });
  assert.deepEqual(validateRegistry(removed).map((exception) => exception.advisory_id), ["GHSA-vfj7-8cjw-p6xm"]);
  assert.throws(
    () => rawValidateAuditText(completedNodeForgeAudit(), removed, shippedFalsePositiveRegistry, { today: NODE_FORGE_DAY }),
    /Unaccepted HIGH advisory GHSA-86w9-cpqp-85rv for node-forge \(1\.4\.0\)/,
  );
});

// ---- GHSA-vfj7-8cjw-p6xm: bounded build/test-tooling exception ------------
// The scanner reports ten Jest paths. The complete Yarn graph also includes
// Expo > @expo/metro > metro-file-map > micromatch > braces; that build-tool
// path is assessed in the exception prose even though Yarn audit omits it.
const BRACES = {
  advisoryId: "GHSA-vfj7-8cjw-p6xm",
  cve: "CVE-2026-93687",
  packageName: "braces",
  version: "3.0.3",
  severity: "high",
  paths: [
    "@types/jest>expect>jest-message-util>micromatch>braces",
    "jest-expo>@jest/globals>@jest/expect>expect>jest-message-util>micromatch>braces",
    "jest-expo>@jest/globals>@jest/expect>jest-snapshot>expect>jest-message-util>micromatch>braces",
    "jest-expo>jest-snapshot>expect>jest-message-util>micromatch>braces",
    "jest>@jest/core>jest-config>jest-circus>@jest/expect>expect>jest-message-util>micromatch>braces",
    "jest>@jest/core>jest-config>jest-circus>jest-runtime>@jest/globals>@jest/expect>expect>jest-message-util>micromatch>braces",
    "jest>@jest/core>micromatch>braces",
    "jest>jest-cli>@jest/core>jest-config>jest-circus>@jest/expect>expect>jest-message-util>micromatch>braces",
    "jest>jest-cli>@jest/core>jest-config>jest-circus>jest-runtime>@jest/globals>@jest/expect>expect>jest-message-util>micromatch>braces",
    "jest>jest-cli>@jest/core>jest-config>jest-circus>jest-runtime>@jest/globals>@jest/expect>jest-snapshot>expect>jest-message-util>micromatch>braces",
  ],
};
const BRACES_DAY = "2026-10-03";

function shippedBracesException() {
  return shippedExceptionRegistry.exceptions.find((exception) => exception.advisory_id === BRACES.advisoryId);
}

function bracesRegistryWith(mutate) {
  const copy = JSON.parse(JSON.stringify(shippedExceptionRegistry));
  mutate(copy.exceptions.find((exception) => exception.advisory_id === BRACES.advisoryId), copy);
  return copy;
}

function bracesAudit(overrides = {}) {
  const finding = { ...BRACES, ...overrides };
  return finding.paths.map((resolutionPath) => JSON.stringify({
    type: "auditAdvisory",
    data: {
      resolution: { path: resolutionPath },
      advisory: {
        cves: finding.cves ?? [finding.cve],
        findings: [{ paths: finding.paths, version: finding.version }],
        github_advisory_id: finding.advisoryId,
        module_name: finding.packageName,
        severity: finding.severity,
      },
    },
  })).join("\n");
}

function completedBracesAudit(overrides = {}) {
  const severity = overrides.severity || BRACES.severity;
  return `${bracesAudit(overrides)}\n${cleanAudit({ [severity]: (overrides.paths || BRACES.paths).length })}`;
}

function validateBraces(audit = completedBracesAudit(), exceptions = shippedExceptionRegistry, today = BRACES_DAY) {
  return rawValidateAuditText(audit, exceptions, shippedFalsePositiveRegistry, { today });
}

test("braces: shipped exception pins the exact ten scanner paths and acknowledges Metro", () => {
  const exception = shippedBracesException();
  assert.ok(exception);
  assert.equal(exception.cve, BRACES.cve);
  assert.equal(exception.package, BRACES.packageName);
  assert.equal(exception.installed_version, BRACES.version);
  assert.equal(exception.severity, BRACES.severity);
  assert.deepEqual(exception.dependency_paths, BRACES.paths);
  assert.deepEqual(
    [exception.created_date, exception.review_date, exception.expiry_date],
    ["2026-10-03", "2026-10-10", "2026-10-17"],
  );
  assert.equal(exception.owner, "@blazebrt");
  assert.equal(exception.production_runtime_reachable, false);
  assert.equal(exception.production_user_input_reachable, false);
  assert.equal(exception.build_ci_reachable, true);
  assert.match(exception.reachability_assessment, /expo > @expo\/metro > metro-file-map@0\.83\.8 > micromatch@4\.0\.8 > braces@3\.0\.3/);
  assert.match(exception.reachability_assessment, /not a Jest-only or dev-dependency-only graph/);
  assert.match(exception.reachability_assessment, /micromatch\.some\(relativePath, globs\)/);
  assert.deepEqual(shippedFalsePositiveRegistry.false_positives, []);
});

test("braces: exact completed finding is a visible temporary exception", () => {
  const result = validateBraces();
  assert.deepEqual(result.acceptedExceptions.map((exception) => exception.advisory_id), [BRACES.advisoryId]);
  assert.equal(result.falsePositiveFindings.length, 0);
  assert.deepEqual([...result.findings[0].paths].sort(), [...BRACES.paths].sort());
  assert.match(formatPass(result), /Accepted temporary exceptions: 1/);
  assert.match(formatPass(result), /braces@3\.0\.3/);
  const both = rawValidateAuditText(
    `${nodeForgeAudit()}\n${bracesAudit()}\n${cleanAudit({ high: 12 })}`,
    shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: BRACES_DAY },
  );
  assert.deepEqual(both.acceptedExceptions.map((exception) => exception.advisory_id), [NODE_FORGE.advisoryId, BRACES.advisoryId]);
  assert.match(formatPass(both), /Accepted temporary exceptions: 2/);
  assert.match(formatPass(both), /Unaccepted HIGH: 0/);
  assert.match(formatPass(both), /Unaccepted CRITICAL: 0/);
});

test("braces: another advisory fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ advisoryId: "GHSA-other-braces" })), /Unaccepted HIGH advisory GHSA-other-braces/);
});

test("braces: another CVE fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ cve: "CVE-2099-0001" })), /does not match reported CVE/);
});

test("braces: an omitted CVE fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ cves: [] })), /does not match reported CVE/);
});

test("braces: an additional CVE fails", () => {
  assert.throws(
    () => validateBraces(completedBracesAudit({ cves: [BRACES.cve, "CVE-2099-0001"] })),
    /does not match reported CVE/,
  );
});

test("braces: another package fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ packageName: "braces-fork" })), /Unaccepted HIGH advisory .*braces-fork/);
});

test("braces: another installed version fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ version: "3.0.4" })), /does not match installed version/);
});

test("braces: a missing scanner path fails", () => {
  assert.throws(
    () => validateBraces(completedBracesAudit({ paths: BRACES.paths.slice(1) })),
    /does not match the complete dependency path set/,
  );
});

test("braces: an additional scanner path fails", () => {
  assert.throws(
    () => validateBraces(completedBracesAudit({ paths: [...BRACES.paths, "expo>@expo/metro>metro-file-map>micromatch>braces"] })),
    /does not allow dependency path expo>@expo\/metro>metro-file-map>micromatch>braces/,
  );
});

test("braces: CRITICAL substitution fails", () => {
  assert.throws(() => validateBraces(completedBracesAudit({ severity: "critical" })), /Unaccepted CRITICAL advisory/);
});

test("braces: missing reachability fields fail", () => {
  for (const field of ["reachability_assessment", "production_runtime_reachable", "production_user_input_reachable", "build_ci_reachable", "compensating_controls"]) {
    const incomplete = bracesRegistryWith((exception) => { delete exception[field]; });
    assert.throws(() => validateBraces(completedBracesAudit(), incomplete), new RegExp(`missing required field ${field}`));
  }
});

test("braces: production-runtime reachability cannot be accepted", () => {
  const reachable = bracesRegistryWith((exception) => { exception.production_runtime_reachable = true; });
  assert.throws(() => validateBraces(completedBracesAudit(), reachable), /cannot accept production-runtime or user-input reachability/);
});

test("braces: user-input reachability cannot be accepted", () => {
  const reachable = bracesRegistryWith((exception) => { exception.production_user_input_reachable = true; });
  assert.throws(() => validateBraces(completedBracesAudit(), reachable), /cannot accept production-runtime or user-input reachability/);
});

test("braces: review warning and expiry boundary remain enforced", () => {
  const review = validateBraces(completedBracesAudit(), shippedExceptionRegistry, "2026-10-10");
  assert.deepEqual(review.reviewWarnings, [
    "SECURITY EXCEPTION REVIEW DUE: GHSA-vfj7-8cjw-p6xm review_date=2026-10-10 expiry_date=2026-10-17",
  ]);
  assert.throws(
    () => validateBraces(completedBracesAudit(), shippedExceptionRegistry, "2026-10-17"),
    /Security exception GHSA-vfj7-8cjw-p6xm expired on 2026-10-17/,
  );
});

test("braces: removing its record leaves the HIGH unaccepted", () => {
  const removed = bracesRegistryWith((exception, copy) => {
    copy.exceptions = copy.exceptions.filter((candidate) => candidate !== exception);
  });
  assert.throws(() => validateBraces(completedBracesAudit(), removed), /Unaccepted HIGH advisory GHSA-vfj7-8cjw-p6xm/);
});

test("braces: false-positive substitution fails", () => {
  const exception = shippedBracesException();
  const falsePositive = {
    schema_version: 1,
    false_positives: [{
      advisory_id: exception.advisory_id,
      cve: exception.cve,
      package: exception.package,
      installed_version: exception.installed_version,
      severity: exception.severity,
      dependency_paths: exception.dependency_paths,
      authoritative_affected_range: "<= 3.0.3",
      authoritative_patched_version: "3.0.4",
      reason: "Incorrectly called a false positive",
      authoritative_source: "https://github.com/advisories/GHSA-vfj7-8cjw-p6xm",
      owner: exception.owner,
      created_date: exception.created_date,
      review_date: exception.review_date,
      expiry_date: exception.expiry_date,
      removal_condition: exception.removal_condition,
    }],
  };
  const removed = bracesRegistryWith((record, copy) => {
    copy.exceptions = copy.exceptions.filter((candidate) => candidate !== record);
  });
  assert.throws(() => rawValidateAuditText(completedBracesAudit(), removed, falsePositive, { today: BRACES_DAY }), /frozen Nano ID identity contract/);
});

test("http-cache-semantics remains unaccepted rather than hidden by a new exception", () => {
  assert.equal(shippedExceptionRegistry.exceptions.some((exception) => exception.package === "http-cache-semantics"), false);
  assert.throws(
    () => rawValidateAuditText(
      `${auditAdvisory({ advisoryId: "GHSA-ch52-4w7c-c8xp", cve: "CVE-2026-93748", packageName: "http-cache-semantics", version: "4.2.0", paths: ["@expo/ngrok>got>cacheable-request>http-cache-semantics"] })}\n${cleanAudit({ high: 1 })}`,
      shippedExceptionRegistry, shippedFalsePositiveRegistry, { today: BRACES_DAY },
    ),
    /Unaccepted HIGH advisory GHSA-ch52-4w7c-c8xp/,
  );
});
