import type { NavigationRouteNarration } from './navigation';

export const NAVIGATION_ROUTE_NARRATION_TOPIC = 'navigator.route.narration';

export interface NavigationNarrationPublishOptions {
  reliable: true;
  topic: typeof NAVIGATION_ROUTE_NARRATION_TOPIC;
}

export async function publishNavigationRouteNarration(
  publishData: (payload: Uint8Array<ArrayBuffer>, options: NavigationNarrationPublishOptions) => Promise<void>,
  narration: NavigationRouteNarration,
) {
  const payload = new TextEncoder().encode(JSON.stringify(narration));
  await publishData(payload, {
    reliable: true,
    topic: NAVIGATION_ROUTE_NARRATION_TOPIC,
  });
}
