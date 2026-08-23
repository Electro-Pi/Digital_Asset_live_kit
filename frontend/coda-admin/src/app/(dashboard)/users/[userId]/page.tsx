import type { Metadata } from 'next';
import { UserDetail } from '@/components/UserDetail';

export const metadata: Metadata = { title: 'User details' };

export default async function UserDetailPage({ params }: { params: Promise<{ userId: string }> }) {
  const { userId } = await params;
  return <UserDetail userId={userId} />;
}
