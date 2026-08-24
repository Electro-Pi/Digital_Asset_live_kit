export type DateRange = 'week' | 'month' | 'year';

export type AdminRole = 'owner' | 'operator' | 'viewer';

export type PlanName = 'free' | 'pro' | string;

export type BookingStatus =
  | 'new'
  | 'contacted'
  | 'scheduled'
  | 'done'
  | 'canceled';

export interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
  };
  message?: string;
}

export interface PaginatedData<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface MetricComparison {
  totalUsers?: number;
  newUsers?: number;
  activeUsers?: number;
  proUsers?: number;
  revenue?: number;
  bookings?: number;
  [key: string]: number | undefined;
}

export interface OverviewSummary {
  totalUsers: number;
  newUsers: number;
  activeUsers: number;
  proUsers: number;
  proAdoptionRate?: number;
  revenue: number;
  bookings: number;
  comparisons?: MetricComparison;
}

export interface TimeBucket {
  bucket: string;
  label?: string;
}

export interface OverviewData {
  summary: OverviewSummary;
  growth: Array<TimeBucket & { users: number }>;
  agentUsage: Array<TimeBucket & { minutes: number }>;
  requestUsage: Array<TimeBucket & { tts: number; stt: number; llm: number }>;
  planDistribution: Array<{ name: string; value: number }>;
  quotaHealth: { healthy: number; warning: number; exhausted: number };
  topUsers: Array<{
    id: string;
    email: string;
    name?: string | null;
    plan: string;
    agentMinutes: number;
    requests: number;
  }>;
  bookingTrend: Array<TimeBucket & { bookings: number }>;
  bookingStatus: Array<{ name: string; value: number }>;
  bookingSources: Array<{ name: string; value: number }>;
  paymentStatus: Array<{ name: string; value: number }>;
}

export interface AdminUser {
  id: string;
  email: string;
  name?: string | null;
  avatarUrl?: string | null;
  provider: string;
  confirmed: boolean;
  createdAt: string;
  lastSignInAt?: string | null;
  lastActiveAt?: string | null;
  plan: PlanName;
  agentUsedMinutes: number;
  agentLimitMinutes: number;
  ttsUsed: number;
  ttsLimit: number;
  sttUsed: number;
  sttLimit: number;
  llmUsed: number;
  llmLimit: number;
  credits: number;
}

export interface QuotaSnapshot {
  agentUsedMinutes: number;
  agentLimitMinutes: number;
  ttsUsed: number;
  ttsLimit: number;
  sttUsed: number;
  sttLimit: number;
  llmUsed: number;
  llmLimit: number;
  periodStart?: string | null;
  periodEnd?: string | null;
}

export interface LimitPayload {
  agentMinutes: number;
  ttsRequests: number;
  sttRequests: number;
  llmRequests: number;
}

export interface OverridePayload {
  agentMinutes: number | null;
  ttsRequests: number | null;
  sttRequests: number | null;
  llmRequests: number | null;
}

export interface UserDetailData {
  user: AdminUser;
  quota: QuotaSnapshot;
  override: OverridePayload;
  credits: number;
  recentUsage: Array<
    TimeBucket & {
      agentMinutes: number;
      tts: number;
      stt: number;
      llm: number;
    }
  >;
}

export interface PlanLimits extends LimitPayload {
  id: string;
  name: string;
  displayName: string;
  isActive: boolean;
}

export interface DemoBooking {
  id: string;
  userId?: string | null;
  name: string;
  email: string;
  phone?: string | null;
  company?: string | null;
  jobTitle?: string | null;
  message?: string | null;
  source?: string | null;
  preferredDate?: string | null;
  preferredTime?: string | null;
  status: BookingStatus;
  createdAt: string;
  updatedAt?: string | null;
}

export interface AuditEntry {
  id: string;
  actorEmail: string;
  actorRole: AdminRole;
  action: string;
  targetType: string;
  targetId?: string | null;
  before?: unknown;
  after?: unknown;
  ipAddress?: string | null;
  createdAt: string;
}

export interface AdminIdentity {
  id: string;
  email: string;
  role: AdminRole;
}

export type ApiKeyScope = 'tts' | 'agent' | 'stt' | 'llm';
export type ApiKeyStatus = 'active' | 'revoked' | 'expired';

export interface ApiKey {
  id: string;
  ownerEmail: string;
  ownerId: string;
  label: string;
  /** coda_sk_live_ab•••••••• — the raw value is never returned by a list. */
  masked: string;
  scopes: ApiKeyScope[];
  maxConcurrent: number;
  status: ApiKeyStatus;
  expiresAt: string | null;
  revokedAt: string | null;
  lastUsedAt: string | null;
  createdAt: string;
  /** Agent minutes inside the selected window, and for all time. */
  liveMinutes: number;
  liveMinutesLifetime: number;
  ttsCharacters: number;
}

/** POST /api/keys — `key` is present exactly once and is never retrievable again. */
export interface CreatedApiKey {
  id: string;
  label: string;
  prefix: string;
  scopes: ApiKeyScope[];
  maxConcurrent: number;
  expiresAt: string | null;
  createdAt: string;
  ownerId: string;
  ownerEmail: string;
  key: string;
}
