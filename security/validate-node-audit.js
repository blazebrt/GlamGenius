#!/usr/bin/env node

const fs = require("node:fs");

const REQUIRED_EXCEPTION_FIELDS = [
  "advisory_id",
  "cve",
  "package",
  "installed_version",
  "severity",
  "dependency_paths",
  "reason",
  "reachability_assessment",
  "production_runtime_reachable",
  "production_user_input_reachable",
  "build_ci_reachable",
  "compensating_controls",
  "owner",
  "created_date",
  "review_date",
  "expiry_date",
  "removal_condition",
  "upstream_tracking",
];

const REQUIRED_FALSE_POSITIVE_FIELDS = [
  "advisory_id",
  "cve",
  "package",
  "installed_version",
  "severity",
  "dependency_paths",
  "authoritative_affected_range",
  "authoritative_patched_version",
  "reason",
  "authoritative_source",
  "owner",
  "created_date",
  "review_date",
  "expiry_date",
  "removal_condition",
];

const EXPECTED_IMAGE_SIZE_PATHS = [
  "expo>@expo/metro>metro>image-size",
  "expo>@expo/cli>@expo/metro>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>image-size",
  "expo>@expo/cli>@expo/metro>metro>metro-config>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>image-size",
  "expo>@expo/cli>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
];

const EXPECTED_IMAGE_SIZE_ADVISORIES = {
  "GHSA-w3rx-r6r6-pgpr": "CVE-2025-71330",
  "GHSA-5p2g-fcmc-qvqq": "CVE-2025-71329",
};

const EXPECTED_NANOID_FALSE_POSITIVE = {
  advisory_id: "GHSA-2v37-7h3g-55p8",
  cve: "CVE-2026-67213",
  package: "nanoid",
  installed_version: "3.3.17",
  severity: "high",
  dependency_paths: [
    "@react-navigation/native>nanoid",
    "@react-navigation/native>@react-navigation/core>nanoid",
    "@react-navigation/native>@react-navigation/core>@react-navigation/routers>nanoid",
    "expo-router>@react-navigation/native>@react-navigation/core>@react-navigation/routers>nanoid",
  ],
  authoritative_affected_range: "< 3.3.17",
  authoritative_patched_version: "3.3.17",
};

function fail(message) {
  throw new Error(message);
}

function parseDate(value, fieldName) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    fail(`Invalid ${fieldName}; expected YYYY-MM-DD`);
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    fail(`Invalid ${fieldName}; expected a real UTC date`);
  }
  return value;
}

function validateRegistry(registry) {
  if (!registry || registry.schema_version !== 1 || !Array.isArray(registry.exceptions)) {
    fail("Exception registry must contain schema_version=1 and an exceptions array");
  }

  const ids = new Set();
  for (const [index, exception] of registry.exceptions.entries()) {
    for (const field of REQUIRED_EXCEPTION_FIELDS) {
      if (!(field in exception)) {
        fail(`Exception ${index} is missing required field ${field}`);
      }
    }
    if (ids.has(exception.advisory_id)) {
      fail(`Duplicate exception advisory_id ${exception.advisory_id}`);
    }
    ids.add(exception.advisory_id);
    if (typeof exception.advisory_id !== "string" || !exception.advisory_id.startsWith("GHSA-")) {
      fail(`Exception ${index} has an invalid advisory_id`);
    }
    if (typeof exception.cve !== "string" || !exception.cve.startsWith("CVE-")) {
      fail(`Exception ${index} has an invalid cve`);
    }
    if (typeof exception.package !== "string" || !exception.package) {
      fail(`Exception ${index} has an invalid package`);
    }
    if (typeof exception.installed_version !== "string" || !exception.installed_version) {
      fail(`Exception ${index} has an invalid installed_version`);
    }
    if (exception.severity !== "high" && exception.severity !== "critical") {
      fail(`Exception ${index} has unsupported severity ${exception.severity}`);
    }
    if (
      !Array.isArray(exception.dependency_paths) ||
      exception.dependency_paths.length === 0 ||
      exception.dependency_paths.some((dependencyPath) => typeof dependencyPath !== "string" || !dependencyPath)
    ) {
      fail(`Exception ${index} must contain non-empty dependency_paths`);
    }
    for (const booleanField of [
      "production_runtime_reachable",
      "production_user_input_reachable",
      "build_ci_reachable",
    ]) {
      if (typeof exception[booleanField] !== "boolean") {
        fail(`Exception ${index} field ${booleanField} must be boolean`);
      }
    }
    if (!Array.isArray(exception.compensating_controls) || exception.compensating_controls.length === 0) {
      fail(`Exception ${index} must contain compensating_controls`);
    }
    for (const textField of [
      "reason",
      "reachability_assessment",
      "owner",
      "removal_condition",
      "upstream_tracking",
    ]) {
      if (typeof exception[textField] !== "string" || !exception[textField]) {
        fail(`Exception ${index} field ${textField} must be non-empty`);
      }
    }
    const createdDate = parseDate(exception.created_date, `exception ${index} created_date`);
    const reviewDate = parseDate(exception.review_date, `exception ${index} review_date`);
    const expiryDate = parseDate(exception.expiry_date, `exception ${index} expiry_date`);
    if (!(createdDate < reviewDate && reviewDate < expiryDate)) {
      fail(`Exception ${index} dates must satisfy created_date < review_date < expiry_date`);
    }
    if (exception.package === "image-size") {
      if (!(exception.advisory_id in EXPECTED_IMAGE_SIZE_ADVISORIES)) {
        fail(`Exception ${index} has an unapproved image-size advisory`);
      }
      if (exception.cve !== EXPECTED_IMAGE_SIZE_ADVISORIES[exception.advisory_id]) {
        fail(`Exception ${index} has an advisory/CVE mismatch`);
      }
      if (exception.installed_version !== "1.2.1" || exception.severity !== "high") {
        fail(`Exception ${index} has an unapproved image-size version or severity`);
      }
      if (
        exception.production_runtime_reachable !== false ||
        exception.production_user_input_reachable !== false ||
        exception.build_ci_reachable !== true
      ) {
        fail(`Exception ${index} has an invalid image-size reachability contract`);
      }
      if (new Set(exception.dependency_paths).size !== EXPECTED_IMAGE_SIZE_PATHS.length ||
          EXPECTED_IMAGE_SIZE_PATHS.some((dependencyPath) => !exception.dependency_paths.includes(dependencyPath))) {
        fail(`Exception ${index} has an invalid Expo/Metro dependency-path contract`);
      }
      for (const requiredPhrase of [
        "not imported by GlamGenius application source",
        "not bundled into the shipped React Native application runtime",
        "User-uploaded GlamGenius images do not flow into this Metro image parser",
        "malicious checked-in/build asset",
        "not production-runtime reachable under the currently verified architecture",
      ]) {
        if (!exception.reachability_assessment.includes(requiredPhrase)) {
          fail(`Exception ${index} reachability assessment is missing: ${requiredPhrase}`);
        }
      }
    }
  }
  const imageSizeIds = registry.exceptions
    .filter((exception) => exception.package === "image-size")
    .map((exception) => exception.advisory_id)
    .sort();
  if (imageSizeIds.length !== 2 || imageSizeIds.join(",") !== Object.keys(EXPECTED_IMAGE_SIZE_ADVISORIES).sort().join(",")) {
    fail("Exception registry must contain exactly the two approved image-size advisories");
  }
  return registry.exceptions;
}

function validateFalsePositiveRegistry(registry) {
  if (!registry || registry.schema_version !== 1 || !Array.isArray(registry.false_positives)) {
    fail("False-positive registry must contain schema_version=1 and a false_positives array");
  }
  if (registry.false_positives.length > 1) {
    fail("False-positive registry must contain at most the approved Nano ID record");
  }

  const ids = new Set();
  for (const [index, falsePositive] of registry.false_positives.entries()) {
    for (const field of REQUIRED_FALSE_POSITIVE_FIELDS) {
      if (!(field in falsePositive)) {
        fail(`False positive ${index} is missing required field ${field}`);
      }
    }
    if (ids.has(falsePositive.advisory_id)) {
      fail(`Duplicate false-positive advisory_id ${falsePositive.advisory_id}`);
    }
    ids.add(falsePositive.advisory_id);
    if (falsePositive.advisory_id !== EXPECTED_NANOID_FALSE_POSITIVE.advisory_id ||
        falsePositive.cve !== EXPECTED_NANOID_FALSE_POSITIVE.cve ||
        falsePositive.package !== EXPECTED_NANOID_FALSE_POSITIVE.package ||
        falsePositive.installed_version !== EXPECTED_NANOID_FALSE_POSITIVE.installed_version ||
        falsePositive.severity !== EXPECTED_NANOID_FALSE_POSITIVE.severity ||
        falsePositive.authoritative_affected_range !== EXPECTED_NANOID_FALSE_POSITIVE.authoritative_affected_range ||
        falsePositive.authoritative_patched_version !== EXPECTED_NANOID_FALSE_POSITIVE.authoritative_patched_version) {
      fail(`False positive ${index} does not match the frozen Nano ID identity contract`);
    }
    if (!Array.isArray(falsePositive.dependency_paths) ||
        falsePositive.dependency_paths.length !== EXPECTED_NANOID_FALSE_POSITIVE.dependency_paths.length ||
        new Set(falsePositive.dependency_paths).size !== EXPECTED_NANOID_FALSE_POSITIVE.dependency_paths.length ||
        EXPECTED_NANOID_FALSE_POSITIVE.dependency_paths.some((path) => !falsePositive.dependency_paths.includes(path))) {
      fail(`False positive ${index} has an invalid dependency-path contract`);
    }
    for (const textField of [
      "reason",
      "authoritative_source",
      "owner",
      "removal_condition",
    ]) {
      if (typeof falsePositive[textField] !== "string" || !falsePositive[textField]) {
        fail(`False positive ${index} field ${textField} must be non-empty`);
      }
    }
    const createdDate = parseDate(falsePositive.created_date, `false positive ${index} created_date`);
    const reviewDate = parseDate(falsePositive.review_date, `false positive ${index} review_date`);
    const expiryDate = parseDate(falsePositive.expiry_date, `false positive ${index} expiry_date`);
    if (!(createdDate < reviewDate && reviewDate < expiryDate)) {
      fail(`False positive ${index} dates must satisfy created_date < review_date < expiry_date`);
    }
  }
  return registry.false_positives;
}

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

//: Yarn Classic emits one counter per severity in every auditSummary. All five
//: are required: a summary missing them is not the record Yarn writes at the
//: end of a real audit, and since that record is now our only proof the scan
//: finished, a loose shape check would let a fabricated or truncated object
//: stand in for that proof.
const SUMMARY_SEVERITIES = ["info", "low", "moderate", "high", "critical"];

function isCount(value) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/**
 * Is this record the marker that the audit ran to completion?
 *
 * Yarn emits exactly one `auditSummary` at the end of a successful audit, so
 * its presence is the only evidence we get that the scanner finished rather
 * than dying partway through. Advisory records are NOT such evidence: an audit
 * that emits two advisories and then loses its connection looks, to anyone
 * counting records, exactly like an audit that found two advisories and
 * stopped because there were no more.
 *
 * That distinction is the whole point. If the advisories received before a
 * crash happen to be ones already governed by an exception, accepting a
 * summary-less stream would report PASS on a scan that never completed --
 * silently clearing every dependency the scanner never got to.
 *
 * Because the marker carries that weight, its shape is checked strictly: every
 * canonical severity counter must be present and be a non-negative integer.
 * `Number.isInteger` also rejects `NaN` and both infinities, so fractional,
 * negative, string, boolean, null and missing counters all fail here.
 * Non-security fields Yarn may add alongside `vulnerabilities` are left alone.
 */
function auditSummaryIsValid(record) {
  if (!record || record.type !== "auditSummary") return false;
  if (!isPlainObject(record.data)) return false;
  const counts = record.data.vulnerabilities;
  if (!isPlainObject(counts)) return false;
  return SUMMARY_SEVERITIES.every((severity) => isCount(counts[severity]));
}

/**
 * What kind of audit record is this? `"advisory"`, `"summary"`, or `null`.
 *
 * Exported so the retry runner does not maintain a competing interpretation of
 * Yarn's output. It classifies; it never judges. Severity, exceptions, false
 * positives and expiry all stay here, in the authority.
 */
function classifyAuditRecord(record) {
  if (record && record.type === "auditAdvisory") return "advisory";
  if (auditSummaryIsValid(record)) return "summary";
  return null;
}

/**
 * Does this stream show an audit that ran to completion?
 *
 * Three things, all of which the runner and the validator must agree on:
 * exactly one valid summary, and it is the last thing in the stream. Yarn
 * writes the summary when it has finished, so anything after it means the
 * output is not the tidy end-of-audit stream it appears to be -- a second
 * audit's records bleeding in, a truncated concatenation, or a stream that
 * carried on past the point we thought it stopped. Rather than guess which,
 * refuse it.
 *
 * Parsing is lenient about unparseable noise, because Yarn writes progress
 * chatter that is not our business; the validator is stricter and rejects it
 * outright. Both directions of that disagreement are safe: the runner may
 * retry something the validator would reject, never the reverse.
 */
//: The only severities whose absence of detail we can act on. A count above
//: zero for either one is a claim the gate must be able to check.
const SUBSTANTIATED_SEVERITIES = ["high", "critical"];

/**
 * Which severities does this summary claim without any advisory to back them?
 *
 * One definition, used twice: by `auditStreamCompletion` against a single raw
 * Yarn attempt, and by the validator against the stream it is finally handed.
 * Never a count comparison -- Yarn counts vulnerable paths and one advisory can
 * cover several, so equality would fail honest audits. Only presence.
 */
function severitiesLackingDetail(summary, observedSeverities) {
  const counts = summary.data.vulnerabilities;
  return SUBSTANTIATED_SEVERITIES.filter(
    (severity) => counts[severity] > 0 && !observedSeverities.has(severity),
  );
}

function auditStreamCompletion(auditText) {
  if (typeof auditText !== "string" || !auditText.trim()) {
    return { complete: false, reason: "no output" };
  }
  let summaries = 0;
  let summaryAt = -1;
  let lastContentAt = -1;
  let summary = null;
  const observed = new Set();
  for (const [index, line] of auditText.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    lastContentAt = index;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    if (auditSummaryIsValid(record)) {
      summaries += 1;
      summaryAt = index;
      summary = record;
    } else if (classifyAuditRecord(record) === "advisory") {
      const severity = record.data && record.data.advisory && record.data.advisory.severity;
      if (severity) observed.add(severity);
    }
  }
  if (summaries === 0) return { complete: false, reason: "no valid auditSummary" };
  if (summaries > 1) return { complete: false, reason: `${summaries} auditSummary records` };
  if (summaryAt !== lastContentAt) {
    return { complete: false, reason: "auditSummary is not the final record" };
  }
  // Substance, judged strictly within this one raw attempt. An attempt whose
  // summary counts a HIGH but which never emitted a HIGH advisory did not
  // finish coherently, whatever some *other* attempt happened to report.
  const unsubstantiated = severitiesLackingDetail(summary, observed);
  if (unsubstantiated.length) {
    return {
      complete: false,
      reason: `summary counts ${unsubstantiated.join(" and ")} with no matching advisory in this attempt`,
    };
  }
  return { complete: true, reason: "complete" };
}

function parseAuditText(auditText) {
  if (typeof auditText !== "string" || !auditText.trim()) {
    fail("Audit output is empty");
  }
  const records = [];
  let summaries = 0;
  let summary = null;
  let summaryOrdinal = -1;
  let ordinal = 0;
  for (const [lineNumber, line] of auditText.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch (error) {
      fail(`Audit output line ${lineNumber + 1} is not valid JSON: ${error.message}`);
    }
    ordinal += 1;
    if (record && record.type === "auditAdvisory") {
      if (!record.data || !record.data.advisory) {
        fail(`Audit advisory line ${lineNumber + 1} is missing advisory data`);
      }
      records.push(record.data);
    } else if (record && record.type === "auditSummary") {
      if (!auditSummaryIsValid(record)) {
        fail(`Audit summary line ${lineNumber + 1} is missing vulnerability data`);
      }
      summaries += 1;
      summary = record;
      summaryOrdinal = ordinal;
    } else {
      fail(`Audit output line ${lineNumber + 1} has unsupported record type`);
    }
  }
  // Completeness is checked here, in the authority, and not only in the runner
  // that feeds it: these scripts can be invoked independently, CI wiring drifts,
  // and the last retry attempt can still hand over a partial stream. A gate that
  // trusts its caller to have validated its input is not a gate.
  if (summaries === 0) {
    fail(
      "Audit output contains no auditSummary: the scan did not run to completion, " +
        "so its advisories cannot clear the dependency tree",
    );
  }
  if (summaries > 1) {
    fail(`Audit output contains ${summaries} auditSummary records; expected exactly one`);
  }
  // Yarn writes the summary last. Records after it mean this is not one tidy
  // end-of-audit stream, and we cannot tell which part is the finished audit.
  if (summaryOrdinal !== ordinal) {
    fail(
      "Audit output continues after its auditSummary: the summary must be the " +
        "final record, so this stream cannot be read as one completed audit",
    );
  }
  return { records, summary };
}

/**
 * If the summary counts a HIGH or CRITICAL, the detail for it must be here.
 *
 * The summary is itself scanner evidence. When Yarn says a HIGH exists but no
 * advisory record for one arrived, the gate has been told a vulnerability is
 * present and handed nothing to identify it with -- no advisory id, package,
 * version or path, so no way to match it against an exception or a false
 * positive. There is no honest verdict available: clearing it would be
 * clearing something we cannot name. Fail closed.
 *
 * Only that one direction is enforced. The reverse -- a summary counting zero
 * while an advisory is present -- is legitimate and must keep working: a HIGH
 * carried forward from an earlier incomplete attempt sits alongside a later
 * clean summary that never saw it, and that carried finding still has to be
 * evaluated on its merits. Discarding it because the final count says zero
 * would re-open exactly the hole the sticky evidence closed.
 *
 * Counts are never compared numerically against the number of advisory
 * records. Yarn counts vulnerable paths, one advisory can cover several, and a
 * naive equality rule would fail honest audits.
 */
function assertSummaryDetailIsPresent(summary, records) {
  const observed = new Set(
    records.map((record) => record.advisory && record.advisory.severity),
  );
  for (const severity of severitiesLackingDetail(summary, observed)) {
    fail(
      `Audit summary reports ${summary.data.vulnerabilities[severity]} ` +
        `${severity.toUpperCase()} vulnerability/ies but the stream carries no ` +
        `${severity.toUpperCase()} advisory record: without an advisory id, ` +
        "package, version and paths the gate cannot match it against any " +
        "exception, so it cannot clear it",
    );
  }
}

function collectHighAndCritical(records) {
  const findings = new Map();
  for (const record of records) {
    const advisory = record.advisory;
    if (!advisory || !["high", "critical"].includes(advisory.severity)) continue;
    const advisoryId = advisory.github_advisory_id;
    const packageName = advisory.module_name;
    if (!advisoryId || !packageName || !Array.isArray(advisory.findings) || advisory.findings.length === 0) {
      fail("HIGH/CRITICAL audit advisory is missing id, package, or findings evidence");
    }
    const key = `${advisoryId}|${packageName}|${advisory.severity}`;
    const existing = findings.get(key) || {
      advisoryId,
      cves: new Set(),
      packageName,
      severity: advisory.severity,
      versions: new Set(),
      paths: new Set(),
    };
    for (const cve of advisory.cves || []) existing.cves.add(cve);
    for (const finding of advisory.findings) {
      if (typeof finding.version !== "string" || !finding.version) {
        fail(`HIGH/CRITICAL advisory ${advisoryId} has a finding without a version`);
      }
      if (!Array.isArray(finding.paths) || finding.paths.length === 0) {
        fail(`HIGH/CRITICAL advisory ${advisoryId} has a finding without dependency paths`);
      }
      existing.versions.add(finding.version);
      for (const dependencyPath of finding.paths) {
        if (typeof dependencyPath !== "string" || !dependencyPath) {
          fail(`HIGH/CRITICAL advisory ${advisoryId} has an invalid dependency path`);
        }
        existing.paths.add(dependencyPath);
      }
    }
    if (record.resolution && record.resolution.path) {
      existing.paths.add(record.resolution.path);
    }
    findings.set(key, existing);
  }
  return [...findings.values()];
}

function validateFalsePositiveFinding(finding, falsePositive, today) {
  if (today >= falsePositive.expiry_date) {
    fail(`Scanner false-positive record ${falsePositive.advisory_id} expired on ${falsePositive.expiry_date}`);
  }
  if (finding.advisoryId !== falsePositive.advisory_id ||
      finding.packageName !== falsePositive.package ||
      finding.severity !== falsePositive.severity) {
    fail(`Scanner false-positive ${falsePositive.advisory_id} does not match advisory/package/severity evidence`);
  }
  if (finding.versions.size !== 1 || !finding.versions.has(falsePositive.installed_version)) {
    fail(`Scanner false-positive ${falsePositive.advisory_id} does not match installed version(s): ${[...finding.versions].join(", ")}`);
  }
  if (finding.cves.size > 0 && (finding.cves.size !== 1 || !finding.cves.has(falsePositive.cve))) {
    fail(`Scanner false-positive ${falsePositive.advisory_id} does not match reported CVE(s)`);
  }
  const allowedPaths = new Set(falsePositive.dependency_paths);
  if (finding.paths.size !== allowedPaths.size || [...finding.paths].some((path) => !allowedPaths.has(path))) {
    fail(`Scanner false-positive ${falsePositive.advisory_id} does not allow the observed dependency paths`);
  }
}

function resolveValidationInputs(falsePositiveRegistryOrOptions, maybeOptions) {
  if (falsePositiveRegistryOrOptions && Array.isArray(falsePositiveRegistryOrOptions.false_positives)) {
    return {
      falsePositiveRegistry: falsePositiveRegistryOrOptions,
      options: maybeOptions || {},
    };
  }
  return {
    falsePositiveRegistry: { schema_version: 1, false_positives: [] },
    options: falsePositiveRegistryOrOptions || {},
  };
}

function validateAuditText(auditText, registry, falsePositiveRegistryOrOptions = {}, maybeOptions = {}) {
  const exceptions = validateRegistry(registry);
  const { falsePositiveRegistry, options } = resolveValidationInputs(falsePositiveRegistryOrOptions, maybeOptions);
  const falsePositives = validateFalsePositiveRegistry(falsePositiveRegistry);
  const { records, summary } = parseAuditText(auditText);
  const findings = collectHighAndCritical(records);
  assertSummaryDetailIsPresent(summary, records);
  const today = options.today || new Date().toISOString().slice(0, 10);
  parseDate(today, "today");
  const accepted = [];
  const falsePositiveFindings = [];
  const reviewWarnings = [];

  for (const finding of findings) {
    const falsePositive = falsePositives.find((candidate) => candidate.advisory_id === finding.advisoryId);
    if (falsePositive) {
      validateFalsePositiveFinding(finding, falsePositive, today);
      falsePositiveFindings.push(falsePositive);
      continue;
    }
    const exception = exceptions.find(
      (candidate) =>
        candidate.advisory_id === finding.advisoryId &&
        candidate.package === finding.packageName &&
        candidate.severity === finding.severity,
    );
    if (!exception) {
      fail(
        `Unaccepted ${finding.severity.toUpperCase()} advisory ${finding.advisoryId} ` +
          `for ${finding.packageName} (${[...finding.versions].join(", ")})`,
      );
    }
    if (today >= exception.expiry_date) {
      fail(`Security exception ${exception.advisory_id} expired on ${exception.expiry_date}`);
    }
    if (today >= exception.review_date) {
      reviewWarnings.push(
        `SECURITY EXCEPTION REVIEW DUE: ${exception.advisory_id} review_date=${exception.review_date} expiry_date=${exception.expiry_date}`,
      );
    }
    if (finding.versions.size !== 1 || !finding.versions.has(exception.installed_version)) {
      fail(
        `Security exception ${exception.advisory_id} does not match installed version(s): ` +
          `${[...finding.versions].join(", ")}`,
      );
    }
    if (finding.cves.size > 0 && !finding.cves.has(exception.cve)) {
      fail(`Security exception ${exception.advisory_id} does not match reported CVE(s)`);
    }
    const allowedPaths = new Set(exception.dependency_paths);
    for (const dependencyPath of finding.paths) {
      if (!allowedPaths.has(dependencyPath)) {
        fail(`Security exception ${exception.advisory_id} does not allow dependency path ${dependencyPath}`);
      }
    }
    accepted.push(exception);
  }

  return {
    acceptedExceptions: [...new Map(accepted.map((exception) => [exception.advisory_id, exception])).values()],
    falsePositiveFindings: [...new Map(falsePositiveFindings.map((finding) => [finding.advisory_id, finding])).values()],
    findings,
    reviewWarnings,
  };
}

function formatPass(result) {
  const lines = [
    "Node security gate PASS",
    "Unaccepted HIGH: 0",
    "Unaccepted CRITICAL: 0",
    `Accepted temporary exceptions: ${result.acceptedExceptions.length}`,
    `Known scanner false positives: ${result.falsePositiveFindings.length}`,
  ];
  for (const exception of result.acceptedExceptions) {
    lines.push(`${exception.advisory_id}`);
    lines.push(`${exception.package}@${exception.installed_version}`);
    lines.push(`expires ${exception.expiry_date}`);
  }
  for (const falsePositive of result.falsePositiveFindings) {
    lines.push(`${falsePositive.advisory_id}`);
    lines.push(`${falsePositive.package}@${falsePositive.installed_version}`);
    lines.push(`scanner false positive; expires ${falsePositive.expiry_date}`);
  }
  lines.push(...result.reviewWarnings);
  return lines.join("\n");
}

function main(argv) {
  const auditPath = argv[2];
  const registryPath = argv[3] || "security/node-audit-exceptions.json";
  const falsePositiveRegistryPath = argv[4] || "security/node-audit-false-positives.json";
  if (!auditPath) {
    console.error("Usage: node security/validate-node-audit.js <audit-json> [exception-registry] [false-positive-registry]");
    process.exitCode = 2;
    return;
  }
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const falsePositiveRegistry = JSON.parse(fs.readFileSync(falsePositiveRegistryPath, "utf8"));
    const result = validateAuditText(fs.readFileSync(auditPath, "utf8"), registry, falsePositiveRegistry);
    console.log(formatPass(result));
  } catch (error) {
    console.error(`Node security gate FAIL\n${error.message}`);
    process.exitCode = 1;
  }
}

if (require.main === module) main(process.argv);

module.exports = {
  auditStreamCompletion,
  auditSummaryIsValid,
  classifyAuditRecord,
  formatPass,
  parseAuditText,
  validateAuditText,
  validateRegistry,
  validateFalsePositiveRegistry,
};
