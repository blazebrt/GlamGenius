/**
 * Frozen advisory records, for the audit gate's own tests only.
 *
 * These are the exception and false-positive records that `security/`
 * shipped while `image-size` and Nano ID 3.3.17 were actually in the
 * dependency tree. Both are now remediated -- the metro package set is
 * pinned to 0.83.8, which dropped `image-size` entirely, and the nanoid
 * resolution is 3.3.18 -- so the live registries are empty.
 *
 * The tests still need a well-formed governed advisory to exercise the
 * validator's rules against: what an accepted exception looks like, what a
 * scanner false positive looks like, and every way each is allowed to fail.
 * Reading those fixtures out of the live registries, as the tests used to,
 * tied the gate's test coverage to the presence of an unfixed vulnerability
 * -- fixing the vulnerability deleted the tests' inputs. They are frozen here
 * instead, so remediating something never costs the gate its coverage.
 *
 * Nothing here suppresses anything. These objects are inputs to tests; the
 * gate reads `node-audit-exceptions.json` and `node-audit-false-positives.json`
 * and nothing else.
 */

const APPROVED_EXCEPTIONS = [
  {
    "advisory_id": "GHSA-w3rx-r6r6-pgpr",
    "cve": "CVE-2025-71330",
    "package": "image-size",
    "installed_version": "1.2.1",
    "severity": "high",
    "dependency_paths": [
      "expo>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size"
    ],
    "reason": "No patched image-size npm release is currently available; the approved exposure is limited to Expo/Metro build tooling.",
    "reachability_assessment": "The package is present beneath Expo/Metro Node tooling. It is not imported by GlamGenius application source. Metro/image-size is not bundled into the shipped React Native application runtime. User-uploaded GlamGenius images do not flow into this Metro image parser. A malicious checked-in/build asset may be able to trigger the affected parser during development or CI/export, creating a build-tool denial of service. This is not production-runtime reachable under the currently verified architecture.",
    "production_runtime_reachable": false,
    "production_user_input_reachable": false,
    "build_ci_reachable": true,
    "compensating_controls": [
      "Frontend builds run in ephemeral CI jobs.",
      "CI uses the frozen Yarn lockfile and pinned GitHub Actions.",
      "Expo exports use checked-in repository assets and do not download external image assets.",
      "Protected-branch review is required before merge."
    ],
    "owner": "@blazebrt",
    "created_date": "2026-08-08",
    "review_date": "2026-08-22",
    "expiry_date": "2026-09-07",
    "removal_condition": "Remove immediately when a patched image-size release or a compatible Expo/Metro release removes the vulnerable package/path; never auto-extend after expiry.",
    "upstream_tracking": "Track image-size parser remediation and compatible Expo/Metro SDK-54 releases; re-run yarn audit on every dependency update."
  },
  {
    "advisory_id": "GHSA-5p2g-fcmc-qvqq",
    "cve": "CVE-2025-71329",
    "package": "image-size",
    "installed_version": "1.2.1",
    "severity": "high",
    "dependency_paths": [
      "expo>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size",
      "expo>@expo/cli>@expo/metro-config>@expo/metro>metro>metro-config>metro>metro-config>metro>image-size"
    ],
    "reason": "No patched image-size npm release is currently available; the approved exposure is limited to Expo/Metro build tooling.",
    "reachability_assessment": "The package is present beneath Expo/Metro Node tooling. It is not imported by GlamGenius application source. Metro/image-size is not bundled into the shipped React Native application runtime. User-uploaded GlamGenius images do not flow into this Metro image parser. A malicious checked-in/build asset may be able to trigger the affected parser during development or CI/export, creating a build-tool denial of service. This is not production-runtime reachable under the currently verified architecture.",
    "production_runtime_reachable": false,
    "production_user_input_reachable": false,
    "build_ci_reachable": true,
    "compensating_controls": [
      "Frontend builds run in ephemeral CI jobs.",
      "CI uses the frozen Yarn lockfile and pinned GitHub Actions.",
      "Expo exports use checked-in repository assets and do not download external image assets.",
      "Protected-branch review is required before merge."
    ],
    "owner": "@blazebrt",
    "created_date": "2026-08-08",
    "review_date": "2026-08-22",
    "expiry_date": "2026-09-07",
    "removal_condition": "Remove immediately when a patched image-size release or a compatible Expo/Metro release removes the vulnerable package/path; never auto-extend after expiry.",
    "upstream_tracking": "Track image-size parser remediation and compatible Expo/Metro SDK-54 releases; re-run yarn audit on every dependency update."
  }
];

const APPROVED_FALSE_POSITIVES = [
  {
    "advisory_id": "GHSA-2v37-7h3g-55p8",
    "cve": "CVE-2026-67213",
    "package": "nanoid",
    "installed_version": "3.3.17",
    "severity": "high",
    "dependency_paths": [
      "@react-navigation/native>nanoid",
      "@react-navigation/native>@react-navigation/core>nanoid",
      "@react-navigation/native>@react-navigation/core>@react-navigation/routers>nanoid",
      "expo-router>@react-navigation/native>@react-navigation/core>@react-navigation/routers>nanoid"
    ],
    "authoritative_affected_range": "< 3.3.17",
    "authoritative_patched_version": "3.3.17",
    "reason": "Yarn Classic reports the patched Nano ID 3.3.17 release for an advisory whose authoritative affected range ends before 3.3.17. Re-verified 2026-08-29 at the scheduled expiry: yarn.lock still resolves nanoid to 3.3.17, the advisory's patched version is still 3.3.17, and the scanner still reports it, so the assessment is unchanged. Re-issued for one further review cycle by the owner; not auto-extended.",
    "authoritative_source": "https://github.com/ai/nanoid/releases/tag/3.3.17",
    "owner": "@blazebrt",
    "created_date": "2026-08-15",
    "review_date": "2026-09-19",
    "expiry_date": "2026-09-26",
    "removal_condition": "Remove immediately when the Yarn audit feed stops incorrectly reporting patched Nano ID 3.3.17; remove and re-review if the installed version, advisory, severity, or dependency path changes; never auto-extend this expiry."
  }
];

const exceptionRegistry = () => ({
  schema_version: 1,
  exceptions: JSON.parse(JSON.stringify(APPROVED_EXCEPTIONS)),
});

const falsePositiveRegistry = () => ({
  schema_version: 1,
  false_positives: JSON.parse(JSON.stringify(APPROVED_FALSE_POSITIVES)),
});

module.exports = {
  APPROVED_EXCEPTIONS,
  APPROVED_FALSE_POSITIVES,
  exceptionRegistry,
  falsePositiveRegistry,
};
