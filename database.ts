import { DatabaseSync } from 'node:sqlite';
import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { Complaint, AuditLogEntry, CivicIntervention, UserSession, UserRole, PriorityBand, ComplaintStatus } from '../types/index.ts';
import { INITIAL_COMPLAINTS, INITIAL_INTERVENTIONS, INITIAL_AUDIT_LOGS } from '../data/mockData.ts';

const DATA_DIR = path.resolve(process.cwd(), 'data');
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

const DB_PATH = path.resolve(DATA_DIR, 'swasthyagrid.db');

export const db = new DatabaseSync(DB_PATH);

// Helper for secure PBKDF2 hashing
export function hashPassword(password: string, salt?: string): { hash: string; salt: string } {
  const actualSalt = salt || crypto.randomBytes(16).toString('hex');
  const hash = crypto.pbkdf2Sync(password, actualSalt, 10000, 64, 'sha512').toString('hex');
  return { hash, salt: actualSalt };
}

export function verifyPassword(password: string, hash: string, salt: string): boolean {
  const calculated = crypto.pbkdf2Sync(password, salt, 10000, 64, 'sha512').toString('hex');
  return crypto.timingSafeEqual(Buffer.from(hash, 'hex'), Buffer.from(calculated, 'hex'));
}

export function initDatabase() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      salt TEXT NOT NULL,
      full_name TEXT NOT NULL,
      role TEXT NOT NULL,
      department_code TEXT,
      phone TEXT,
      is_demo INTEGER DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS complaints (
      id TEXT PRIMARY KEY,
      tracking_number TEXT UNIQUE NOT NULL,
      citizen_id TEXT NOT NULL,
      citizen_name TEXT NOT NULL,
      category_id TEXT NOT NULL,
      category_code TEXT NOT NULL,
      department_code TEXT NOT NULL,
      ward_number INTEGER NOT NULL,
      ward_name TEXT NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      status TEXT NOT NULL,
      current_priority TEXT NOT NULL,
      priority_score REAL NOT NULL,
      lat REAL NOT NULL,
      lng REAL NOT NULL,
      address_text TEXT NOT NULL,
      image_url TEXT,
      requires_human_review INTEGER DEFAULT 0,
      sla_deadline_hours INTEGER NOT NULL,
      hours_elapsed REAL DEFAULT 0,
      independent_reports_count INTEGER DEFAULT 1,
      proximity_risk_level TEXT DEFAULT 'MEDIUM',
      ai_summary TEXT,
      ai_observations TEXT,
      ai_missing_info TEXT,
      ai_limitations TEXT,
      assigned_officer_id TEXT,
      assigned_officer_name TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      observed_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS complaint_status_history (
      id TEXT PRIMARY KEY,
      complaint_id TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      actor_name TEXT NOT NULL,
      actor_role TEXT NOT NULL,
      previous_status TEXT,
      new_status TEXT NOT NULL,
      remarks TEXT,
      timestamp TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS interventions (
      id TEXT PRIMARY KEY,
      complaint_id TEXT NOT NULL,
      tracking_number TEXT NOT NULL,
      intervention_type TEXT NOT NULL,
      description TEXT NOT NULL,
      completed_at TEXT NOT NULL,
      officer_id TEXT,
      officer_name TEXT NOT NULL,
      baseline_window_days INTEGER NOT NULL,
      evaluation_window_days INTEGER NOT NULL,
      baseline_incident_count INTEGER NOT NULL,
      post_incident_count INTEGER NOT NULL,
      rainfall_shift_mm REAL,
      trend_verdict TEXT,
      confidence_rating TEXT,
      identified_confounders TEXT,
      recommendation TEXT
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
      id TEXT PRIMARY KEY,
      actor_id TEXT NOT NULL,
      actor_name TEXT NOT NULL,
      actor_role TEXT NOT NULL,
      action_type TEXT NOT NULL,
      entity_name TEXT NOT NULL,
      entity_id TEXT NOT NULL,
      metadata TEXT,
      ip_address TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS notifications (
      id TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      title TEXT NOT NULL,
      message TEXT NOT NULL,
      type TEXT NOT NULL,
      is_read INTEGER DEFAULT 0,
      link TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS citizen_verifications (
      id TEXT PRIMARY KEY,
      complaint_id TEXT NOT NULL,
      citizen_id TEXT NOT NULL,
      citizen_name TEXT NOT NULL,
      verdict TEXT NOT NULL,
      remarks TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS duplicate_relationships (
      id TEXT PRIMARY KEY,
      primary_complaint_id TEXT NOT NULL,
      related_complaint_id TEXT NOT NULL,
      similarity_score REAL NOT NULL,
      reasoning TEXT NOT NULL,
      status TEXT DEFAULT 'SUGGESTED',
      reviewed_by TEXT,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_complaints_citizen ON complaints(citizen_id);
    CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
    CREATE INDEX IF NOT EXISTS idx_complaints_priority ON complaints(current_priority);
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
    CREATE INDEX IF NOT EXISTS idx_history_complaint ON complaint_status_history(complaint_id);
  `);

  seedInitialDataIfEmpty();
}

function seedInitialDataIfEmpty() {
  if (process.env.NODE_ENV === 'production' || process.env.SG_ENABLE_DEMO_DATA !== 'true') return;
  const userCount = (db.prepare('SELECT count(*) as count FROM users').get() as { count: number }).count;
  if (userCount > 0) return;

  const defaultPassword = 'password123';
  const now = new Date().toISOString();

  // Official role users with cryptographically secure PBKDF2 hashes
  const initialUsers = [
    {
      id: 'u-cit-01',
      email: 'citizen@swasthyagrid.tn.gov.in',
      full_name: 'M. Anand (ஆனந்த்)',
      role: 'CITIZEN',
      department_code: null,
      phone: '+91 94440 12345',
      is_demo: 1,
    },
    {
      id: 'u-dep-03',
      email: 'rep@swasthyagrid.tn.gov.in',
      full_name: 'R. Priya (பிரியா - AEE Sanitation)',
      role: 'DEPT_OFFICER',
      department_code: 'CMWSSB',
      phone: '+91 98400 54321',
      is_demo: 1,
    },
    {
      id: 'u-fld-02',
      email: 'field@swasthyagrid.tn.gov.in',
      full_name: 'K. Selvam (செல்வம் - Field Unit)',
      role: 'FIELD_OFFICER',
      department_code: 'SWD',
      phone: '+91 97910 67890',
      is_demo: 1,
    },
    {
      id: 'u-ana-04',
      email: 'analyst@swasthyagrid.tn.gov.in',
      full_name: 'Dr. S. Kugan (குகன் - Epidemiologist)',
      role: 'HEALTH_ANALYST',
      department_code: 'GCC_HEALTH',
      phone: '+91 94441 98765',
      is_demo: 1,
    },
    {
      id: 'u-adm-05',
      email: 'admin@swasthyagrid.tn.gov.in',
      full_name: 'Sundararajan (நிர்வாக அதிகாரி)',
      role: 'SYS_ADMIN',
      department_code: 'GCC_HQ',
      phone: '+91 94440 00001',
      is_demo: 1,
    },
  ];

  const insertUser = db.prepare(`
    INSERT INTO users (id, email, password_hash, salt, full_name, role, department_code, phone, is_demo, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  for (const u of initialUsers) {
    const { hash, salt } = hashPassword(defaultPassword);
    insertUser.run(u.id, u.email, hash, salt, u.full_name, u.role, u.department_code, u.phone, u.is_demo, now, now);
  }

  // Seed initial complaints
  const insertComplaint = db.prepare(`
    INSERT INTO complaints (
      id, tracking_number, citizen_id, citizen_name, category_id, category_code,
      department_code, ward_number, ward_name, title, description, status,
      current_priority, priority_score, lat, lng, address_text, image_url,
      requires_human_review, sla_deadline_hours, hours_elapsed, independent_reports_count,
      proximity_risk_level, ai_summary, ai_observations, ai_missing_info, ai_limitations,
      assigned_officer_id, assigned_officer_name, created_at, updated_at, observed_at
    ) VALUES (
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?
    )
  `);

  for (const c of INITIAL_COMPLAINTS) {
    insertComplaint.run(
      c.id, c.trackingNumber, c.citizenId, c.citizenName, c.categoryId, c.categoryCode,
      c.departmentCode, c.wardNumber, c.wardName, c.title, c.description, c.status,
      c.currentPriority, c.priorityScore, c.lat, c.lng, c.addressText, c.imageUrl || null,
      c.requiresHumanReview ? 1 : 0, c.slaDeadlineHours, c.hoursElapsed, c.independentReportsCount,
      c.proximityRiskLevel, c.aiSummary || null, JSON.stringify(c.aiObservations || []),
      JSON.stringify(c.aiMissingInfo || []), JSON.stringify(c.aiLimitations || []),
      c.assignedOfficerId || null, c.assignedOfficerName || null, c.createdAt, c.updatedAt, c.observedAt
    );
  }

  // Seed initial interventions
  const insertIntervention = db.prepare(`
    INSERT INTO interventions (
      id, complaint_id, tracking_number, intervention_type, description,
      completed_at, officer_id, officer_name, baseline_window_days, evaluation_window_days,
      baseline_incident_count, post_incident_count, rainfall_shift_mm, trend_verdict,
      confidence_rating, identified_confounders, recommendation
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  for (const i of INITIAL_INTERVENTIONS) {
    insertIntervention.run(
      i.id, i.complaintId, i.trackingNumber, i.interventionType, i.description,
      i.completedAt, 'u-fld-02', i.officerName, i.baselineWindowDays, i.evaluationWindowDays,
      i.baselineIncidentCount, i.postIncidentCount, i.rainfallShiftMm ?? null,
      i.trendVerdict || 'IMPROVING', i.confidenceRating || 'HIGH',
      JSON.stringify(i.identifiedConfounders || []), i.recommendation || ''
    );
  }

  // Seed initial audit logs
  const insertAudit = db.prepare(`
    INSERT INTO audit_logs (id, actor_id, actor_name, actor_role, action_type, entity_name, entity_id, metadata, ip_address, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  for (const a of INITIAL_AUDIT_LOGS) {
    insertAudit.run(
      a.id, a.actorId, a.actorName, a.actorRole, a.actionType, a.entityName, a.entityId,
      JSON.stringify(a.metadata || {}), a.ipAddress, a.createdAt
    );
  }

  // Seed initial welcome notification for citizen
  const insertNotification = db.prepare(`
    INSERT INTO notifications (id, user_id, title, message, type, is_read, link, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `);

  insertNotification.run(
    'notif-001',
    'u-cit-01',
    'Field Unit Dispatched',
    'Field Officer K. Selvam has been assigned to inspect your report SG-TN-2026-10492 at Perambur Barracks Road.',
    'STATUS_UPDATE',
    0,
    '/complaints/cmp-101',
    now
  );
}

// User Management Repository Methods
export interface DbUserRecord {
  id: string;
  email: string;
  password_hash: string;
  salt: string;
  full_name: string;
  role: UserRole;
  department_code?: string;
  phone?: string;
  is_demo: number;
  created_at: string;
  updated_at: string;
}

export function findDbUserByEmail(email: string): DbUserRecord | null {
  const row = db.prepare('SELECT * FROM users WHERE LOWER(email) = LOWER(?)').get(email.trim()) as DbUserRecord | undefined;
  return row || null;
}

export function findDbUserById(id: string): DbUserRecord | null {
  const row = db.prepare('SELECT * FROM users WHERE id = ?').get(id) as DbUserRecord | undefined;
  return row || null;
}

export function createDbUser(userData: {
  id?: string;
  email: string;
  password: string;
  fullName: string;
  role: UserRole;
  departmentCode?: string;
  phone?: string;
  isDemo?: boolean;
}): { id: string; email: string; name: string; role: UserRole; departmentCode?: string; isDemo: boolean } {
  const existing = findDbUserByEmail(userData.email);
  if (existing) {
    throw new Error(`Account with email '${userData.email}' already exists.`);
  }

  const id = userData.id || `u-${userData.role.toLowerCase()}-${Date.now().toString().slice(-6)}`;
  const { hash, salt } = hashPassword(userData.password);
  const now = new Date().toISOString();

  db.prepare(`
    INSERT INTO users (id, email, password_hash, salt, full_name, role, department_code, phone, is_demo, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id,
    userData.email.toLowerCase().trim(),
    hash,
    salt,
    userData.fullName,
    userData.role,
    userData.departmentCode || null,
    userData.phone || null,
    userData.isDemo ? 1 : 0,
    now,
    now
  );

  return {
    id,
    email: userData.email.toLowerCase().trim(),
    name: userData.fullName,
    role: userData.role,
    departmentCode: userData.departmentCode,
    isDemo: Boolean(userData.isDemo),
  };
}

export function getDbComplaintHistory(complaintId: string) {
  return db.prepare('SELECT * FROM complaint_status_history WHERE complaint_id = ? ORDER BY timestamp DESC').all(complaintId);
}

export function findNearbyDuplicates(lat: number, lng: number, categoryCode: string, maxMeters = 150): Complaint[] {
  const all = getDbComplaints({ category: categoryCode });
  const duplicates: Complaint[] = [];

  for (const c of all) {
    if (c.status === 'CLOSED' || c.status === 'DUPLICATE') continue;
    const dist = calculateHaversineMeters(lat, lng, c.lat, c.lng);
    if (dist <= maxMeters) {
      duplicates.push(c);
    }
  }

  return duplicates;
}

function calculateHaversineMeters(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371e3;
  const phi1 = (lat1 * Math.PI) / 180;
  const phi2 = (lat2 * Math.PI) / 180;
  const deltaPhi = ((lat2 - lat1) * Math.PI) / 180;
  const deltaLambda = ((lon2 - lon1) * Math.PI) / 180;

  const a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
            Math.cos(phi1) * Math.cos(phi2) *
            Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

// Data Access Repository Helper Methods
export function getDbComplaints(filters: { citizenId?: string; category?: string; priority?: string; status?: string; wardNumber?: number } = {}): Complaint[] {
  let query = 'SELECT * FROM complaints WHERE 1=1';
  const params: (string | number)[] = [];

  if (filters.citizenId) {
    query += ' AND citizen_id = ?';
    params.push(filters.citizenId);
  }
  if (filters.category && filters.category !== 'ALL') {
    query += ' AND category_code = ?';
    params.push(filters.category);
  }
  if (filters.priority && filters.priority !== 'ALL') {
    query += ' AND current_priority = ?';
    params.push(filters.priority);
  }
  if (filters.status && filters.status !== 'ALL') {
    query += ' AND status = ?';
    params.push(filters.status);
  }
  if (filters.wardNumber) {
    query += ' AND ward_number = ?';
    params.push(filters.wardNumber);
  }

  query += ' ORDER BY created_at DESC';

  const rows = db.prepare(query).all(...params) as any[];

  return rows.map(r => ({
    id: r.id,
    trackingNumber: r.tracking_number,
    citizenId: r.citizen_id,
    citizenName: r.citizen_name,
    categoryId: r.category_id,
    categoryCode: r.category_code,
    departmentCode: r.department_code,
    wardNumber: r.ward_number,
    wardName: r.ward_name,
    title: r.title,
    description: r.description,
    status: r.status as ComplaintStatus,
    currentPriority: r.current_priority as PriorityBand,
    priorityScore: r.priority_score,
    lat: r.lat,
    lng: r.lng,
    addressText: r.address_text,
    imageUrl: r.image_url || undefined,
    requiresHumanReview: Boolean(r.requires_human_review),
    slaDeadlineHours: r.sla_deadline_hours,
    hoursElapsed: r.hours_elapsed,
    independentReportsCount: r.independent_reports_count,
    proximityRiskLevel: r.proximity_risk_level,
    aiSummary: r.ai_summary || undefined,
    aiObservations: r.ai_observations ? JSON.parse(r.ai_observations) : [],
    aiMissingInfo: r.ai_missing_info ? JSON.parse(r.ai_missing_info) : [],
    aiLimitations: r.ai_limitations ? JSON.parse(r.ai_limitations) : [],
    assignedOfficerId: r.assigned_officer_id || undefined,
    assignedOfficerName: r.assigned_officer_name || undefined,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    observedAt: r.observed_at,
  }));
}

export function getDbComplaintById(id: string): Complaint | null {
  const r = db.prepare('SELECT * FROM complaints WHERE id = ? OR tracking_number = ?').get(id, id) as any;
  if (!r) return null;

  return {
    id: r.id,
    trackingNumber: r.tracking_number,
    citizenId: r.citizen_id,
    citizenName: r.citizen_name,
    categoryId: r.category_id,
    categoryCode: r.category_code,
    departmentCode: r.department_code,
    wardNumber: r.ward_number,
    wardName: r.ward_name,
    title: r.title,
    description: r.description,
    status: r.status as ComplaintStatus,
    currentPriority: r.current_priority as PriorityBand,
    priorityScore: r.priority_score,
    lat: r.lat,
    lng: r.lng,
    addressText: r.address_text,
    imageUrl: r.image_url || undefined,
    requiresHumanReview: Boolean(r.requires_human_review),
    slaDeadlineHours: r.sla_deadline_hours,
    hoursElapsed: r.hours_elapsed,
    independentReportsCount: r.independent_reports_count,
    proximityRiskLevel: r.proximity_risk_level,
    aiSummary: r.ai_summary || undefined,
    aiObservations: r.ai_observations ? JSON.parse(r.ai_observations) : [],
    aiMissingInfo: r.ai_missing_info ? JSON.parse(r.ai_missing_info) : [],
    aiLimitations: r.ai_limitations ? JSON.parse(r.ai_limitations) : [],
    assignedOfficerId: r.assigned_officer_id || undefined,
    assignedOfficerName: r.assigned_officer_name || undefined,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    observedAt: r.observed_at,
  };
}

export function saveDbComplaint(c: Complaint): void {
  const stmt = db.prepare(`
    INSERT INTO complaints (
      id, tracking_number, citizen_id, citizen_name, category_id, category_code,
      department_code, ward_number, ward_name, title, description, status,
      current_priority, priority_score, lat, lng, address_text, image_url,
      requires_human_review, sla_deadline_hours, hours_elapsed, independent_reports_count,
      proximity_risk_level, ai_summary, ai_observations, ai_missing_info, ai_limitations,
      assigned_officer_id, assigned_officer_name, created_at, updated_at, observed_at
    ) VALUES (
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?, ?,
      ?, ?, ?, ?,
      ?, ?, ?, ?, ?,
      ?, ?, ?, ?, ?
    )
  `);

  stmt.run(
    c.id, c.trackingNumber, c.citizenId, c.citizenName, c.categoryId, c.categoryCode,
    c.departmentCode, c.wardNumber, c.wardName, c.title, c.description, c.status,
    c.currentPriority, c.priorityScore, c.lat, c.lng, c.addressText, c.imageUrl || null,
    c.requiresHumanReview ? 1 : 0, c.slaDeadlineHours, c.hoursElapsed, c.independentReportsCount,
    c.proximityRiskLevel, c.aiSummary || null, JSON.stringify(c.aiObservations || []),
    JSON.stringify(c.aiMissingInfo || []), JSON.stringify(c.aiLimitations || []),
    c.assignedOfficerId || null, c.assignedOfficerName || null, c.createdAt, c.updatedAt, c.observedAt
  );
}

export function updateDbComplaintStatus(
  complaintId: string,
  newStatus: ComplaintStatus,
  assignedOfficerName?: string,
  assignedOfficerId?: string
): Complaint | null {
  const existing = getDbComplaintById(complaintId);
  if (!existing) return null;

  const now = new Date().toISOString();
  const officerName = assignedOfficerName !== undefined ? assignedOfficerName : existing.assignedOfficerName;
  const officerId = assignedOfficerId !== undefined ? assignedOfficerId : existing.assignedOfficerId;

  db.prepare(`
    UPDATE complaints
    SET status = ?, assigned_officer_name = ?, assigned_officer_id = ?, updated_at = ?
    WHERE id = ?
  `).run(newStatus, officerName || null, officerId || null, now, complaintId);

  return getDbComplaintById(complaintId);
}

export function logDbStatusHistory(entry: {
  complaintId: string;
  actorId: string;
  actorName: string;
  actorRole: string;
  previousStatus: string;
  newStatus: string;
  remarks?: string;
}) {
  const id = `hist-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO complaint_status_history (id, complaint_id, actor_id, actor_name, actor_role, previous_status, new_status, remarks, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(id, entry.complaintId, entry.actorId, entry.actorName, entry.actorRole, entry.previousStatus, entry.newStatus, entry.remarks || '', now);
}

export function logDbAudit(entry: {
  actorId: string;
  actorName: string;
  actorRole: UserRole;
  actionType: string;
  entityName: string;
  entityId: string;
  metadata?: Record<string, unknown>;
  ipAddress?: string;
}) {
  const id = `aud-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO audit_logs (id, actor_id, actor_name, actor_role, action_type, entity_name, entity_id, metadata, ip_address, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    id, entry.actorId, entry.actorName, entry.actorRole, entry.actionType, entry.entityName, entry.entityId,
    JSON.stringify(entry.metadata || {}), entry.ipAddress || '127.0.0.1', now
  );
}

export function createDbNotification(entry: {
  userId: string;
  title: string;
  message: string;
  type?: string;
  link?: string;
}) {
  const id = `notif-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO notifications (id, user_id, title, message, type, is_read, link, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(id, entry.userId, entry.title, entry.message, entry.type || 'INFO', 0, entry.link || null, now);
}

export function getDbNotifications(userId: string) {
  return db.prepare('SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50').all(userId);
}

export function markDbNotificationRead(id: string, userId: string) {
  db.prepare('UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?').run(id, userId);
}

export function getDbInterventions(): CivicIntervention[] {
  const rows = db.prepare('SELECT * FROM interventions ORDER BY completed_at DESC').all() as any[];
  return rows.map(r => ({
    id: r.id,
    complaintId: r.complaint_id,
    trackingNumber: r.tracking_number,
    interventionType: r.intervention_type,
    description: r.description,
    completedAt: r.completed_at,
    officerName: r.officer_name,
    baselineWindowDays: r.baseline_window_days,
    evaluationWindowDays: r.evaluation_window_days,
    baselineIncidentCount: r.baseline_incident_count,
    postIncidentCount: r.post_incident_count,
    rainfallShiftMm: r.rainfall_shift_mm ?? undefined,
    trendVerdict: r.trend_verdict,
    confidenceRating: r.confidence_rating,
    identifiedConfounders: r.identified_confounders ? JSON.parse(r.identified_confounders) : [],
    recommendation: r.recommendation || '',
  }));
}

export function saveDbIntervention(i: CivicIntervention): void {
  db.prepare(`
    INSERT INTO interventions (
      id, complaint_id, tracking_number, intervention_type, description,
      completed_at, officer_id, officer_name, baseline_window_days, evaluation_window_days,
      baseline_incident_count, post_incident_count, rainfall_shift_mm, trend_verdict,
      confidence_rating, identified_confounders, recommendation
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    i.id, i.complaintId, i.trackingNumber, i.interventionType, i.description,
    i.completedAt, null, i.officerName, i.baselineWindowDays, i.evaluationWindowDays,
    i.baselineIncidentCount, i.postIncidentCount, i.rainfallShiftMm ?? null,
    i.trendVerdict || 'IMPROVING', i.confidenceRating || 'HIGH',
    JSON.stringify(i.identifiedConfounders || []), i.recommendation || ''
  );
}

export function getDbAuditLogs(): AuditLogEntry[] {
  const rows = db.prepare('SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 100').all() as any[];
  return rows.map(r => ({
    id: r.id,
    actorId: r.actor_id,
    actorName: r.actor_name,
    actorRole: r.actor_role as UserRole,
    actionType: r.action_type,
    entityName: r.entity_name,
    entityId: r.entity_id,
    metadata: r.metadata ? JSON.parse(r.metadata) : undefined,
    ipAddress: r.ip_address,
    createdAt: r.created_at,
  }));
}

// Initialize database schema and seeds on module load
initDatabase();
