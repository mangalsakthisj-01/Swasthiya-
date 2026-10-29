import { ComplaintStatus, UserRole } from '../types/index.ts';

export interface StateTransitionResult {
  isValid: boolean;
  error?: string;
  autoLogRemarks?: string;
}

// Defined Lifecycle State Order
export const LIFECYCLE_STAGES: ComplaintStatus[] = [
  'DRAFT',
  'SUBMITTED',
  'UNDER_VALIDATION',
  'ACCEPTED',
  'ASSIGNED',
  'INSPECTION_SCHEDULED',
  'UNDER_INVESTIGATION',
  'ACTION_IN_PROGRESS',
  'AWAITING_VERIFICATION',
  'RESOLVED',
  'CLOSED',
];

/**
 * Permitted transitions by source state
 */
export const ALLOWED_TRANSITIONS: Record<ComplaintStatus, ComplaintStatus[]> = {
  DRAFT: ['SUBMITTED', 'CLOSED'],
  SUBMITTED: ['UNDER_VALIDATION', 'ACCEPTED', 'DUPLICATE', 'NEEDS_MORE_INFO', 'CLOSED'],
  UNDER_VALIDATION: ['ACCEPTED', 'ASSIGNED', 'DUPLICATE', 'NEEDS_MORE_INFO', 'CLOSED'],
  ACCEPTED: ['ASSIGNED', 'INSPECTION_SCHEDULED', 'ACTION_IN_PROGRESS', 'DUPLICATE', 'NEEDS_MORE_INFO', 'CLOSED'],
  ASSIGNED: ['INSPECTION_SCHEDULED', 'UNDER_INVESTIGATION', 'ACTION_IN_PROGRESS', 'NEEDS_MORE_INFO', 'CLOSED'],
  INSPECTION_SCHEDULED: ['UNDER_INVESTIGATION', 'ACTION_IN_PROGRESS', 'NEEDS_MORE_INFO', 'AWAITING_VERIFICATION', 'CLOSED'],
  UNDER_INVESTIGATION: ['ACTION_IN_PROGRESS', 'NEEDS_MORE_INFO', 'AWAITING_VERIFICATION', 'CLOSED'],
  ACTION_IN_PROGRESS: ['AWAITING_VERIFICATION', 'NEEDS_MORE_INFO', 'RESOLVED', 'CLOSED'],
  AWAITING_VERIFICATION: ['RESOLVED', 'REOPENED', 'ACTION_IN_PROGRESS'],
  RESOLVED: ['CLOSED', 'REOPENED'],
  CLOSED: ['REOPENED'],
  REOPENED: ['UNDER_VALIDATION', 'ACCEPTED', 'ASSIGNED', 'ACTION_IN_PROGRESS'],
  REJECTED: ['CLOSED', 'REOPENED'],
  DUPLICATE: ['CLOSED', 'REOPENED'],
  NEEDS_MORE_INFO: ['SUBMITTED', 'UNDER_VALIDATION', 'ACCEPTED', 'CLOSED'],
};

/**
 * Role permissions per state transition
 */
export function validateStateTransition(
  currentStatus: ComplaintStatus,
  targetStatus: ComplaintStatus,
  userRole: UserRole,
  isComplaintOwner: boolean = false
): StateTransitionResult {
  if (currentStatus === targetStatus) {
    return { isValid: false, error: `Complaint is already in status '${currentStatus}'.` };
  }

  const validTargets = ALLOWED_TRANSITIONS[currentStatus];
  if (!validTargets || !validTargets.includes(targetStatus)) {
    return {
      isValid: false,
      error: `Invalid lifecycle transition: Cannot move complaint directly from '${currentStatus}' to '${targetStatus}'.`,
    };
  }

  // Role-specific enforcement:
  switch (userRole) {
    case 'CITIZEN': {
      // Citizens can only:
      // 1. Confirm resolution from AWAITING_VERIFICATION -> RESOLVED
      // 2. Dispute resolution from AWAITING_VERIFICATION or RESOLVED -> REOPENED
      // 3. Provide info from NEEDS_MORE_INFO -> UNDER_VALIDATION
      if (!isComplaintOwner) {
        return { isValid: false, error: 'Citizens may only update the lifecycle of their own filed complaints.' };
      }

      if (
        (currentStatus === 'AWAITING_VERIFICATION' && targetStatus === 'RESOLVED') ||
        ((currentStatus === 'AWAITING_VERIFICATION' || currentStatus === 'RESOLVED' || currentStatus === 'CLOSED') && targetStatus === 'REOPENED') ||
        (currentStatus === 'NEEDS_MORE_INFO' && (targetStatus === 'UNDER_VALIDATION' || targetStatus === 'SUBMITTED'))
      ) {
        return { isValid: true };
      }

      return {
        isValid: false,
        error: `Citizens cannot perform transition '${currentStatus}' -> '${targetStatus}'. This requires municipal field or department authorization.`,
      };
    }

    case 'FIELD_OFFICER': {
      // Field officers can update progress on assigned work:
      const allowedForField: ComplaintStatus[] = [
        'INSPECTION_SCHEDULED',
        'UNDER_INVESTIGATION',
        'ACTION_IN_PROGRESS',
        'AWAITING_VERIFICATION',
        'NEEDS_MORE_INFO',
      ];
      if (allowedForField.includes(targetStatus)) {
        return { isValid: true };
      }

      return {
        isValid: false,
        error: `Field Officers cannot transition to '${targetStatus}'. Final closure and resolution approval requires citizen verification or supervisory signoff.`,
      };
    }

    case 'DEPT_OFFICER':
    case 'SYS_ADMIN': {
      // Full administrative authority over municipal workflow
      return { isValid: true };
    }

    case 'HEALTH_ANALYST': {
      return {
        isValid: false,
        error: 'Health Analysts hold advisory and epidemiologic surveillance access and cannot mutate civic operational workflow states directly.',
      };
    }

    default:
      return { isValid: false, error: 'Unrecognized user role.' };
  }
}
