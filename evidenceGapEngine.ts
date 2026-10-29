import { InspectionCandidate, PrioritizedInspectionTarget } from '../types';

/**
 * Novel Research Candidate 2: Evidence-Gap Inspection Prioritization
 *
 * Traditional municipal triage sends officers to highest reported severity.
 * However, this leads to blind spots where reports are high-risk but evidence is missing,
 * stale, or ambiguous. This engine computes an "Information Gain Ranking":
 * Composite Priority = 0.55 * OperationalUrgency + 0.45 * EvidenceGapScore
 */
export function rankEvidenceGapInspections(
  candidates: InspectionCandidate[]
): PrioritizedInspectionTarget[] {
  return candidates
    .map(c => {
      const missingEvidence: string[] = [];
      let gap = 0;

      // 1. Stale or absent inspection history
      if (c.lastInspectedHoursAgo === -1) {
        gap += 0.40;
        missingEvidence.push('Zero on-site physical officer inspections on record');
      } else if (c.lastInspectedHoursAgo > 72) {
        gap += 0.30;
        const daysAgo = Math.floor(c.lastInspectedHoursAgo / 24);
        missingEvidence.push(`Verification evidence stale (last inspected ${daysAgo} days ago)`);
      }

      // 2. Lack of high-quality verified imagery
      if (!c.hasVerifiedPhoto) {
        gap += 0.25;
        missingEvidence.push('No geo-tagged resolution photography available');
      }

      // 3. Imprecise coordinates
      if (!c.hasPreciseGps) {
        gap += 0.15;
        missingEvidence.push('Coarse or citizen-estimated location coordinates');
      }

      // 4. Completed intervention awaiting independent officer check
      if (c.isInterventionPendingCheck) {
        gap += 0.20;
        missingEvidence.push('Civic intervention recorded by contractor, pending departmental signoff');
      }

      // 5. Corroboration uncertainty (single reporter vs crowd)
      if (c.citizenReportCount <= 1) {
        gap += 0.10;
        missingEvidence.push('Single uncorroborated report; high false-positive or false-negative risk');
      }

      const evidenceGapScore = Math.min(Number(gap.toFixed(3)), 1.0);
      const urgencyScore = Math.min(Math.max(c.priorityScore, 0), 1.0);

      // Composite Information Gain Rank
      const composite = Number((0.55 * urgencyScore + 0.45 * evidenceGapScore).toFixed(4));

      return {
        complaintId: c.complaintId,
        trackingNumber: c.trackingNumber,
        urgencyScore: Number(urgencyScore.toFixed(3)),
        evidenceGapScore,
        compositeInspectionPriority: composite,
        criticalMissingEvidence: missingEvidence,
      };
    })
    .sort((a, b) => b.compositeInspectionPriority - a.compositeInspectionPriority);
}
