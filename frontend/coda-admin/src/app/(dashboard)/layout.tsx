import type { ReactNode } from 'react';
import { AdminShell } from '@/components/AdminShell';
import { requireAdminPage } from '@/lib/auth';

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  await requireAdminPage();
  return <AdminShell>{children}</AdminShell>;
}
