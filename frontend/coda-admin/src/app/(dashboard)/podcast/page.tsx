import type { Metadata } from 'next';
import { PodcastDashboard } from '@/components/PodcastDashboard';

export const metadata: Metadata = { title: 'Podcast' };

export default function PodcastPage() {
  return <PodcastDashboard />;
}
