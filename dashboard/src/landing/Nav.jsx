import { Github, Terminal } from "./icons";

const LINKS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#evidence", label: "Evidence" },
  { href: "#compare", label: "Compare" },
  { href: "#pricing", label: "Pricing" },
  { href: "#faq", label: "FAQ" },
];

export function Nav() {
  return (
    <nav className="nav" aria-label="Primary">
      <div className="wrap nav-inner">
        <a className="brand" href="#top" aria-label="Principal home">
          <Terminal className="mark" aria-hidden="true" />
          Principal
        </a>

        <div className="nav-links">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href}>
              {l.label}
            </a>
          ))}
        </div>

        <div className="nav-spacer" />

        <div className="nav-cta">
          <a
            className="btn btn--sm"
            href="https://github.com/BugHunterX2101/AMTDRS"
            target="_blank"
            rel="noreferrer"
          >
            <Github aria-hidden="true" />
            <span>Star on GitHub</span>
          </a>
          <a className="btn btn--sm btn--primary" href="/app">
            Open console
          </a>
        </div>
      </div>
    </nav>
  );
}
