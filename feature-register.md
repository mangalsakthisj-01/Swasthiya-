# SwasthyaGrid feature register

The notes below distinguish the current implementation from future integrations. No benefit is guaranteed without measuring it in a real jurisdiction.

## CivicPulse signal engine — implemented

- **Inputs:** non-final cases with a known coordinate, category, jurisdiction and creation timestamp; configurable window, distance radius and minimum cluster threshold.
- **Processing:** group by jurisdiction/category, cluster nearby distinct complaint IDs using haversine distance, create a stable signature, preserve the contributing report IDs/metadata, and require an administrator to review the candidate.
- **Outputs:** candidate signal, count, approximate centroid/radius, severity heuristic, case evidence, review status, admin inbox notification and append-only audit event.
- **Failure modes:** reports without coordinates do not join spatial clusters; poor duplicate linkage can inflate counts; threshold choices may create false positives or false negatives. Signals are never described as confirmed outbreaks.
- **Measurement ideas:** percentage of signals reviewed within SLA, fraction dismissed as duplicate/noise, time from first report to human acknowledgement, and repeat complaint rate after intervention.

## Incident fusion — implemented in a limited form

- **Inputs:** citizen narrative, selected category, optional media, incident coordinates, severity and jurisdiction.
- **Processing:** server-side category-to-department routing, critical-case notification, rule-based/Gemini triage adapter, case history, status and investigation evidence records. Separate complaints remain separate records; CivicPulse uses a distinct evidence set rather than merging records invisibly.
- **Outputs:** routed case, reviewer hint, history and evidence references.
- **Failure modes:** category rules may be imprecise, AI can be wrong or unavailable, media can be irrelevant; human review remains required.
- **Future extension:** add an explicit duplicate-candidate graph with administrator-confirmed links and tests to avoid merging unrelated reports.

## Proof-of-resolution — implemented

- **Inputs:** investigation report marked resolved/action completed, officer declaration and a real persisted after-action `ComplaintMedia` reference using an `AFTER:<media-id>` reference.
- **Processing:** the service checks that the after-action evidence row belongs to the case before allowing a field officer to transition the case to resolved.
- **Outputs:** resolution transition, history item, citizen notification and audit record.
- **Failure modes:** evidence proves that a file was uploaded, not that the claimed work occurred; reviewer verification and retention controls are still important.
- **Measurement ideas:** percentage of proposed resolutions with evidence; post-resolution reopen rate; time from action to citizen confirmation.

## SLA Guardian — implemented

- **Inputs:** case severity, response/escalation hours, due timestamp, assignment and current status.
- **Processing:** scheduled worker checks due cases, sends idempotent reminder notifications, escalates cases beyond the configured threshold and logs system events. Hotspots are automatically expired and affected jurisdictions notified.
- **Outputs:** SLA reminder/escalation inbox records, updated case state and audit chain.
- **Failure modes:** scheduler downtime delays action; poor policy configuration causes noisy alerts; external email/SMS delivery is not included without provider configuration.
- **Measurement ideas:** percentage of responses within SLA, median acknowledgement time and overdue backlog age.

## Cross-department dependency tracker — implemented

- **Inputs:** case, initiating department, target department, reason and optional due date.
- **Processing:** create a persistent dependency row, enforce case jurisdiction access for representatives and audit the action.
- **Outputs:** pending dependency list scoped to the representative's jurisdiction.
- **Failure modes:** notifications and automatic dependency closure are not yet connected to every downstream department workflow; owners must update status through a future workflow expansion.
- **Measurement ideas:** interdepartment wait time, overdue dependency rate and resolution time by dependency count.

## AI-assisted media triage — implemented adapter

- **Inputs:** validated media bytes/MIME type plus the citizen's selected category and description.
- **Processing:** optional Gemini vision adapter emits structured suggestions; missing key, network/model error or invalid response falls back to transparent rule-based triage. Uploaded image text is treated as untrusted evidence.
- **Outputs:** suggested category, confidence when available, explanation, visible-hazard hints, missing-evidence list and human-review status.
- **Failure modes:** model output can be wrong; model/service outages can occur; the adapter does not establish authenticity, diagnose disease or reject a case.
- **Measurement ideas:** human correction rate, uncertain-review rate and median time to triage; do not optimise solely for acceptance/rejection rate.

## Future proposals, not claimed as implemented

- Provider-specific SMS/WhatsApp/push delivery and delivery receipts.
- Offline field work queues with revision conflict resolution.
- A full duplicate-link lifecycle with similarity-assisted recommendations and authorised human confirmation.
- Geofenced and spatial PostGIS clustering for high volume; current CivicPulse cluster calculation runs in application code over a bounded set of cases, while the production schema includes PostGIS geometry columns/indexes.
- Data-retention jobs, jurisdiction-defined legal hold policies, and formal privacy impact assessment for actual public-sector deployments.
