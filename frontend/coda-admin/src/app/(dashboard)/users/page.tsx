import type { Metadata } from 'next';
import { UsersDashboard } from '@/components/UsersDashboard';

export const metadata: Metadata = { title: 'Users' };

export default function UsersPage() {
  return <UsersDashboard />;
}
