const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const {
  formatPass,
  validateAuditText: rawValidateAuditText,
  validateFalsePositiveRegistry,
  validateRegistry,
} = require("./validate-node-audit");

// Frozen fixtures rather than the live registries. Both governed advisories
// are remediated and the shipped registries are now empty, but the validator's
// rules about accepted exceptions and scanner false positives still need a
// well-formed record to be exercised against, and reading that record out of
// the live registry meant fixing the vulnerability deleted the tests' inputs.
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
// nanoid resolution moved to the patched 3.3.18, so nothing in the tree needs
// governing and both shipped registries are empty. The registry rules have to
// admit that state without admitting anything else.

test("the shipped registries are empty and still validate", () => {
  assert.deepEqual(validateRegistry(shippedExceptionRegistry), []);
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
