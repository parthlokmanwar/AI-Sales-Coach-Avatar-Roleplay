// ── Product tour — step registry ─────────────────────────────────────────────
// THE single place the tour is defined. Adding a feature to the tour =
// (1) put a `data-tour="…"` attribute on the element, (2) add one object here.
// See ./README.md for the full contract.
//
// Step shape:
//   id      unique slug (also used in tests)
//   target  CSS selector to spotlight — omit for a centered welcome/finish card
//   page    activePage id to navigate to BEFORE highlighting (omit if the
//           target is always visible, e.g. sidebar / top bar)
//   title   short heading shown in the popover
//   body    1–2 plain sentences: what it does + why an agent cares
//   side    preferred popover side ('top'|'right'|'bottom'|'left', optional)
//
// A step whose target never appears (feature flagged off, slow render…) is
// skipped silently after a short wait — a bad selector can't strand the tour.

export const TOUR_STEPS = [
  {
    id: 'welcome',
    title: 'Welcome to Posiview Edge 👋',
    body: 'Your AI sales copilot. This one-minute tour shows you where everything lives. Use Next / Back to move around, or press Esc to leave — you can replay it anytime from the ? button up top.',
  },
  {
    id: 'nav-home',
    target: '[data-tour="nav-home"]',
    title: 'Home',
    body: 'Your dashboard — quick stats on your pitches and practice sessions, plus shortcuts into every tool.',
    side: 'right',
  },
  {
    id: 'nav-roleplay',
    target: '[data-tour="nav-roleplay"]',
    title: 'RolePlay Simulation',
    body: 'Practice real sales calls with a lifelike AI customer avatar — new-customer pitches or existing-customer service calls — and get a coaching report when you hang up.',
    side: 'right',
  },
  {
    id: 'nav-generator',
    target: '[data-tour="nav-generator"]',
    title: 'Pitch Generator',
    body: 'Describe the customer and get a ready-to-speak pitch script in seconds — opening, objection handling, closing — grounded in HDFC product documents.',
    side: 'right',
  },
  {
    id: 'nav-askme',
    target: '[data-tour="nav-askme"]',
    title: 'AI Mentor',
    body: 'Ask anything about HDFC loan products. Answers come straight from the official product documents, so you can quote them with confidence.',
    side: 'right',
  },
  {
    id: 'chat-language',
    page: 'askme',
    target: '[data-tour="chat-language"]',
    title: 'Language toggle',
    body: 'Switch the bot’s replies between English, Hindi, Marathi, Tamil, Bengali and Telugu — mid-conversation, whenever the customer’s language changes.',
    side: 'bottom',
  },
  {
    id: 'chat-learnings',
    page: 'askme',
    target: '[data-tour="chat-learnings"]',
    title: 'Key Learnings',
    body: 'One click distils the whole conversation into takeaways you can revise just before the next call.',
    side: 'bottom',
  },
  {
    id: 'chat-new',
    page: 'askme',
    target: '[data-tour="chat-new"]',
    title: 'New Chat',
    body: 'Start a fresh conversation for a new customer or topic. Earlier sessions stay saved in the chat sidebar.',
    side: 'bottom',
  },
  {
    id: 'nav-loansuite',
    target: '[data-tour="nav-loansuite"]',
    title: 'Loan Suite',
    body: 'EMI, eligibility and other loan calculators — run the numbers with the customer while you’re still on the call.',
    side: 'right',
  },
  {
    id: 'nav-history',
    target: '[data-tour="nav-history"]',
    title: 'Pitch History',
    body: 'Every pitch you’ve generated, saved automatically. Reopen one before a follow-up call, or reuse it for a similar customer.',
    side: 'right',
  },
  {
    id: 'fab',
    page: 'home',
    target: '[data-tour="fab"]',
    title: '“Ask me !” assistant',
    body: 'This bubble follows you on every page. Drag it anywhere; click it to ask a quick question without leaving what you’re doing.',
  },
  {
    id: 'profile',
    target: '[data-tour="profile"]',
    title: 'Your account',
    body: 'Update your name or password here, and sign out when you’re done for the day.',
    side: 'bottom',
  },
  {
    id: 'finish',
    target: '[data-tour="help"]',
    title: 'That’s the tour! 🎉',
    body: 'Replay it anytime from this ? button. Good selling!',
    side: 'bottom',
  },
];
