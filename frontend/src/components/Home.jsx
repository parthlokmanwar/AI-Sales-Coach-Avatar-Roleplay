/* ============================================================================
   Posiview Edge IQ — Marketing / Landing page  (DEMO — easy to remove)
   ----------------------------------------------------------------------------
   Fully self-contained. Shown to logged-out visitors before the sign-in page.
   To remove entirely: delete Home.jsx + Home.css and the small DEMO block in
   App.jsx (see the comment there). Nothing else depends on this file.
   All class names are namespaced `.pvh-` so they never collide with the app.
   ============================================================================ */
import { useEffect } from 'react';
import {
  Sparkles, Headphones, Users, MessageSquare, Languages, ShieldCheck,
  ArrowRight, Check, CheckCircle2, Phone, Mail, MapPin, Lock,
  Linkedin, Facebook, Instagram, Twitter, Youtube,
} from 'lucide-react';
import './Home.css';

const LOGO = '/posiview-logo.png';
const PARENT_URL = 'https://posiviewtech.com/';

const FEATURES = [
  {
    icon: Sparkles,
    title: 'AI Pitch Generation',
    text: 'Turn a one-line scenario into a complete, objection-ready pitch — opening hook, discovery questions, product facts and a closing strategy in seconds.',
  },
  {
    icon: Headphones,
    title: 'Live Call Assistance',
    text: 'Real-time talking points, rebuttals and cues while you are on the call, so you never lose the thread or miss an opening.',
  },
  {
    icon: Users,
    title: 'Roleplay Simulation',
    text: 'Practise against a lifelike AI customer avatar and get a scored coaching report after every session to sharpen your craft.',
  },
  {
    icon: MessageSquare,
    title: 'Document-Grounded Chat',
    text: 'Ask anything about your products and policies. Every answer is cited from your own uploaded playbooks — grounded, not guessed.',
  },
  {
    icon: Languages,
    title: 'Six Indian Languages',
    text: 'Generate pitches and coaching in English, Hindi, Marathi, Tamil, Bengali and Telugu — meet every customer in their language.',
  },
  {
    icon: ShieldCheck,
    title: 'Enterprise-Grade Security',
    text: 'Role-based access, encrypted sessions and a multi-tenant workspace, purpose-built for compliance-heavy field teams.',
  },
];

const STEPS = [
  {
    n: '01',
    title: 'Set the scene',
    text: 'Describe your customer and goal — or pick a ready-made scenario to start in one click.',
  },
  {
    n: '02',
    title: 'Get AI-built guidance',
    text: 'Receive a tailored pitch, live-call cues and a roleplay partner, all grounded in your playbooks.',
  },
  {
    n: '03',
    title: 'Close with confidence',
    text: 'Walk into every meeting prepared, then review coaching reports to keep getting better.',
  },
];

const STATS = [
  { value: '13+', label: 'Years delivering' },
  { value: '130+', label: 'Enterprise clients' },
  { value: '7+', label: 'Countries served' },
  { value: '6', label: 'Indian languages' },
];

const VALUE_POINTS = [
  'Answers cited from your own SOPs — zero hallucination',
  'Built for banking & financial-services field teams',
  'From cold scenario to call-ready pitch in under a minute',
  'Coaching that compounds with every roleplay session',
];

/* Reveal-on-scroll: fade elements up as they enter the viewport.
   Honours prefers-reduced-motion by showing everything immediately. */
function useReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll('.pvh-reveal'));
    const reduce = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window)) {
      els.forEach((el) => el.classList.add('pvh-in'));
      return undefined;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add('pvh-in'); io.unobserve(e.target); }
      });
    }, { threshold: 0.12 });
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

export default function Home({ onEnter }) {
  useReveal();

  const scrollTo = (id) => (e) => {
    e.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="pvh-root">
      {/* ── Top navigation ── */}
      <header className="pvh-nav">
        <div className="pvh-nav-inner">
          <a className="pvh-brand" href={PARENT_URL} target="_blank" rel="noopener noreferrer">
            <img src={LOGO} alt="Posiview" />
            <span className="pvh-brand-pill">Edge IQ</span>
          </a>
          <nav className="pvh-nav-links">
            <a href="#features" onClick={scrollTo('features')}>Features</a>
            <a href="#how" onClick={scrollTo('how')}>How it works</a>
            <a href="#why" onClick={scrollTo('why')}>Why Edge IQ</a>
            <a href="#contact" onClick={scrollTo('contact')}>Contact</a>
          </nav>
          <div className="pvh-nav-cta">
            <button className="pvh-btn pvh-btn-ghost" onClick={onEnter}>Sign in</button>
            <button className="pvh-btn pvh-btn-primary" onClick={onEnter}>
              Get started <ArrowRight size={16} />
            </button>
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="pvh-hero">
        <div className="pvh-hero-grid">
          <div className="pvh-hero-copy">
            <div className="pvh-badge">
              <Sparkles size={13} /> Enterprise AI for banking sales teams
            </div>
            <div className="pvh-kicker">Posiview Edge IQ</div>
            <h1 className="pvh-h1">
              Connect better.<br />
              <span className="pvh-grad">Convert faster.</span>
            </h1>
            <p className="pvh-lead">
              An AI sales-intelligence workspace for field teams — instant pitches,
              live call coaching and lifelike roleplay, all grounded in your own
              playbooks.
            </p>
            <div className="pvh-hero-actions">
              <button className="pvh-btn pvh-btn-primary pvh-btn-lg" onClick={onEnter}>
                Get started <ArrowRight size={18} />
              </button>
              <a className="pvh-btn pvh-btn-outline pvh-btn-lg" href="#features" onClick={scrollTo('features')}>
                See capabilities
              </a>
            </div>
            <div className="pvh-hero-trust">
              <Lock size={13} /> Built by Posiview Digital Technologies · 13+ years · 130+ clients
            </div>
          </div>

          {/* Stylised product preview */}
          <div className="pvh-hero-visual pvh-reveal">
            <div className="pvh-mock">
              <div className="pvh-mock-bar">
                <span /><span /><span />
                <div className="pvh-mock-url">posiview-edge-iq</div>
              </div>
              <div className="pvh-mock-body">
                <div className="pvh-mock-head">
                  <div className="pvh-mock-ic"><Sparkles size={16} /></div>
                  <div>
                    <div className="pvh-mock-title">Generated Pitch</div>
                    <div className="pvh-mock-sub">Mr. Sharma · HDFC Business Loan</div>
                  </div>
                  <span className="pvh-mock-badge"><CheckCircle2 size={11} /> READY</span>
                </div>
                <div className="pvh-mock-pill pvh-mock-pill-a">Opening hook · build rapport in 10s</div>
                <div className="pvh-mock-pill pvh-mock-pill-b">Objection handling · "rate is high"</div>
                <div className="pvh-mock-pill pvh-mock-pill-c">Closing strategy · lock the next step</div>
                <div className="pvh-mock-chat">
                  <Headphones size={14} />
                  <span>Live cue: mention zero foreclosure charges after 3 years</span>
                </div>
              </div>
            </div>
            <div className="pvh-mock-glow" />
          </div>
        </div>
      </section>

      {/* ── Stats band ── */}
      <section className="pvh-stats">
        {STATS.map((s) => (
          <div className="pvh-stat pvh-reveal" key={s.label}>
            <div className="pvh-stat-value">{s.value}</div>
            <div className="pvh-stat-label">{s.label}</div>
          </div>
        ))}
      </section>

      {/* ── Features ── */}
      <section className="pvh-section" id="features">
        <div className="pvh-section-head pvh-reveal">
          <div className="pvh-eyebrow">Capabilities</div>
          <h2 className="pvh-h2">Everything a field rep needs, in one workspace</h2>
          <p className="pvh-section-lead">
            From first scenario to signed deal — Edge IQ turns your playbooks into
            real-time selling power.
          </p>
        </div>
        <div className="pvh-feature-grid">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <article className="pvh-card pvh-reveal" key={title}>
              <div className="pvh-card-ic"><Icon size={20} /></div>
              <h3 className="pvh-card-title">{title}</h3>
              <p className="pvh-card-text">{text}</p>
            </article>
          ))}
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="pvh-section pvh-section-alt" id="how">
        <div className="pvh-section-head pvh-reveal">
          <div className="pvh-eyebrow">How it works</div>
          <h2 className="pvh-h2">Three steps to a confident conversation</h2>
        </div>
        <div className="pvh-steps">
          {STEPS.map((s) => (
            <div className="pvh-step pvh-reveal" key={s.n}>
              <div className="pvh-step-n">{s.n}</div>
              <h3 className="pvh-step-title">{s.title}</h3>
              <p className="pvh-step-text">{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Why / value ── */}
      <section className="pvh-section" id="why">
        <div className="pvh-why">
          <div className="pvh-why-copy pvh-reveal">
            <div className="pvh-eyebrow">Why Edge IQ</div>
            <h2 className="pvh-h2">Grounded in your knowledge, built for your teams</h2>
            <p className="pvh-section-lead" style={{ marginLeft: 0 }}>
              Edge IQ doesn’t invent answers — it reads your SOPs, product sheets
              and scripts, then coaches your reps with material you can stand behind.
            </p>
            <ul className="pvh-checks">
              {VALUE_POINTS.map((p) => (
                <li key={p}><span className="pvh-check"><Check size={13} /></span>{p}</li>
              ))}
            </ul>
            <button className="pvh-btn pvh-btn-primary pvh-btn-lg" onClick={onEnter}>
              Enter the workspace <ArrowRight size={18} />
            </button>
          </div>
          <div className="pvh-why-card pvh-reveal">
            <div className="pvh-why-row"><span className="pvh-why-ic"><MessageSquare size={15} /></span> Doc-grounded answers, every time</div>
            <div className="pvh-why-row"><span className="pvh-why-ic"><Languages size={15} /></span> Speak the customer’s language</div>
            <div className="pvh-why-row"><span className="pvh-why-ic"><Users size={15} /></span> Roleplay that scores and coaches</div>
            <div className="pvh-why-row"><span className="pvh-why-ic"><ShieldCheck size={15} /></span> Access-controlled & encrypted</div>
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section className="pvh-cta-band">
        <div className="pvh-cta-inner pvh-reveal">
          <h2 className="pvh-cta-title">Ready to connect better and convert faster?</h2>
          <p className="pvh-cta-sub">Sign in to your secure Posiview Edge IQ workspace.</p>
          <button className="pvh-btn pvh-btn-light pvh-btn-lg" onClick={onEnter}>
            Get started <ArrowRight size={18} />
          </button>
          <div className="pvh-cta-foot"><ShieldCheck size={13} /> Authorized users only · Sessions are encrypted</div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="pvh-footer" id="contact">
        <div className="pvh-footer-grid">
          <div className="pvh-footer-brand">
            <img src={LOGO} alt="Posiview" className="pvh-footer-logo" />
            <p className="pvh-footer-tag">
              Posiview Digital Technologies · Transforming Learning, Creating Experiences.
            </p>
            <div className="pvh-socials">
              <a href={PARENT_URL} target="_blank" rel="noopener noreferrer" aria-label="LinkedIn"><Linkedin size={16} /></a>
              <a href={PARENT_URL} target="_blank" rel="noopener noreferrer" aria-label="Facebook"><Facebook size={16} /></a>
              <a href={PARENT_URL} target="_blank" rel="noopener noreferrer" aria-label="Instagram"><Instagram size={16} /></a>
              <a href={PARENT_URL} target="_blank" rel="noopener noreferrer" aria-label="Twitter"><Twitter size={16} /></a>
              <a href={PARENT_URL} target="_blank" rel="noopener noreferrer" aria-label="YouTube"><Youtube size={16} /></a>
            </div>
          </div>

          <div className="pvh-footer-col">
            <h4>Product</h4>
            <a href="#features" onClick={scrollTo('features')}>Features</a>
            <a href="#how" onClick={scrollTo('how')}>How it works</a>
            <a href="#why" onClick={scrollTo('why')}>Why Edge IQ</a>
            <button className="pvh-linklike" onClick={onEnter}>Sign in</button>
          </div>

          <div className="pvh-footer-col">
            <h4>Company</h4>
            <a href={PARENT_URL} target="_blank" rel="noopener noreferrer">About Us</a>
            <a href={PARENT_URL} target="_blank" rel="noopener noreferrer">Why Us</a>
            <a href={PARENT_URL} target="_blank" rel="noopener noreferrer">Careers</a>
            <a href={PARENT_URL} target="_blank" rel="noopener noreferrer">posiviewtech.com</a>
          </div>

          <div className="pvh-footer-col">
            <h4>Contact</h4>
            <span className="pvh-footer-contact"><MapPin size={14} /> Onyx Business Park, Wakad, Pune 411057</span>
            <a className="pvh-footer-contact" href="tel:+919890817644"><Phone size={14} /> +91 98908 17644</a>
            <a className="pvh-footer-contact" href="mailto:info@posiviewtech.com"><Mail size={14} /> info@posiviewtech.com</a>
            <a className="pvh-footer-contact" href="mailto:sales@posiviewtech.com"><Mail size={14} /> sales@posiviewtech.com</a>
          </div>
        </div>
        <div className="pvh-footer-bottom">
          <span>© {new Date().getFullYear()} Posiview Digital Technologies. All rights reserved.</span>
          <a href={PARENT_URL} target="_blank" rel="noopener noreferrer">posiviewtech.com</a>
        </div>
      </footer>
    </div>
  );
}
