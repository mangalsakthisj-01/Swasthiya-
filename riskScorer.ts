import { RiskFactors, RiskEvaluationResult, PriorityBand } from '../types';

/**
 * Computes an explainable, linear-weighted Civic Risk Score (0.0000 - 1.0000).
 *
 * Mathematical Formula:
 * Score = 0.25 * Severity + 0.20 * Persistence + 0.20 * CorroboratedReports
 *       + 0.15 * Exposure + 0.10 * UnresolvedDuration + 0.10 * EvidenceQuality
 */
export function computeCivicRiskScore(factors: RiskFactors): RiskEvaluationResult {
  // Normalize each dimension to 0.0 - 1.0 bounded range
  const normSeverity = Math.min(Math.max(factors.severityWeight, 0), 1.0);
  const normPersistence = Math.min(Math.max(factors.persistenceDays, 0) / 14.0, 1.0); // Capped at 14 days
  const normReports = Math.min(Math.max(factors.independentReportsCount - 1, 0) / 5.0, 1.0); // Capped at 6 reports
  const normExposure = Math.min(Math.max(factors.exposureIndex, 0), 1.0); // Proximity to schools, hospitals
  const normUnresolved = Math.min(Math.max(factors.unresolvedDurationDays, 0) / 7.0, 1.0); // Capped at 7 days overdue
  const normEvidence = Math.min(Math.max(factors.evidenceQuality, 0), 1.0); // Photo + GPS verified

  // Linear weighted contributions
  const weighted = {
    severity: Number((0.25 * normSeverity).toFixed(4)),
    persistence: Number((0.20 * normPersistence).toFixed(4)),
    reports: Number((0.20 * normReports).toFixed(4)),
    exposure: Number((0.15 * normExposure).toFixed(4)),
    unresolved: Number((0.10 * normUnresolved).toFixed(4)),
    evidence: Number((0.10 * normEvidence).toFixed(4)),
  };

  const totalScore = Number(
    Object.values(weighted)
      .reduce((acc, val) => acc + val, 0)
      .toFixed(4)
  );

  // Operational Priority Banding
  let band: PriorityBand = 'ROUTINE';
  if (totalScore >= 0.72) {
    band = 'URGENT';
  } else if (totalScore >= 0.52) {
    band = 'HIGH';
  } else if (totalScore >= 0.32) {
    band = 'MODERATE';
  }

  // Explainability narrative
  const explanation: string[] = [];
  if (normSeverity >= 0.7) {
    explanation.push(`High hazard baseline weight (${(normSeverity * 100).toFixed(0)}%) associated with public contamination vector.`);
  }
  if (factors.independentReportsCount > 1) {
    explanation.push(`Corroborated by ${factors.independentReportsCount} independent citizen reports across the locality.`);
  }
  if (normExposure >= 0.6) {
    explanation.push(`Located within immediate 200m buffer of vulnerable public infrastructure (educational or healthcare facility).`);
  }
  if (factors.persistenceDays >= 5) {
    explanation.push(`Condition has persisted unresolved for ${factors.persistenceDays} consecutive days.`);
  }
  if (factors.unresolvedDurationDays > 0) {
    explanation.push(`Standard municipal departmental SLA breached by ${factors.unresolvedDurationDays} days.`);
  }
  if (factors.evidenceQuality >= 0.8) {
    explanation.push(`High-confidence inspection grade: Verified photographic evidence and accurate GPS coordinates present.`);
  }

  return {
    score: totalScore,
    band,
    breakdown: weighted,
    explanation,
  };
}
