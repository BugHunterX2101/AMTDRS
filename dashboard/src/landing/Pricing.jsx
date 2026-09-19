import { useReveal } from "./useReveal";
import { Check } from "./icons";

/* Pricing structure and CTA-card layout borrowed from 21st.dev's "Pricing"
   component (sshahaider/pricing) — reimplemented in plain CSS instead of
   framer-motion/radix so the landing bundle adds zero new dependencies to a
   workspace that otherwise ships only react + react-dom. The BorderTrail
   highlight effect is recreated with a CSS conic-gradient mask + @property
   angle animation (see .plan--featured in landing.css).

   Nothing here is a live checkout. Principal is Apache-2.0 and free to run
   today; these are illustrative roadmap tiers, priced against real 2026
   market comparables, sized to a value-based model rather than seats — see
   the footnote. No CTA collects payment; every button links to the repo or
   a plain email. */
const PLANS = [
  {
    id: "oss",
    name: "Self-hosted",
    info: "Run the whole system yourself, today.",
    price: "Free",
    per: "Apache 2.0",
    features: [
      "Full source, Apache 2.0",
      "Bring your own Token Factory project",
      "Unlimited jobs — your sandboxes, your budget",
      "Dashboard and PR bot included",
      "Community support via GitHub issues",
    ],
    btn: { text: "Clone the repo", href: "https://github.com/BugHunterX2101/AMTDRS" },
  },
  {
    id: "team",
    name: "Team",
    info: "Managed orchestrator. Pay for outcomes, not attempts.",
    price: "$299",
    per: "/mo + $25 per verified PR",
    features: [
      "Hosted orchestrator and dashboard, no infra to run",
      "10 verified PRs/mo included, then metered",
      "Nothing is billed for a candidate that didn't survive verification",
      "Priority routing across Nano / Super / Ultra",
      "Slack + email support, next-business-day",
    ],
    btn: { text: "Talk to us", href: "mailto:hello@principal.dev?subject=Team%20plan" },
    featured: true,
  },
  {
    id: "enterprise",
    name: "Enterprise",
    info: "Dedicated capacity and compliance controls.",
    price: "Custom",
    per: "annual contract",
    features: [
      "Dedicated VPC / isolated sandbox pool",
      "SSO and full audit-log export",
      "Migration-sprint SLA with an assigned engineer",
      "Volume pricing per verified PR",
      "Custom refactor-class onboarding",
    ],
    btn: { text: "Contact sales", href: "mailto:hello@principal.dev?subject=Enterprise" },
  },
];

export function Pricing() {
  const [ref, shown] = useReveal();
  return (
    <section className="section" id="pricing" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="eyebrow">Pricing</div>
        <h2 className="section-title">You don't pay for candidates that fail.</h2>
        <p className="section-lede">
          For comparison: GitHub Copilot Enterprise runs roughly $60/seat/month
          all-in, Cursor Business $40/seat/month — both bill for access, whether or
          not anything shippable comes out of a session. Principal's Team tier bills
          per verified, merge-ready PR, because that's the only thing this
          architecture ever produces for a human to see.
        </p>

        <div className="plans">
          {PLANS.map((p) => (
            <div className={`plan${p.featured ? " plan--featured" : ""}`} key={p.id}>
              {p.featured && <span className="plan-tag">Most teams start here</span>}
              <div className="plan-head">
                <div className="plan-name">{p.name}</div>
                <div className="plan-info">{p.info}</div>
                <div className="plan-price">
                  <span className="amount">{p.price}</span>
                  <span className="per">{p.per}</span>
                </div>
              </div>
              <ul className="plan-features">
                {p.features.map((f) => (
                  <li key={f}>
                    <Check aria-hidden="true" />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
              <div className="plan-foot">
                <a
                  className={`btn${p.featured ? " btn--primary" : ""}`}
                  href={p.btn.href}
                  target={p.btn.href.startsWith("http") ? "_blank" : undefined}
                  rel={p.btn.href.startsWith("http") ? "noreferrer" : undefined}
                >
                  {p.btn.text}
                </a>
              </div>
            </div>
          ))}
        </div>

        <p className="price-footnote">
          Illustrative roadmap pricing, not a live billing system — Principal is
          Apache-2.0 and free to self-host right now. Market comparables above are
          public 2026 list prices for GitHub Copilot Enterprise and Cursor Business.
        </p>
      </div>
    </section>
  );
}
