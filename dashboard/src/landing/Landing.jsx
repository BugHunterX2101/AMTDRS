import "./landing.css";
import { Nav } from "./Nav";
import { Hero } from "./Hero";
import { Pipeline } from "./Pipeline";
import { Evidence } from "./Evidence";
import { Features } from "./Features";
import { ROI } from "./ROI";
import { Compare } from "./Compare";
import { Pricing } from "./Pricing";
import { FAQ } from "./FAQ";
import { Finale } from "./Finale";
import { Footer } from "./Footer";

export default function Landing() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <Nav />
      <main id="main">
        <Hero />
        <Pipeline />
        <hr className="rule" />
        <Evidence />
        <hr className="rule" />
        <Features />
        <ROI />
        <hr className="rule" />
        <Compare />
        <Pricing />
        <FAQ />
        <Finale />
      </main>
      <Footer />
    </>
  );
}
