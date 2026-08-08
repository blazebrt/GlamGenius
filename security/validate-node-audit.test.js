const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const {
  formatPass,
  validateAuditText,
  validateRegistry,
} = require("./validate-node-audit");

const registry = JSON.parse(
  fs.readFileSync(`${__dirname}/node-audit-exceptions.json`, "utf8"),
);
const approved = registry.exceptions[0];

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

test("clean audit passes with no exceptions", () => {
  const result = validateAuditText(cleanAudit(), registry, { today: "2026-08-08" });
  assert.equal(result.findings.length, 0);
  assert.match(formatPass(result), /Unexcepted HIGH: 0/);
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

test("nanoid 3.3.16 remains an unaccepted HIGH finding", () => {
  assert.throws(
    () =>
      validateAuditText(
        auditAdvisory({
          advisoryId: "GHSA-2v37-7h3g-55p8",
          cve: "CVE-2026-67213",
          packageName: "nanoid",
          version: "3.3.16",
          paths: ["@react-navigation/native>nanoid"],
        }),
        registry,
        { today: "2026-08-08" },
      ),
    /Unaccepted HIGH advisory GHSA-2v37-7h3g-55p8/,
  );
});

test("nanoid 3.3.17 needs no exception", () => {
  const result = validateAuditText(
    auditAdvisory({
      advisoryId: "GHSA-nanoid-3-3-17-not-high",
      cve: "CVE-2099-0017",
      packageName: "nanoid",
      version: "3.3.17",
      severity: "moderate",
      paths: ["@react-navigation/native>nanoid"],
    }),
    registry,
    { today: "2026-08-08" },
  );
  assert.equal(result.acceptedExceptions.length, 0);
});
