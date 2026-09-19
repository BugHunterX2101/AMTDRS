/* Hand-written, stroke-based icon set (Lucide-style paths, 24x24, currentColor).
   Kept inline rather than adding an icon-library dependency: the landing page
   uses about a dozen glyphs total, and the console bundle already ships with
   zero icon dependencies — matching that keeps the whole dashboard workspace
   dependency-light. */

const base = {
  width: "1em",
  height: "1em",
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export const Check = (p) => (
  <svg {...base} {...p}>
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

export const Shield = (p) => (
  <svg {...base} {...p}>
    <path d="M12 2 4 5v6c0 5 3.5 8.5 8 11 4.5-2.5 8-6 8-11V5l-8-3Z" />
  </svg>
);

export const GitBranch = (p) => (
  <svg {...base} {...p}>
    <circle cx="6" cy="4" r="2.4" />
    <circle cx="6" cy="20" r="2.4" />
    <circle cx="18" cy="9" r="2.4" />
    <path d="M6 6.4V17.6" />
    <path d="M6 12c0-3 3-5 8-5.2" />
  </svg>
);

export const Zap = (p) => (
  <svg {...base} {...p}>
    <path d="M12 2 4 14h6l-1 8 9-13h-6l1-7Z" />
  </svg>
);

export const Terminal = (p) => (
  <svg {...base} {...p}>
    <path d="M4 5h16v14H4z" />
    <path d="m7 9 3 3-3 3" />
    <path d="M13 15h4" />
  </svg>
);

export const GitPullRequest = (p) => (
  <svg {...base} {...p}>
    <circle cx="6" cy="6" r="2.4" />
    <circle cx="6" cy="18" r="2.4" />
    <circle cx="18" cy="6" r="2.4" />
    <path d="M6 8.4V15.6" />
    <path d="M18 8.4v3.6a4 4 0 0 1-4 4h-2" />
  </svg>
);

export const Lock = (p) => (
  <svg {...base} {...p}>
    <rect x="4.5" y="10.5" width="15" height="9.5" rx="2" />
    <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" />
  </svg>
);

export const Github = (p) => (
  <svg viewBox="0 0 24 24" fill="currentColor" width="1em" height="1em" {...p}>
    <path d="M12 .5A11.5 11.5 0 0 0 .5 12.26c0 5.2 3.34 9.6 7.98 11.16.58.11.79-.26.79-.57v-2.02c-3.25.72-3.94-1.6-3.94-1.6-.53-1.37-1.3-1.74-1.3-1.74-1.06-.75.08-.73.08-.73 1.18.08 1.8 1.24 1.8 1.24 1.04 1.83 2.73 1.3 3.4 1 .1-.77.41-1.3.74-1.6-2.6-.3-5.33-1.34-5.33-5.96 0-1.32.46-2.4 1.22-3.24-.12-.31-.53-1.55.12-3.23 0 0 1-.33 3.28 1.24a11.1 11.1 0 0 1 5.98 0c2.28-1.57 3.28-1.24 3.28-1.24.65 1.68.24 2.92.12 3.23.76.84 1.22 1.92 1.22 3.24 0 4.63-2.73 5.65-5.34 5.95.42.37.79 1.1.79 2.22v3.29c0 .31.21.69.8.57A11.5 11.5 0 0 0 23.5 12.26 11.5 11.5 0 0 0 12 .5Z" />
  </svg>
);

export const ChevronDown = (p) => (
  <svg {...base} {...p}>
    <path d="m6 9 6 6 6-6" />
  </svg>
);

export const Play = (p) => (
  <svg viewBox="0 0 24 24" fill="currentColor" width="1em" height="1em" {...p}>
    <path d="M8 5v14l11-7Z" />
  </svg>
);

export const ArrowRight = (p) => (
  <svg {...base} {...p}>
    <path d="M5 12h14" />
    <path d="m13 6 6 6-6 6" />
  </svg>
);

export const Layers = (p) => (
  <svg {...base} {...p}>
    <path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 13 9 5 9-5" />
  </svg>
);

export const FlaskConical = (p) => (
  <svg {...base} {...p}>
    <path d="M9 3h6" />
    <path d="M10 3v6.2L4.8 18a1.8 1.8 0 0 0 1.55 2.7h11.3A1.8 1.8 0 0 0 19.2 18L14 9.2V3" />
    <path d="M7.5 15h9" />
  </svg>
);

export const XCircle = (p) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m9.5 9.5 5 5" />
    <path d="m14.5 9.5-5 5" />
  </svg>
);

export const Cpu = (p) => (
  <svg {...base} {...p}>
    <rect x="6" y="6" width="12" height="12" rx="1.5" />
    <rect x="9.5" y="9.5" width="5" height="5" rx="0.5" />
    <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
  </svg>
);

export const Scale = (p) => (
  <svg {...base} {...p}>
    <path d="M12 3v18" />
    <path d="M5 7h14" />
    <path d="m5 7-3 6a3.2 3.2 0 0 0 6 0Z" />
    <path d="m19 7 3 6a3.2 3.2 0 0 1-6 0Z" />
    <path d="M8 21h8" />
  </svg>
);
