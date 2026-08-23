import type { Metadata } from 'next';
import { AuditDashboard } from '@/components/AuditDashboard';

export const metadata: Metadata = { title: 'Audit log' };

export default function AuditPage() {
  return <AuditDashboard />;
}
