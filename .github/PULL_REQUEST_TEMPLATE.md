# Pull request

## Summary

<!-- One paragraph. What changes and why. -->

## Baseline

- Baseline commit (`main` HEAD at branch-off): `<sha>`
- Branch name follows convention: [ ] yes

## Risk

<!-- Low / Medium / High + why. Anything touching auth, privacy, media, safety, or progress is High by default. -->

## Rollback plan

<!-- How would we undo this if it goes wrong in production? Include the exact revert command or migration downgrade command. -->

## Data changes

<!-- Any migration? Does the change require follow-up migrations to be safe? Is the migration reversible? -->

## Tests actually run

<!--
Concrete evidence, not "I think so". The CI output is preferred. If you ran
something locally that CI cannot run (a real device journey, a live provider
call), say which device and paste the timestamped output.
-->

- [ ] `pytest -q tests` — passing (CI run URL: `<url>`)
- [ ] Frontend tests — passing
- [ ] `alembic upgrade head` on an empty database — passing
- [ ] `alembic check` — no drift
- [ ] CI jobs (Security, Audits, Tests) — passing
- [ ] Manual smoke test on a real device — attach screenshot or explicitly state none available

## External tests actually run

<!-- Live Gemini? S3-compatible upload against a real bucket? Monitoring test event? State which of these actually happened. -->

## UI changes

<!-- Before / after screenshots for every screen that changes. -->

## Security & Privacy Implications

<!-- Detail any security or privacy implications of this change. -->

## AI-assisted authorship

- [ ] Some or all of this PR was produced by an AI coding assistant.
      If checked, name the tool(s): `<tool>`.
- [ ] The merge commit will carry the trailer `AI-assisted-by: <tool>`.

## Known limitations

<!-- What this PR deliberately does not do. -->

## Owner action required

<!-- Any actions required by the owner after merge (e.g. updating settings). -->

## Final checks

- [ ] I have read the diff.
- [ ] I have read the tests that ran.
- [ ] I confirm the evidence provided is truthful and not fabricated.
