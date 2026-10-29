import crypto from 'node:crypto';
import { Request, Response, NextFunction } from 'express';
import { UserRole, UserSession } from '../types/index.ts';
import { db, getDbComplaintById } from '../db/database.ts';

const JWT_SECRET = process.env.SESSION_SECRET || (process.env.NODE_ENV === 'production' ? '' : crypto.randomBytes(32).toString('hex'));
if (!JWT_SECRET) {
  throw new Error('SESSION_SECRET must be configured before starting the React/Express server in production.');
}
const TOKEN_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

export interface TokenPayload {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  departmentCode?: string;
  exp: number;
}

export interface AuthenticatedRequest extends Request {
  user?: TokenPayload;
}

/**
 * Generate a cryptographically signed HMAC-SHA256 token
 */
export function generateSignedToken(user: UserSession): string {
  const payload: TokenPayload = {
    id: user.id,
    email: user.email,
    name: user.name,
    role: user.role,
    departmentCode: user.departmentCode,
    exp: Date.now() + TOKEN_TTL_MS,
  };

  const payloadEncoded = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const signature = crypto
    .createHmac('sha256', JWT_SECRET)
    .update(payloadEncoded)
    .digest('base64url');

  return `${payloadEncoded}.${signature}`;
}

/**
 * Verify HMAC-SHA256 signed token
 */
export function verifySignedToken(token: string): TokenPayload | null {
  if (!token || !token.includes('.')) return null;

  const [payloadEncoded, signature] = token.split('.');
  if (!payloadEncoded || !signature) return null;

  const expectedSignature = crypto
    .createHmac('sha256', JWT_SECRET)
    .update(payloadEncoded)
    .digest('base64url');

  try {
    const isSigValid = crypto.timingSafeEqual(
      Buffer.from(signature),
      Buffer.from(expectedSignature)
    );
    if (!isSigValid) return null;

    const payload: TokenPayload = JSON.parse(
      Buffer.from(payloadEncoded, 'base64url').toString('utf8')
    );

    if (Date.now() > payload.exp) {
      return null; // Expired token
    }

    return payload;
  } catch {
    return null;
  }
}

/**
 * Express middleware to authenticate token
 */
export function authenticateToken(
  req: AuthenticatedRequest,
  res: Response,
  next: NextFunction
) {
  const authHeader = req.headers['authorization'];
  let token: string | undefined;

  if (authHeader && authHeader.startsWith('Bearer ')) {
    token = authHeader.split(' ')[1];
  } else if (req.headers['x-auth-token']) {
    token = req.headers['x-auth-token'] as string;
  }

  if (!token) {
    return res.status(401).json({ error: 'Authentication required. Missing Bearer token.' });
  }

  const payload = verifySignedToken(token);
  if (!payload) {
    return res.status(401).json({ error: 'Invalid, malformed, or expired session token.' });
  }

  req.user = payload;
  next();
}

/**
 * Express middleware to check if user has one of allowed roles
 */
export function requireRole(allowedRoles: UserRole[]) {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    if (!req.user) {
      return res.status(401).json({ error: 'Authentication required.' });
    }

    if (!allowedRoles.includes(req.user.role)) {
      return res.status(403).json({
        error: `Access denied. Role '${req.user.role}' is not authorized for this resource. Required: [${allowedRoles.join(', ')}]`,
      });
    }

    next();
  };
}

/**
 * IDOR Prevention: Ensures that citizen can only access/modify their own complaint,
 * while privileged roles (DEPT_OFFICER, FIELD_OFFICER, HEALTH_ANALYST, SYS_ADMIN) can access.
 */
export function requireComplaintOwnershipOrRole(
  complaintIdParam = 'id',
  allowedPrivilegedRoles: UserRole[] = ['DEPT_OFFICER', 'FIELD_OFFICER', 'HEALTH_ANALYST', 'SYS_ADMIN']
) {
  return (req: AuthenticatedRequest, res: Response, next: NextFunction) => {
    if (!req.user) {
      return res.status(401).json({ error: 'Authentication required.' });
    }

    const complaintId = req.params[complaintIdParam];
    if (!complaintId) {
      return res.status(400).json({ error: 'Complaint identifier parameter missing.' });
    }

    const complaint = getDbComplaintById(complaintId);
    if (!complaint) {
      return res.status(404).json({ error: `Complaint '${complaintId}' not found.` });
    }

    // If user has a privileged role, allow
    if (allowedPrivilegedRoles.includes(req.user.role)) {
      return next();
    }

    // For CITIZEN role, must match citizenId or be public read-safe
    if (req.user.role === 'CITIZEN' && complaint.citizenId !== req.user.id) {
      return res.status(403).json({
        error: 'Forbidden (IDOR Protection): You cannot view or modify incidents filed by another citizen.',
      });
    }

    next();
  };
}
