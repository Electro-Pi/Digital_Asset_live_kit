import type { SVGProps } from 'react';

export type IconName =
  | 'activity'
  | 'arrow-down'
  | 'arrow-left'
  | 'arrow-right'
  | 'arrow-up'
  | 'calendar'
  | 'check'
  | 'chevron-down'
  | 'chevron-right'
  | 'clock'
  | 'close'
  | 'database'
  | 'external'
  | 'eye'
  | 'file'
  | 'filter'
  | 'gauge'
  | 'logout'
  | 'mail'
  | 'menu'
  | 'message'
  | 'microphone'
  | 'money'
  | 'more'
  | 'phone'
  | 'refresh'
  | 'save'
  | 'search'
  | 'settings'
  | 'shield'
  | 'sparkles'
  | 'trend'
  | 'user'
  | 'users'
  | 'volume'
  | 'warning'
  | 'wave';

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 20, ...props }: IconProps) {
  const content = (() => {
    switch (name) {
      case 'activity':
        return <path d="M3 12h4l2-7 4 14 2-7h6" />;
      case 'arrow-down':
        return <><path d="m6 9 6 6 6-6" /><path d="M12 3v12" /></>;
      case 'arrow-left':
        return <><path d="m15 18-6-6 6-6" /><path d="M20 12H9" /></>;
      case 'arrow-right':
        return <><path d="m9 18 6-6-6-6" /><path d="M4 12h11" /></>;
      case 'arrow-up':
        return <><path d="m6 15 6-6 6 6" /><path d="M12 21V9" /></>;
      case 'calendar':
        return <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>;
      case 'check':
        return <path d="m5 12 4 4L19 6" />;
      case 'chevron-down':
        return <path d="m7 10 5 5 5-5" />;
      case 'chevron-right':
        return <path d="m9 18 6-6-6-6" />;
      case 'clock':
        return <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>;
      case 'close':
        return <path d="m6 6 12 12M18 6 6 18" />;
      case 'database':
        return <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" /></>;
      case 'external':
        return <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" /></>;
      case 'eye':
        return <><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.5" /></>;
      case 'file':
        return <><path d="M6 2h8l5 5v15H6z" /><path d="M14 2v6h5M9 13h6M9 17h6" /></>;
      case 'filter':
        return <path d="M3 5h18l-7 8v6l-4 2v-8z" />;
      case 'gauge':
        return <><path d="M4 18a9 9 0 1 1 16 0" /><path d="m12 13 4-4M8 18h8" /></>;
      case 'logout':
        return <><path d="M10 5H5v14h5M14 8l4 4-4 4M18 12H9" /></>;
      case 'mail':
        return <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m4 7 8 6 8-6" /></>;
      case 'menu':
        return <path d="M4 7h16M4 12h16M4 17h16" />;
      case 'message':
        return <path d="M21 14a4 4 0 0 1-4 4H9l-5 3v-7a9 9 0 1 1 17 0Z" />;
      case 'microphone':
        return <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></>;
      case 'money':
        return <><circle cx="12" cy="12" r="9" /><path d="M15 8.5c-.6-.7-1.5-1-2.7-1-1.5 0-2.7.8-2.7 2s1 1.8 2.8 2.2c1.8.4 2.8.9 2.8 2.3s-1.2 2.4-3 2.4c-1.3 0-2.4-.4-3.2-1.3M12 5.5v13" /></>;
      case 'more':
        return <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>;
      case 'phone':
        return <path d="M6.5 3h3l1.5 5-2 1.5a15 15 0 0 0 5.5 5.5l1.5-2 5 1.5v3A3.5 3.5 0 0 1 17.5 21C9.5 21 3 14.5 3 6.5A3.5 3.5 0 0 1 6.5 3Z" />;
      case 'refresh':
        return <><path d="M20 7v5h-5" /><path d="M19 12a7 7 0 1 0-2 5" /></>;
      case 'save':
        return <><path d="M5 3h12l3 3v15H4V4a1 1 0 0 1 1-1Z" /><path d="M8 3v6h8V3M8 21v-7h8v7" /></>;
      case 'search':
        return <><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></>;
      case 'settings':
        return <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>;
      case 'shield':
        return <><path d="m12 3 8 3v5c0 5-3.3 8.5-8 10-4.7-1.5-8-5-8-10V6z" /><path d="m8.5 12 2.2 2.2 4.8-5" /></>;
      case 'sparkles':
        return <><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2zM18 14l.7 2.3L21 17l-2.3.7L18 20l-.7-2.3L15 17l2.3-.7zM5 13l.6 1.9 1.9.6-1.9.6L5 18l-.6-1.9-1.9-.6 1.9-.6z" /></>;
      case 'trend':
        return <><path d="m4 16 5-5 4 4 7-8" /><path d="M15 7h5v5" /></>;
      case 'user':
        return <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>;
      case 'users':
        return <><circle cx="9" cy="8" r="4" /><path d="M2 21a7 7 0 0 1 14 0M16 4a4 4 0 0 1 0 8M18 14a7 7 0 0 1 4 7" /></>;
      case 'volume':
        return <><path d="M5 9H2v6h3l5 4V5zM14 9a4 4 0 0 1 0 6M17 6a8 8 0 0 1 0 12" /></>;
      case 'warning':
        return <><path d="M12 3 2.5 20h19z" /><path d="M12 9v4M12 17h.01" /></>;
      case 'wave':
        return <path d="M3 12h2l2-7 3 14 3-14 3 14 2-7h3" />;
    }
  })();

  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      {...props}
    >
      {content}
    </svg>
  );
}
