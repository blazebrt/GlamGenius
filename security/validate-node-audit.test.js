const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const {
  formatPass,
  validateAuditText,
  validateFalsePositiveRegistry,
  validateRegistry,
} = require("./validate-node-audit");

const registry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-exceptions.json`, "utf8"),
);
const approved = registry.exceptions[0];
const falsePositiveRegistry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-false-positives.json`, "utf8"),
);
const nanoidFalsePositive = falsePositiveRegistry.false_positives[0];

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

function cleanAudit() {
  return JSON.stringify({
    type: "auditSummary",
    data: { vulnerabilities: { high: 0, critical: 0 } },
  });
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
