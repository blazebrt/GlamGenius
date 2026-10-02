# Scaling organization — authority before headcount

Status: Step 18 planning authority, not a hiring authorization. Read with
[architecture authority](../architecture/ARCHITECTURE_AUTHORITY.md),
[scale architecture](../architecture/SCALE_ARCHITECTURE_100CR.md),
[operational SLO gates](../operations/SCALE_READINESS_AND_SLOS.md) and the
[capacity model](UNIT_ECONOMICS_AND_CAPACITY_MODEL.md). Today a solo founder
may perform several roles. That does not collapse independent approval rights.

## Decision rights that do not move with revenue

- Product Truth / Science owns evidence admission, uncertainty, rule validity,
  category interpretation and governed scientific release review. It may
  refuse an unsafe or unsupported commercial request. **NOW**, publication
  follows all five gates in the [Product Constitution](../../PRODUCT_CONSTITUTION.md),
  including the founder opening the source and confirming its number, plus
  the existing exact-pack [Step 8I activation lifecycle](../OPERATIONS.md#8-first-governed-skin-care-knowledge-activation).
  Neither authority currently makes a second distinct qualified human
  reviewer mandatory for every activation.
- Security / Privacy owns access, retention, breach triage and data-purpose
  review, with a stop right on privacy or security risk. Legal counsel owns
  regulated advice; this document is not a legal opinion.
- Backend / Platform owns deploy and data-migration safety. The incident
  commander may roll back an unsafe release; rollback cannot silently weaken
  Product Truth or delete audit evidence.
- Product owns customer problem and experience. Growth owns acquisition,
  Commerce owns partner operations, and B2B owns client relationships and
  contract operations. None may approve evidence, scientific thresholds,
  ranking, grades, verdicts or alternate Product Truth for a payer.
- Finance owns definitions, margin measurement and spend approval; ARR or a
  contract cannot override a safety gate.

When one person occupies multiple roles, record which constitutional and
governed-lifecycle gates they completed and any conflict. Do not silently
replace the founder's source-confirmation step or add a new universal NOW
veto. **NEXT — trigger required:** formally adopt a second independent
qualified human reviewer or expert panel when release volume exceeds one
person's safe capacity, methodology or category risk grows, evidence disputes
repeat, regulatory/enterprise obligations require it, or founder key-person
risk becomes unacceptable. Once that future two-person control is approved
and activated, an affected release waits if its required reviewer is absent.

## Five triggered operating stages

| Stage | Responsibilities and first roles | On-call and founder boundary | Change trigger |
| --- | --- | --- | --- |
| NOW — solo/free tier | Founder covers product, engineering, support and incident command; request external specialist review when needed. | Name an incident contact, keep change and credential-recovery records; do not promise continuous coverage. Founder owns spend and scientific-release stop. | Repeated missed incident/deletion target, unsupported scientific decision, customer-impacting fatigue, or binding B2B obligation. |
| NEXT — first specialist | Choose one bottleneck: qualified scientific reviewer, backend/platform engineer, privacy/security reviewer or support lead, per evidence below. Fractional review may suffice. | Name primary/backup response contacts; founder retains spend and conflict escalation but must stop being the sole technical/scientific reviewer. | A documented queue/risk persists after tooling and a funded runway exists. |
| Small functional team | Assign explicit Product Truth/review, product-mobile, platform/data, and support/security responsibilities; one generalist may cover adjacent engineering tasks. | Sustainable primary/secondary rota for agreed coverage hours, documented handover and drill; founder no longer sole deployer or incident contact. | Multiple independent change streams, repeat incidents/support load and role-specific work justify separation. |
| Multi-team scale | Product Truth, platform/reliability, mobile/product, data/AI, privacy/security and commercial teams have named accountable owners; QA/release engineering is explicit. | Rotating on-call, service ownership, access review, tested DR and cross-team incident command; founder arbitrates strategy, not every release. | Sustained cross-team coordination, measurable load and contract obligations. |
| Enterprise/company scale | B2B/customer success, finance/legal/compliance and specialist science/regulatory review expand only with real contracts and risk. | Funded primary/secondary coverage, contract-specific escalation and counsel-reviewed communications; founder owns capital allocation and constitutional guardrails. | Enterprise commitments and verified economics, not the phrase “₹100 Cr.” |

## RACI and veto map

R = executes, A = accountable final decision, C = consulted, I = informed.
Rows name future functions, not mandatory current employees. One person may
hold multiple letters; the veto boundaries above still apply.

| Decision | Founder/CEO | Product | Truth/Science | Platform/Data | Security/Privacy | Growth/Commerce | B2B/Success | Finance/Legal |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Evidence and scientific-rule activation | R for Constitution-required source opening and number confirmation | C | A/R for governed evidence/release review | R only for controlled technical execution | C, stop right | I | I | C for claims |
| Product UX and customer copy | I | A/R | C, stop right on scientific claims | C | C, stop right on data use | C | C | C for legal claims |
| Partner link and commercial contract | A for material commitment | C | C, stop right on Product Truth | C | C | R | R | A for economics/legal terms |
| Runtime topology, migration, rollback | I | C | C on truth impact | A/R | C, stop right | I | I | C on spend |
| Privacy retention/export/deletion | I | C | C | R | A | I | C | C |
| Severity/incident command | A until delegated | C | C on truth harm | R | R on security/privacy | I | R for customer communication | C |
| SLO or enterprise SLA commitment | A | C | C | C, feasibility veto | C, risk veto | I | R for client scope | A for funded terms |
| Hiring and paid capacity | A | C | C | C | C | C | C | R for affordability |

Science approval must be traceable to source, version, review and rollback.
Commercial colleagues can propose a research question but cannot queue or
force an automatic approval. A B2B client receives the same governed Product
Truth as a consumer; entitlement and transport may differ, truth may not.
When the NEXT trigger above is met and the new control is formally activated,
establish an independent qualified reviewer or expert panel and, where the
risk warrants it, a regulatory/compliance reviewer. Record conflicts of
interest, recusal, methodology-change approval and that control's sign-off.
This is not a retroactive condition on the current Step 8I process. A coding
team alone is not the scientific authority.

## Triggered hiring sequence, not a vanity chart

Order is conditional. Re-evaluate quarterly with the [SLO](../operations/SCALE_READINESS_AND_SLOS.md)
and [unit economics](UNIT_ECONOMICS_AND_CAPACITY_MODEL.md) evidence. Tooling,
automation and fractional review should be considered before permanent spend.

| Candidate capability | Bottleneck and evidence to hire | Why tooling alone is insufficient | Owned risk and expected outcome |
| --- | --- | --- | --- |
| Qualified Product Truth / science reviewer | Scientific release queue or unresolved evidence disputes delay safe releases; founder cannot supply independent review. | A model or test cannot establish evidence validity or independent accountability. | Reviewed provenance, calibrated uncertainty, defensible activation and retraction. |
| Backend/platform engineer | Repeated API/DB incidents or release toil persists after optimization and documented runbooks; founder is on a single-person critical path. | Automation cannot own migration judgment, incident recovery or a second human reviewer. | Reliable releases, bounded DB capacity, rollback and subsystem ownership. |
| Privacy/security lead (fractional first) | Access reviews, deletion/export, incident response or B2B diligence exceed documented capacity; measured overdue controls. | Scanners cannot approve data purpose or run a breach investigation. | Measured control closure and breach readiness with authority to stop unsafe changes. |
| Customer support/operations | Ticket age and incident communication exceed an explicitly funded support target after self-service improvements. | Automated answers cannot resolve sensitive product-truth disputes safely. | Case triage, escalation and voice-of-customer without scientific authority. |
| Mobile/product engineer | Mobile quality/release queue measurably blocks validated customer outcomes. | Build automation does not own UX decisions and device regressions. | Stable scan-first experience, accessibility and release quality. |
| SRE/reliability | Recurring out-of-hours incidents or on-call burden persist, multiple replicas/services exist, and an SLO/contract needs staffed coverage. | Dashboards are not responders and a single founder is not a 24×7 rotation. | Alerts, capacity, rehearsed recovery, sustainable on-call. |
| Data engineering | Audited analytical queries and retention jobs threaten Store B or provenance after SQL/retention optimization. | A warehouse alone does not define consent, lineage or data quality. | Governed aggregates and reproducible economics; no mixed-purpose raw export. |
| B2B/enterprise lead | Multiple signed clients generate support/contract toil above founder capacity with positive measured contribution margin. | CRM automation cannot negotiate lawful scope or incident communication. | Client operations and renewals; no authority over Product Truth. |
| Commerce/growth lead | Verified conversion and margin justify dedicated work; partner operations or acquisition tests exceed founder bandwidth. | Automation cannot resolve partner conflicts of interest. | Commercial growth under ranking and disclosure firewalls. |
| Finance/legal support | Contract, taxation, privacy or regulatory complexity requires specialist review; quantified risk or signed obligations. | Templates are not professional judgment. | Correct definitions, funded obligations and legal review. |

Do not recruit an SRE, create an enterprise 24×7 promise or buy a queue merely
to look mature. No unstaffed SLA. Write owner, backup, escalation time,
coverage hours and exception process before selling a support commitment.

## Incident and customer continuity

The incident commander coordinates containment, rollback, timeline and
customer updates. Product Truth/Science decides scientific correction and
customer-facing uncertainty; Security/Privacy directs breach handling.
Platform executes reversible technical controls. B2B/Customer Success gives
client-specific communication but cannot conceal a consumer-facing truth
issue. The founder is current fallback for every unfilled role; this is a
documented concentration risk, not proof of continuous coverage.

Quarterly readiness review: verify backup and restore evidence, access lists,
on-call reachability, scientific review queue, incident exercises and the
capacity model. Record what is measured, what is unknown, who owns follow-up,
and the date. No role or expensive architecture is automatically activated by
the strategic ARR target.
