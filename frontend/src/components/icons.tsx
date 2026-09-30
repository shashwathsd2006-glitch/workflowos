import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function baseProps(props: IconProps): IconProps {
  return {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": true,
    ...props,
  };
}

export function IconLogo(props: IconProps) {
  return (
    <svg {...baseProps(props)} strokeWidth={1.8}>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
      <path d="M10 6.5h4a3 3 0 0 1 3 3V14" />
      <path d="M6.5 10v4a3 3 0 0 0 3 3H14" />
    </svg>
  );
}

export function IconOverview(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <rect x="3" y="3" width="8" height="8" rx="2" />
      <rect x="13" y="3" width="8" height="5" rx="2" />
      <rect x="13" y="10" width="8" height="11" rx="2" />
      <rect x="3" y="13" width="8" height="8" rx="2" />
    </svg>
  );
}

export function IconActivity(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M3 12h4l2.5-6 4 12L16 12h5" />
    </svg>
  );
}

export function IconWorkflow(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <rect x="15" y="3" width="6" height="6" rx="1.5" />
      <path d="M8.5 6H14a2 2 0 0 1 2 2v1.5" />
      <path d="M6 8.5V16a2 2 0 0 0 2 2h7.5" />
    </svg>
  );
}

export function IconAutomation(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" />
    </svg>
  );
}

export function IconAnalytics(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M4 20V10" />
      <path d="M10 20V4" />
      <path d="M16 20v-7" />
      <path d="M22 20H2" />
    </svg>
  );
}

export function IconSettings(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M4 6h10" />
      <path d="M18 6h2" />
      <path d="M4 12h4" />
      <path d="M12 12h8" />
      <path d="M4 18h9" />
      <path d="M17 18h3" />
      <circle cx="16" cy="6" r="2" />
      <circle cx="10" cy="12" r="2" />
      <circle cx="15" cy="18" r="2" />
    </svg>
  );
}

export function IconMenu(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
    </svg>
  );
}

export function IconClose(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M6 6l12 12" />
      <path d="M18 6 6 18" />
    </svg>
  );
}

export function IconArrow(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M5 12h14" />
      <path d="m13 6 6 6-6 6" />
    </svg>
  );
}

export function IconSpark(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M12 3v4" />
      <path d="M12 17v4" />
      <path d="M3 12h4" />
      <path d="M17 12h4" />
      <path d="m6.3 6.3 2.8 2.8" />
      <path d="m14.9 14.9 2.8 2.8" />
      <path d="m17.7 6.3-2.8 2.8" />
      <path d="m9.1 14.9-2.8 2.8" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function IconDatabase(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.66 3.13 3 7 3s7-1.34 7-3V6" />
      <path d="M5 12v6c0 1.66 3.13 3 7 3s7-1.34 7-3v-6" />
    </svg>
  );
}

export function IconShield(props: IconProps) {
  return (
    <svg {...baseProps(props)}>
      <path d="M12 3 5 6v5.5c0 4.2 2.9 7.9 7 9 4.1-1.1 7-4.8 7-9V6l-7-3Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
