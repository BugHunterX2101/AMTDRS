import { useMemo, useState } from "react";
import { useReveal } from "./useReveal";

const HOURS_PER_ENGINEER_YEAR = 2080;

const fmtUSD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const fmtNum = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function ROI() {
  const [ref, shown] = useReveal();
  const [engineers, setEngineers] = useState(40);
  const [salary, setSalary] = useState(180000);
  const [debtShare, setDebtShare] = useState(33);
  const [addressable, setAddressable] = useState(20);

  const out = useMemo(() => {
    const hourlyRate = salary / HOURS_PER_ENGINEER_YEAR;
    const debtHours = engineers * HOURS_PER_ENGINEER_YEAR * (debtShare / 100);
    const addressableHours = debtHours * (addressable / 100);
    const value = addressableHours * hourlyRate;
    return { debtHours, addressableHours, value };
  }, [engineers, salary, debtShare, addressable]);

  return (
    <section className="section" id="roi" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="eyebrow">The business case</div>
        <h2 className="section-title">
          What mechanical migration work actually costs your team.
        </h2>
        <p className="section-lede">
          Not a promise — an estimate you can argue with. Move the sliders; the
          assumptions and sources are stated below, and every one of them is
          adjustable because your team's numbers are not the industry average.
        </p>

        <div className="roi" style={{ marginTop: 40 }}>
          <div className="card">
            <div className="card-head">
              <h3>Your team</h3>
            </div>
            <div className="card-body roi-controls">
              <div className="field">
                <label htmlFor="r-eng">
                  Engineers on the team <b>{engineers}</b>
                </label>
                <input
                  id="r-eng"
                  type="range"
                  min="5"
                  max="300"
                  step="5"
                  value={engineers}
                  onChange={(e) => setEngineers(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="r-sal">
                  Fully-loaded cost per engineer / year{" "}
                  <b>{fmtUSD.format(salary)}</b>
                </label>
                <input
                  id="r-sal"
                  type="range"
                  min="80000"
                  max="320000"
                  step="5000"
                  value={salary}
                  onChange={(e) => setSalary(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="r-debt">
                  Share of time spent on technical debt <b>{debtShare}%</b>
                </label>
                <input
                  id="r-debt"
                  type="range"
                  min="10"
                  max="50"
                  step="1"
                  value={debtShare}
                  onChange={(e) => setDebtShare(Number(e.target.value))}
                />
              </div>
              <div className="field">
                <label htmlFor="r-addr">
                  Share of that which is mechanical migration work{" "}
                  <b>{addressable}%</b>
                </label>
                <input
                  id="r-addr"
                  type="range"
                  min="5"
                  max="60"
                  step="1"
                  value={addressable}
                  onChange={(e) => setAddressable(Number(e.target.value))}
                />
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Illustrative annual exposure</h3>
            </div>
            <div className="card-body">
              <div className="roi-out">
                <div className="roi-stat roi-stat--hero">
                  <div className="k">Engineer-hours in the addressable class, per year</div>
                  <div className="v">{fmtNum.format(out.addressableHours)} hrs</div>
                </div>
                <div className="roi-stat">
                  <div className="k">Total hours lost to debt / year</div>
                  <div className="v">{fmtNum.format(out.debtHours)}</div>
                </div>
                <div className="roi-stat">
                  <div className="k">Fully-loaded value of that slice</div>
                  <div className="v">{fmtUSD.format(out.value)}</div>
                </div>
              </div>

              <div className="roi-assumptions">
                Debt-time default (33%) is Stripe &amp; Harris Poll, 2018 —{" "}
                developers reported roughly 13.5 of a 41.1-hour week on technical
                debt. Accumulated US technical-debt principal was estimated at
                $1.52T in{" "}
                <a
                  href="https://www.it-cisq.org/the-cost-of-poor-quality-software-in-the-us-a-2022-report/"
                  target="_blank"
                  rel="noreferrer"
                >
                  CISQ's 2022 report
                </a>
                . The "mechanical migration" share and your own salary and headcount
                figures are assumptions you set, not published statistics — this
                model exists to be argued with, not cited as fact. Principal targets
                exactly one slice of this: interface evolution work with a
                computable blast radius, not judgement calls about what's worth
                paying down.
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
