import type { Metadata } from 'next';
import { LimitsDashboard } from '@/components/LimitsDashboard';

export const metadata: Metadata = { title: 'Plan limits' };

export default function LimitsPage() {
  return <LimitsDashboard />;
}
