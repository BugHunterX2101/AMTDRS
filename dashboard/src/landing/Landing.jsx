import "./landing.css";
import { Nav } from "./Nav";
import { Hero } from "./Hero";
import { Pipeline } from "./Pipeline";
import { Outcomes } from "./Outcomes";
import { Evidence } from "./Evidence";
import { Features } from "./Features";
import { ROI } from "./ROI";
import { Compare } from "./Compare";
import { FAQ } from "./FAQ";
import { Finale } from "./Finale";
import { Footer } from "./Footer";
import { Field } from "../field/Field";

export default function Landing() {
  return (
    <>
      <Field gain={1.4} />
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <Nav />
      <main id="main">
        <Hero />
        <Pipeline />
        <Outcomes />
        <hr className="rule" />
        <Evidence />
        <hr className="rule" />
        <Features />
        <ROI />
        <hr className="rule" />
        <Compare />
        <FAQ />
        <Finale />
      </main>
      <Footer />
    </>
  );
}
