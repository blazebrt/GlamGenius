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

const EXPECTED_IMAGE_SIZE_PATHS = [
  "expo>@expo/metro>metro>image-size",
  "expo>@expo/cli>@expo/metro>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>image-size",
  "expo>@expo/metro>metro>metro-config>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>image-size",
  "expo>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
  "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
];

const EXPECTED_IMAGE_SIZE_ADVISORIES = {
  "GHSA-w3rx-r6r6-pgpr": "CVE-2025-71330",
  "GHSA-5p2g-fcmc-qvqq": "CVE-2025-71329",
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

function parseAuditText(auditText) {
  if (typeof auditText !== "string" || !auditText.trim()) {
    fail("Audit output is empty");
  }
  const records = [];
  for (const [lineNumber, line] of auditText.split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch (error) {
      fail(`Audit output line ${lineNumber + 1} is not valid JSON: ${error.message}`);
    }
    if (record && record.type === "auditAdvisory") {
      if (!record.data || !record.data.advisory) {
        fail(`Audit advisory line ${lineNumber + 1} is missing advisory data`);
      }
      records.push(record.data);
    } else if (record && record.type === "auditSummary") {
      continue;
    } else {
      fail(`Audit output line ${lineNumber + 1} has unsupported record type`);
    }
  }
  return records;
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

function validateAuditText(auditText, registry, options = {}) {
  const exceptions = validateRegistry(registry);
  const records = parseAuditText(auditText);
  const findings = collectHighAndCritical(records);
  const today = options.today || new Date().toISOString().slice(0, 10);
  parseDate(today, "today");
  const accepted = [];
  const reviewWarnings = [];

  for (const finding of findings) {
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
    findings,
    reviewWarnings,
  };
}

function formatPass(result) {
  const lines = [
    "Node security gate PASS",
    "Unexcepted HIGH: 0",
    "Unexcepted CRITICAL: 0",
    `Accepted temporary exceptions: ${result.acceptedExceptions.length}`,
  ];
  for (const exception of result.acceptedExceptions) {
    lines.push(`${exception.advisory_id}`);
    lines.push(`${exception.package}@${exception.installed_version}`);
    lines.push(`expires ${exception.expiry_date}`);
  }
  lines.push(...result.reviewWarnings);
  return lines.join("\n");
}

function main(argv) {
  const auditPath = argv[2];
  const registryPath = argv[3] || "security/node-audit-exceptions.json";
  if (!auditPath) {
    console.error("Usage: node security/validate-node-audit.js <audit-json> [exception-registry]");
    process.exitCode = 2;
    return;
  }
  try {
    const registry = JSON.parse(fs.readFileSync(registryPath, "utf8"));
    const result = validateAuditText(fs.readFileSync(auditPath, "utf8"), registry);
    console.log(formatPass(result));
  } catch (error) {
    console.error(`Node security gate FAIL\n${error.message}`);
    process.exitCode = 1;
  }
}

if (require.main === module) main(process.argv);

module.exports = {
  formatPass,
  parseAuditText,
  validateAuditText,
  validateRegistry,
};
