import { useEffect, useState } from "react";
import { Github, Terminal } from "./icons";

const LINKS = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#evidence", label: "Evidence" },
  { href: "#compare", label: "Compare" },
  { href: "#pricing", label: "Pricing" },
  { href: "#faq", label: "FAQ" },
];

export function Nav() {
  const [open, setOpen] = useState(false);

  // Escape closes the sheet, matching the console's evidence drawer so the two
  // surfaces behave the same way under the keyboard.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

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
            className="btn btn--sm nav-github"
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
          <button
            type="button"
            className="nav-toggle"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((v) => !v)}
          >
            <span className={`burger${open ? " is-open" : ""}`} aria-hidden="true">
              <i />
              <i />
            </span>
          </button>
        </div>
      </div>

      {open && (
        <div className="mobile-nav" id="mobile-nav">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} onClick={() => setOpen(false)}>
              {l.label}
            </a>
          ))}
          <a
            href="https://github.com/BugHunterX2101/AMTDRS"
            target="_blank"
            rel="noreferrer"
            onClick={() => setOpen(false)}
          >
            Source on GitHub
          </a>
        </div>
      )}
    </nav>
  );
}
