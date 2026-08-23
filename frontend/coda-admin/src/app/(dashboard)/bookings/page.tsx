import type { Metadata } from 'next';
import { BookingsDashboard } from '@/components/BookingsDashboard';

export const metadata: Metadata = { title: 'Demo bookings' };

export default function BookingsPage() {
  return <BookingsDashboard />;
}
