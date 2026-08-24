import type { Metadata } from 'next';
import { KeysDashboard } from '@/components/KeysDashboard';

export const metadata: Metadata = { title: 'API keys' };

export default function KeysPage() {
  return <KeysDashboard />;
}
