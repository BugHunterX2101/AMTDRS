import { Terminal } from "./icons";

const REPO = "https://github.com/BugHunterX2101/AMTDRS";

const COLUMNS = [
  {
    h: "Product",
    links: [
      { label: "How it works", href: "#how-it-works" },
      { label: "Evidence", href: "#evidence" },
      { label: "Guardrails", href: "#guardrails" },
      { label: "Live console", href: "/app/" },
    ],
  },
  {
    h: "Docs",
    links: [
      { label: "Product spec", href: `${REPO}/blob/main/docs/PRD.md` },
      { label: "Architecture", href: `${REPO}/blob/main/docs/DESIGN.md` },
      { label: "Technical spec", href: `${REPO}/blob/main/docs/SPEC.md` },
      { label: "Threat model", href: `${REPO}/blob/main/docs/THREAT_MODEL.md` },
    ],
  },
  {
    h: "Project",
    links: [
      { label: "Source on GitHub", href: REPO },
      { label: "Hackathon submission", href: `${REPO}/blob/main/docs/SUBMISSION.md` },
      { label: "Deploy guide", href: `${REPO}/blob/main/docs/DEPLOY.md` },
      { label: "License (Apache 2.0)", href: `${REPO}/blob/main/LICENSE` },
    ],
  },
];

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <div className="footer-inner">
          <div>
            <a className="brand" href="#top" style={{ marginBottom: 10 }}>
              <Terminal aria-hidden="true" />
              Principal
            </a>
            <p style={{ color: "var(--dim)", fontSize: 13.5, maxWidth: "28ch", marginTop: 10 }}>
              Verification-gated parallel refactoring, built for the Nebius ×
              NVIDIA Global AI Hackathon 2026.
            </p>
          </div>
          {COLUMNS.map((c) => (
            <div className="footer-col" key={c.h}>
              <h4>{c.h}</h4>
              {c.links.map((l) => (
                <a
                  key={l.label}
                  href={l.href}
                  target={l.href.startsWith("http") ? "_blank" : undefined}
                  rel={l.href.startsWith("http") ? "noreferrer" : undefined}
                >
                  {l.label}
                </a>
              ))}
            </div>
          ))}
        </div>

        <div className="footer-legal">
          <span>© 2026 Principal. Source available under Apache License 2.0.</span>
          <span>Built on Nebius Token Factory · NVIDIA Nemotron 3 · not affiliated with either.</span>
        </div>
      </div>
    </footer>
  );
}
