import test from 'node:test';
import assert from 'node:assert/strict';
import {
  hashPassword,
  verifyPassword,
  createDbUser,
  findDbUserByEmail,
  getDbComplaints,
  saveDbComplaint,
  getDbComplaintById,
} from '../src/db/database.ts';
import {
  generateSignedToken,
  verifySignedToken,
} from '../src/services/authService.ts';
import { validateStateTransition } from '../src/services/stateMachine.ts';
import { computeCivicRiskScore } from '../src/services/riskScorer.ts';
import { runDBSCANClustering } from '../src/services/clustering.ts';
import { evaluateInterventionEffect } from '../src/services/interventionReassessment.ts';
import { rankEvidenceGapInspections } from '../src/services/evidenceGapEngine.ts';
import { generateIntelligentCivicResponse } from '../src/services/civicKnowledgeEngine.ts';
import { Complaint, UserSession } from '../src/types/index.ts';

test('1. Cryptographic Authentication: PBKDF2 Password Hashing & Verification', () => {
  const plainPassword = 'SecuredPassword@2026';
  const { hash, salt } = hashPassword(plainPassword);

  assert.ok(hash && hash.length === 128, 'PBKDF2 hash should be 128 hex chars (64 bytes)');
  assert.ok(salt && salt.length === 32, 'Salt should be 32 hex chars (16 bytes)');

  // Correct password verification
  const isValid = verifyPassword(plainPassword, hash, salt);
  assert.equal(isValid, true, 'Correct password must verify to true');

  // Wrong password verification
  const isInvalid = verifyPassword('WrongPassword123', hash, salt);
  assert.equal(isInvalid, false, 'Wrong password must be rejected');
});

test('2. HMAC-SHA256 Token Generation & Expiration Guard', () => {
  const mockUser: UserSession = {
    id: 'u-cit-999',
    name: 'Test Citizen',
    email: 'test.citizen@swasthyagrid.tn.gov.in',
    role: 'CITIZEN',
    isDemo: false,
  };

  const token = generateSignedToken(mockUser);
  assert.ok(token.includes('.'), 'Token must contain payload and signature separated by dot');

  const payload = verifySignedToken(token);
  assert.ok(payload !== null, 'Valid token must be successfully verified');
  assert.equal(payload?.email, mockUser.email);
  assert.equal(payload?.role, 'CITIZEN');

  // Tampered token test
  const tamperedToken = token.slice(0, -4) + 'abcd';
  const tamperedPayload = verifySignedToken(tamperedToken);
  assert.equal(tamperedPayload, null, 'Tampered signature must be rejected');
});

test('3. Role Security: User Registration & Role Boundary', () => {
  const testEmail = `test.user.${Date.now()}@example.com`;
  const registered = createDbUser({
    email: testEmail,
    password: 'password123',
    fullName: 'Test Resident',
    role: 'CITIZEN',
  });

  assert.equal(registered.role, 'CITIZEN', 'Public registration must strictly assign CITIZEN role');
  assert.equal(registered.email, testEmail);

  // Attempting duplicate registration must fail
  assert.throws(() => {
    createDbUser({
      email: testEmail,
      password: 'password123',
      fullName: 'Duplicate Resident',
      role: 'CITIZEN',
    });
  }, /already exists/);
});

test('4. Civic Lifecycle State Machine: Valid and Invalid Role Transitions', () => {
  // Citizen trying to jump SUBMITTED -> RESOLVED (Illegal)
  const illegalCitizenJump = validateStateTransition('SUBMITTED', 'RESOLVED', 'CITIZEN', true);
  assert.equal(illegalCitizenJump.isValid, false, 'Citizen cannot jump directly from SUBMITTED to RESOLVED');

  // Citizen confirming resolution: AWAITING_VERIFICATION -> RESOLVED (Allowed if owner)
  const legalCitizenConfirm = validateStateTransition('AWAITING_VERIFICATION', 'RESOLVED', 'CITIZEN', true);
  assert.equal(legalCitizenConfirm.isValid, true, 'Citizen owner can verify and resolve completed action');

  // Citizen who is NOT owner trying to resolve another citizen\'s complaint (Illegal)
  const notOwnerConfirm = validateStateTransition('AWAITING_VERIFICATION', 'RESOLVED', 'CITIZEN', false);
  assert.equal(notOwnerConfirm.isValid, false, 'Non-owner citizen cannot confirm resolution');

  // Field Officer progressing work: ASSIGNED -> ACTION_IN_PROGRESS (Allowed)
  const legalFieldProgress = validateStateTransition('ASSIGNED', 'ACTION_IN_PROGRESS', 'FIELD_OFFICER');
  assert.equal(legalFieldProgress.isValid, true, 'Field officer can mark action in progress');

  // Field Officer attempting final administrative case closure: ACTION_IN_PROGRESS -> CLOSED (Illegal)
  const illegalFieldClose = validateStateTransition('ACTION_IN_PROGRESS', 'CLOSED', 'FIELD_OFFICER');
  assert.equal(illegalFieldClose.isValid, false, 'Field officer cannot unilaterally close case');

  // Department Officer / Admin valid transition: ASSIGNED -> ACTION_IN_PROGRESS -> AWAITING_VERIFICATION -> CLOSED
  const adminClose = validateStateTransition('RESOLVED', 'CLOSED', 'DEPT_OFFICER');
  assert.equal(adminClose.isValid, true, 'Department officer can perform final case closure');

  // Health Analyst trying to mutate operational state (Illegal - read-only surveillance role)
  const analystMutation = validateStateTransition('SUBMITTED', 'ACCEPTED', 'HEALTH_ANALYST');
  assert.equal(analystMutation.isValid, false, 'Health analyst cannot mutate operational civic state');
});

test('5. Deterministic Risk Scoring Formula: Mathematical Bounding & Sensitivities', () => {
  // High hazard inputs: sewage contamination near school
  const highRisk = computeCivicRiskScore({
    severityWeight: 0.9,
    persistenceDays: 7,
    independentReportsCount: 5,
    exposureIndex: 0.85,
    unresolvedDurationDays: 4,
    evidenceQuality: 0.9,
  });

  assert.ok(highRisk.score >= 0.0 && highRisk.score <= 1.0, 'Risk score must be bounded in [0.0, 1.0]');
  assert.ok(highRisk.band === 'HIGH' || highRisk.band === 'URGENT', 'Severe hazard must yield HIGH or URGENT band');
  assert.ok(Object.keys(highRisk.breakdown).length > 0, 'Risk calculation must explain individual factor contributions');

  // Low hazard input: single minor dry garbage sighting
  const lowRisk = computeCivicRiskScore({
    severityWeight: 0.2,
    persistenceDays: 1,
    independentReportsCount: 1,
    exposureIndex: 0.1,
    unresolvedDurationDays: 0,
    evidenceQuality: 0.5,
  });

  assert.ok(lowRisk.score < highRisk.score, 'Minor hazard must score lower than severe hazard');
  assert.ok(lowRisk.band === 'ROUTINE' || lowRisk.band === 'MODERATE', 'Minor hazard should be ROUTINE or MODERATE');
});

test('6. DBSCAN Spatio-temporal Clustering Algorithm', () => {
  const baseTime = Date.now();
  // 3 points close to each other in Ward 10 within 1 hour
  const clusterPoints = [
    { id: 'p1', lat: 13.0827, lng: 80.2707, category: 'sewage_overflow', timestamp: baseTime, title: 'Point 1' },
    { id: 'p2', lat: 13.0831, lng: 80.2711, category: 'sewage_overflow', timestamp: baseTime + 1800000, title: 'Point 2' },
    { id: 'p3', lat: 13.0829, lng: 80.2709, category: 'sewage_overflow', timestamp: baseTime + 3600000, title: 'Point 3' },
    // 1 distant outlier point
    { id: 'p4', lat: 13.1500, lng: 80.3500, category: 'sewage_overflow', timestamp: baseTime, title: 'Outlier Point' },
  ];

  const clusters = runDBSCANClustering(clusterPoints, 300, 2, 24);
  assert.ok(clusters.length >= 1, 'Should detect at least 1 cluster from the 3 proximal points');
  assert.ok(clusters[0].memberIds.length >= 3, 'Cluster should include the 3 close points');
});

test('7. Research Candidate 1: Intervention-Aware Risk Reassessment', () => {
  const result = evaluateInterventionEffect({
    interventionId: 'int-test-01',
    complaintId: 'cmp-test-01',
    type: 'DESILTING',
    completedAt: new Date(Date.now() - 14 * 86400000),
    baselineWindowDays: 14,
    evaluationWindowDays: 14,
    baselineIncidentCount: 12,
    postIncidentCount: 2, // Large reduction
  });

  assert.ok(result.observedDeltaRatio <= -0.5, 'Incident delta ratio should reflect significant decrease (<= -50%)');
  assert.equal(result.trendVerdict, 'IMPROVING');
  assert.ok(result.recommendation.length > 10, 'Must produce actionable recommendation');
});

test('8. Research Candidate 2: Evidence-Gap Inspection Prioritization', () => {
  const candidates = [
    {
      complaintId: 'c1',
      trackingNumber: 'SG-1',
      priorityScore: 0.8,
      lastInspectedHoursAgo: 72, // Long uninspected
      hasVerifiedPhoto: false, // Gap in photo
      hasPreciseGps: false, // Gap in GPS
      citizenReportCount: 4,
      isInterventionPendingCheck: false,
    },
    {
      complaintId: 'c2',
      trackingNumber: 'SG-2',
      priorityScore: 0.3,
      lastInspectedHoursAgo: 2, // Just inspected
      hasVerifiedPhoto: true,
      hasPreciseGps: true,
      citizenReportCount: 1,
      isInterventionPendingCheck: false,
    },
  ];

  const ranked = rankEvidenceGapInspections(candidates);
  assert.equal(ranked[0].complaintId, 'c1', 'Candidate c1 with high gaps and high age must rank highest for inspection');
});

test('9. Civic Knowledge Engine: Municipal Complaint Drafting & Bilingual Support', () => {
  const draftEn = generateIntelligentCivicResponse('Help me draft a clear complaint about a stagnant water mosquito pool.', {
    role: 'CITIZEN',
    language: 'en',
    wardNumber: 10,
  });

  assert.ok(draftEn.includes('Official Complaint Draft'), 'Must output official municipal draft format');
  assert.ok(draftEn.includes('Zone 10'), 'Must include Greater Chennai Corporation Zone 10');
  assert.ok(draftEn.includes('GCC SLA'), 'Must include official SLA response times');

  const draftTa = generateIntelligentCivicResponse('தேங்கி நிற்கும் நீர் மற்றும் கொசு', {
    role: 'CITIZEN',
    language: 'ta',
    wardNumber: 10,
  });

  assert.ok(draftTa.includes('நகராட்சி புகார்'), 'Must produce Tamil municipal draft');
  assert.ok(draftTa.includes('சென்னை மாநகராட்சி'), 'Must reference Greater Chennai Corporation in Tamil');
});

test('10. SQLite Database Persistence: Insert and Retrieve Complaint', () => {
  const testId = `cmp-pers-${Date.now()}`;
  const mockComplaint: Complaint = {
    id: testId,
    trackingNumber: `SG-TN-TEST-${Date.now().toString().slice(-4)}`,
    citizenId: 'u-cit-01',
    citizenName: 'M. Anand',
    categoryId: 'cat-sewage',
    categoryCode: 'sewage_overflow',
    departmentCode: 'CMWSSB',
    wardNumber: 10,
    wardName: 'Ward 10 Chennai',
    title: 'Test SQLite Persistence Incident',
    description: 'Testing SQLite file storage on disk',
    status: 'SUBMITTED',
    currentPriority: 'HIGH',
    priorityScore: 0.78,
    lat: 13.0827,
    lng: 80.2707,
    addressText: 'Perambur Barracks Rd, Chennai',
    requiresHumanReview: true,
    slaDeadlineHours: 24,
    hoursElapsed: 0,
    independentReportsCount: 2,
    proximityRiskLevel: 'HIGH',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    observedAt: new Date().toISOString(),
  };

  saveDbComplaint(mockComplaint);

  const retrieved = getDbComplaintById(testId);
  assert.ok(retrieved !== null, 'Complaint must be retrieved from SQLite database');
  assert.equal(retrieved?.title, mockComplaint.title);
  assert.equal(retrieved?.priorityScore, 0.78);
});
