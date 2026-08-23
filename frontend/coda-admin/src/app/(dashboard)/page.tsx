import type { Metadata } from 'next';
import { OverviewDashboard } from '@/components/OverviewDashboard';

export const metadata: Metadata = { title: 'Overview' };

export default function OverviewPage() {
  return <OverviewDashboard />;
}
