export type UserRole = 'CITIZEN' | 'FIELD_OFFICER' | 'DEPT_OFFICER' | 'HEALTH_ANALYST' | 'SYS_ADMIN';

export type PriorityBand = 'ROUTINE' | 'MODERATE' | 'HIGH' | 'URGENT';

export type ComplaintStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'UNDER_VALIDATION'
  | 'ACCEPTED'
  | 'ASSIGNED'
  | 'INSPECTION_SCHEDULED'
  | 'UNDER_INVESTIGATION'
  | 'ACTION_IN_PROGRESS'
  | 'AWAITING_VERIFICATION'
  | 'RESOLVED'
  | 'REOPENED'
  | 'REJECTED'
  | 'DUPLICATE'
  | 'NEEDS_MORE_INFO'
  | 'CLOSED';

export type VerificationVerdict = 'CONFIRMED_RESOLVED' | 'STILL_PERSISTS' | 'PARTIAL_IMPROVEMENT' | 'INCONCLUSIVE';

export interface UserSession {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  departmentCode?: string;
  isDemo: boolean;
}

export interface ComplaintCategory {
  id: string;
  code: string;
  name_en: string;
  name_ta: string;
  base_severity_weight: number;
}

export interface Complaint {
  id: string;
  trackingNumber: string;
  citizenId: string;
  citizenName: string;
  categoryId: string;
  categoryCode: string;
  departmentCode: string;
  wardNumber: number;
  wardName: string;
  title: string;
  description: string;
  status: ComplaintStatus;
  currentPriority: PriorityBand;
  priorityScore: number;
  lat: number;
  lng: number;
  addressText: string;
  imageUrl?: string;
  createdAt: string;
  updatedAt: string;
  observedAt: string;
  requiresHumanReview: boolean;
  assignedOfficerName?: string;
  assignedOfficerId?: string;
  slaDeadlineHours: number;
  hoursElapsed: number;
  independentReportsCount: number;
  proximityRiskLevel: 'LOW' | 'MEDIUM' | 'HIGH';
  aiSummary?: string;
  aiObservations?: string[];
  aiMissingInfo?: string[];
  aiLimitations?: string[];
}

export interface FieldInspection {
  id: string;
  complaintId: string;
  inspectorName: string;
  inspectedAt: string;
  observations: string;
  evidenceUrl?: string;
  isHazardActive: boolean;
}

export interface CivicIntervention {
  id: string;
  complaintId: string;
  trackingNumber: string;
  interventionType: 'DESILTING' | 'CHEMICAL_LARVICIDING' | 'CONTAINER_EMPTIED' | 'PIPE_PATCHING' | 'DRAINAGE_RECONSTRUCTION';
  description: string;
  completedAt: string;
  officerName: string;
  baselineWindowDays: number;
  evaluationWindowDays: number;
  baselineIncidentCount: number;
  postIncidentCount: number;
  rainfallShiftMm?: number;
  trendVerdict?: 'IMPROVING' | 'UNCHANGED' | 'DETERIORATING' | 'INSUFFICIENT_DATA';
  confidenceRating?: 'LOW' | 'MEDIUM' | 'HIGH';
  identifiedConfounders?: string[];
  recommendation?: string;
}

export interface AuditLogEntry {
  id: string;
  actorId: string;
  actorName: string;
  actorRole: UserRole;
  actionType: string;
  entityName: string;
  entityId: string;
  metadata?: Record<string, unknown>;
  ipAddress: string;
  createdAt: string;
}

export interface RiskFactors {
  severityWeight: number;         // 0.0 - 1.0
  persistenceDays: number;        // days open
  independentReportsCount: number;// distinct reports
  exposureIndex: number;          // proximity to schools/hospitals/markets (0.0 - 1.0)
  unresolvedDurationDays: number; // days since SLA breached
  evidenceQuality: number;        // 0.0 (text only) to 1.0 (verified photo + precise GPS)
}

export interface RiskEvaluationResult {
  score: number;
  band: PriorityBand;
  breakdown: Record<string, number>;
  explanation: string[];
}

export interface CoordinatePoint {
  id: string;
  lat: number;
  lng: number;
  category: string;
  timestamp: number;
  title?: string;
}

export interface IncidentCluster {
  id: string;
  centroid: { lat: number; lng: number };
  memberIds: string[];
  radiusMeters: number;
  dominantCategory: string;
}

export interface InspectionCandidate {
  complaintId: string;
  trackingNumber: string;
  priorityScore: number;
  lastInspectedHoursAgo: number;
  hasVerifiedPhoto: boolean;
  hasPreciseGps: boolean;
  citizenReportCount: number;
  isInterventionPendingCheck: boolean;
}

export interface PrioritizedInspectionTarget {
  complaintId: string;
  trackingNumber: string;
  urgencyScore: number;
  evidenceGapScore: number;
  compositeInspectionPriority: number;
  criticalMissingEvidence: string[];
}

export interface ReassessmentResult {
  observedDeltaRatio: number;
  trendVerdict: 'IMPROVING' | 'UNCHANGED' | 'DETERIORATING' | 'INSUFFICIENT_DATA';
  confidenceRating: 'LOW' | 'MEDIUM' | 'HIGH';
  identifiedConfounders: string[];
  recommendation: string;
}

export interface AICategorizationResponse {
  suggestedCategory: string;
  summary: string;
  extractedObservations: string[];
  missingInformation: string[];
  suggestedUrgency: PriorityBand;
  confidence: number;
  explanation: string[];
  limitations: string[];
  requiresHumanReview: boolean;
}
