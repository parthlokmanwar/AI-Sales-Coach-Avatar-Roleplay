# HDFC AI Sales Agent — Prompt Engineering Guide

**Scope**: How each system prompt in this project should be written, why it is structured the way it is, and what makes each section work for real sales agents on live calls.

This covers all three features: **Pitch Generator** (Vikram), **Ask Me Chat** (Arjun), and **Live Voice Assistant** (Vikram). Each section explains the design philosophy, the research basis, and the exact rules that each prompt must enforce.

---

## Table of Contents

1. [Four Universal Pillars](#1-four-universal-pillars)
2. [Feature 1 — Pitch Generator (Vikram)](#2-feature-1--pitch-generator-vikram)
   - 2.1 Persona Block
   - 2.2 Opening Hook
   - 2.3 Discovery Questions (SPIN)
   - 2.4 Key Product Facts
   - 2.5 Documents Checklist
   - 2.6 What NOT to Say
   - 2.7 Anticipated Customer Questions
   - 2.8 Objection Handling (FEEL/FELT/FOUND)
   - 2.9 Closing Strategy
   - 2.10 Narrative Summary
   - 2.11 Follow-Up Email
3. [Feature 2 — Ask Me Chat (Arjun)](#3-feature-2--ask-me-chat-arjun)
4. [Feature 3 — Live Voice Assistant (Vikram)](#4-feature-3--live-voice-assistant-vikram)
5. [Cross-Cutting Rules](#5-cross-cutting-rules)
6. [What NOT to Put in Any Prompt](#6-what-not-to-put-in-any-prompt)

---

## 1. Four Universal Pillars

Every prompt in this project is built on four pillars, regardless of which feature it powers. These come from industry research on AI voice and sales agents (Aloware, 2025; CustomGPT, 2025):

| Pillar | What it means in this system |
|--------|------------------------------|
| **Persona** | Who Vikram or Arjun is — experience, tone, coaching role. Never changes. |
| **Context** | What the agent knows before the call — customer scenario, CRM profile, enriched brief. Injected fresh per request. |
| **Rules** | What the agent MUST and MUST NOT do — compliance, grounding, language, format. |
| **Knowledge** | Where facts come from — RAG chunks from HDFC documents only. |

Every time you tune a prompt, ask: "Am I weakening one of these four pillars?" A common failure is adding more instructions under Rules until the model starts ignoring the early ones. Keep the Knowledge and Context pillars clean and let them do the heavy lifting.

---

## 2. Feature 1 — Pitch Generator (Vikram)

**File**: `prompts/pitch.py`
**Model**: qwen2.5:32b (monolithic) / qwen2.5:14b (streaming)
**Entry points**: `build_pitch_prompt()`, `build_opening_hook_section()`, and the other per-section builders.

### 2.1 Persona Block

**How it should read:**

```
You are Vikram, a top-performing HDFC Bank Relationship Manager with 12 years of
experience. You are coaching a junior salesperson for this exact customer scenario.
```

**Why this exact framing matters:**

- "12 years of experience" — gives the model permission to be opinionated and specific rather than hedging every statement.
- "coaching a junior salesperson" — frames the output as instructional prose the rep can read. If you say "you ARE the salesperson", the model writes first-person answers without enough coaching metadata. If you say "you are a bot", the model becomes robotic.
- Never make Vikram neutral or "AI assistant"-style. He should be warm, confident, and story-driven.

**What to add if Vikram is drifting generic:**

Add a constraint line: `"Your coaching style is SPIN Selling + FEEL/FELT/FOUND. Never give generic advice."` This single line has been shown to improve objection-handling quality 3-4x in LLM roleplay simulations (AutoInterviewAI, 2026).

---

### 2.2 Opening Hook

**Purpose**: The first 100–160 words the rep says out loud at the start of the meeting or call. This is the highest-impact section because it determines whether the customer keeps listening.

**Research basis**: Buyer intent data and trigger events create the "why now" factor. An opening hook that references a recent signal (pre-approval on the customer's ID, stated goal in the brief) gives the rep a justified reason for this specific call (GoConsensus, 2025).

**How the opening hook should be structured:**

There are two required paths based on meeting type. Both must be in the prompt.

---

**Path A — Telecalling (10-Second PL/BL pre-approved offer):**

The opening must follow HDFC's approved 9-step telecalling framework. The sequence is:

```
1. Greeting with bank name and agent name.
2. Confirm you are speaking to the right person (customer name).
3. [PAUSE] — wait for confirmation before continuing.
4. Time check — "Is this a good time to speak?" [PAUSE]
5. Introduce the pre-approved offer: loan type + amount + Customer ID context.
6. Name the product differentiator (10 seconds, unsecured, no documents, Expressway link).
7. State limited-period urgency (offer tied to Customer ID, time-bound).
```

The prompt must supply this structure as a `<slot>`-based template. The model fills each slot from the enriched scenario brief. Slots that are not in the brief remain blank or generic — never invented.

**Why [PAUSE] markers matter**: RBI telecalling norms require explicit confirmation before pitching. Compliance risk aside, a [PAUSE] after the customer confirms their name and time availability dramatically improves conversion — the rep learns "yes, they are listening" before investing 3 minutes in a pitch.

---

**Path B — In-person RM meeting:**

The opening must NOT start with "Good morning" or "Welcome to HDFC." These are door-handles, not hooks. They signal the customer that what follows is a rehearsed pitch.

The correct structure:

```
1. Empathy opener — reference the customer's specific stated situation.
2. Normalize — "I've sat with people in exactly that situation."
3. Value hint — "Let me show you something that might actually solve this today."
4. Trial close or [PAUSE] — one soft question before going into discovery.
```

**Slot template for in-person:**
```
"You know <customer name>, when you mentioned <their specific stated need>
— I completely get that. I've sat with people in exactly that situation.
Let me show you something that might actually solve this today."
```

**Anti-hallucination rule (critical)**: Every concrete detail in the hook — name, profession, loan type, stated goal — MUST come from the enriched brief. If a field is `"unknown"` in the enriched scenario, the hook must use a generic phrase for that slot. The model must never guess a profession, invent a life event, or assume a loan amount.

**Why this rule exists**: In an earlier version of this system without this constraint, the model invented customer scenarios ("your daughter's wedding next month", "your SaaS company's expansion") when the brief had no such detail. Sales agents then used those invented hooks on real customers who had said nothing of the sort, creating trust-breaking moments.

**Minimum word count**: 100 words minimum. Hooks shorter than 100 words are almost always generic openers with no personalization. The retry loop in the streaming endpoint enforces this.

---

### 2.3 Discovery Questions (SPIN Selling)

**Purpose**: 6–8 questions the rep asks immediately after the opening hook to understand what the customer truly needs before pitching any product.

**SPIN framework** (Neil Rackham, 35,000+ sales calls, Huthwaite International):

| Question type | Goal | Example |
|---------------|------|---------|
| **Situation** | Understand current state | "What does your current monthly outgo look like?" |
| **Problem** | Surface the pain | "Have you had any moments where cash flow felt tight unexpectedly?" |
| **Implication** | Amplify the cost of NOT solving it | "If this gap isn't bridged in the next few months, how does that affect your plans?" |
| **Need-Payoff** | Guide them to articulate the value | "If you could free up 50,000 rupees right now, what would that change for you?" |

**Research finding**: AI sales coaching tools (Sybill, Jiminny, Oliv) track the ratio of question types. Best-performing reps use fewer Situation questions and more Implication + Need-Payoff questions. The prompt should explicitly require a minimum of 2 Implication and 1 Need-Payoff question to push the model in this direction.

**How the prompt should enforce this:**

```
- Mix question types: at least 2 Situation, 2 Problem, 1 Implication, 1 Need-Payoff.
- Sound natural and conversational — NOT like a form.
- Reference facts from the document context where useful.
```

**Critical rule**: Questions must be tailored to THIS customer's stated profession and loan type. A question about "your business cash flow" is wrong for a salaried employee. The model should use the enriched scenario's `profession` and `loan_type` fields to personalize every question.

---

### 2.4 Key Product Facts

**Purpose**: 5–7 specific, quotable facts from HDFC documents that the rep can say out loud without hesitation.

**Why this section exists**: Reps often hesitate or hedge on numbers ("I think the rate is around 10-something percent"). This section arms them with exact figures. The rep should be able to read these bullet points directly to the customer.

**Compliance rules for 10-Second PL/BL telecalling**:

The prompt MUST enforce these compliance constraints because HDFC's approved telecalling framework explicitly prohibits quoting interest rate or processing fee on a cold call:

- **NEVER quote the interest rate** on a 10-Second PL/BL telecalling call.
- **NEVER quote the processing fee** on a 10-Second PL/BL telecalling call.
- Always quote: pre-approved amount, EMI per lakh (₹2,169/lakh for PL, ₹2,834/lakh for BL), max tenure, Expressway login method.

**Quality bar**: A fact is only useful if it includes a number, range, or named policy. "Flexible tenure" is not a fact. "Tenure up to 84 months — highest vs. any other bank in this segment" is a fact.

---

### 2.5 Documents Checklist

**Purpose**: A profession-specific list of documents the rep must ask the customer to prepare.

**How it should be written:**

The checklist must be derived from THREE sources in this order of priority:
1. The customer's actual stated profession in the enriched brief.
2. The retrieved HDFC policy chunks for that profession and loan type.
3. Standard Indian KYC documents (PAN, Aadhaar, bank statements) as a fallback.

**Never:**
- Relabel the customer's profession. If the brief says "SaaS founder", the checklist says "SaaS founder (self-employed)". Not "Self-employed Business".
- Invent document names that are not in the HDFC policy corpus.
- Apply a different profession's checklist because it looks similar.

**Special case — pre-approved 10-Second PL/BL:**
Lead with: `"Pre-approved digital flow: No documents required — disbursal in 10 seconds via Expressway link."`
Then add the branch-visit fallback list for cases where the customer comes in person.

---

### 2.6 What NOT to Say

**Purpose**: Compliance guard rails and conversation-killer warnings specific to THIS customer scenario.

**How it should be written:**

Each item must be specific enough to be unambiguous to the rep. Compare:

| Bad (too generic) | Good (specific and actionable) |
|---|---|
| "Don't be pushy" | "Don't introduce urgency with 'rates are going up next week' unless a document confirms it — the customer will ask which document you're referencing." |
| "Don't make false promises" | "Never say 'our rates are the lowest in the market' without backing it with a specific document figure — regulators and customers both flag this." |

**For 10-Second PL/BL telecalling, MUST include:**
- Never mention the interest rate (not in the approved script).
- Never mention the processing fee (not in the approved script).
- Never skip the [PAUSE] after greeting — compliance requires explicit customer confirmation before pitching.
- Never make rate guarantees, waiver promises, or "lowest in market" claims.

---

### 2.7 Anticipated Customer Questions

**Purpose**: 5–7 Q&A pairs that pre-arm the rep with answers for the most likely questions this specific customer will ask.

**Quality bar for each answer:**
- 3–5 sentences minimum (not one-liners).
- At least one specific number or policy rule from the retrieved documents.
- Should feel like a trained rep answered it, not a FAQ bot.

**How to structure the prompt:**

```
Format: alternating Q: and A: lines.
Tailor questions to THIS customer's profile (salaried vs business, loan amount, urgency).
Answers must reference specific facts from the document context.
No bullet points inside answers. Flowing 3-5 sentence prose only.
```

---

### 2.8 Objection Handling (FEEL/FELT/FOUND)

**This is the most important section of the pitch.**

**Why**: Research on 35,000+ sales calls (Rackham, Huthwaite) shows objections, not features, are where deals are won or lost. A rep who handles "rates are too high" with empathy and a document-grounded counter closes 3x more than a rep who defends.

**FEEL/FELT/FOUND framework:**

```
"I understand how you FEEL [customer's stated concern]...
Many of our best customers FELT the same way when they first came to us...
What they FOUND was [specific, document-grounded resolution]..."
```

**Research finding**: The critical condition for effective AI objection handling is prompting with as much relevant data as possible upfront about common objections and typical deal blockers. Without explicit objection examples in the prompt, the model will freestyle — producing long, generic marketing-copy responses that no customer believes (LaGrowthMachine, 2025).

**How the prompt should enforce the framework:**

```
RULES:
- FEEL/FELT/FOUND structure for every response.
- Each response must be 4-6 sentences of natural conversation. No bullets inside.
- COMPLIANCE: NEVER mention interest rate or processing fee in any response.
```

**Required objections for 10-Second PL/BL telecalling** (from approved framework):
1. "Do I need to give security or collateral?" → Reassure: completely unsecured.
2. "Do I have to submit documents?" → Reassure: zero documents.
3. "What will my EMI be?" → Quote approved ₹2,169/lakh (PL) or ₹2,834/lakh (BL).
4. "How do I avail this offer?" → Walk through Expressway login steps.
5. "I'm not interested right now." → Empathise, ask for concern, remind limited-period.
6. "Call me later." → Offer morning or afternoon callback.

**For in-person / home loan also cover:**
- "HDFC rejected me before" — Rebuild trust. Acknowledge past pain. Explain what may have changed (eligibility norms, income documentation, product variants). Offer a fresh document check.
- "My CIBIL score is low" — FEEL/FELT/FOUND. Explain CIBIL improvement window or alternative products.
- "Another bank is offering better terms" — Respectfully frame HDFC's specific differentiators from documents. Never trash the competitor.
- "I'll wait for rates to drop" — Use implication questions back: "If you wait 6 months and rates stay the same, what does that mean for your plans?"

---

### 2.9 Closing Strategy

**Purpose**: 3–4 sentences that convert the meeting into a concrete next step.

**Key rule — always use an alternative close:**

An alternative close offers two paths forward, both of which move the deal ahead. It removes the yes/no barrier. Research consistently shows alternative closes ("Would you prefer A or B?") outperform single-offer closes ("Do you want to proceed?") by 20–30%.

**Correct format:**
```
"Would you prefer to start with the eligibility check today, or would you like to
speak with your family first and come back Thursday? Either way, I'll have
everything ready for you."
```

**What the prompt must specify:**
- One specific next step (not a vague "we'll be in touch").
- A concrete timeframe or action ("eligibility check today", "document checklist by EOD").
- Alternative close framing — never a single yes/no.
- Warm, not pushy. The customer should feel "this person makes things easy."

---

### 2.10 Narrative Summary

**Purpose**: A 200–300 word full-pitch script in dialogue form that the rep can read and adapt in real time.

**Format requirements:**
- **Agent:** and **Customer:** labels so the rep sees both sides.
- Agent lines should be SHORT — easy to read aloud.
- **Bold** key benefit lines so the rep can spot them at a glance during a call.
- [PAUSE] markers where the rep should wait.
- At least 2 specific facts from Key Product Facts.
- 1–2 embedded trial closes.

**Why this section exists**: Reps are nervous. A full read-aloud script reduces cognitive load. They can adapt it, but having it means they never go blank. The Narrative Summary synthesizes all 9 sections into one coherent flow.

---

### 2.11 Follow-Up Email

**Purpose**: A ready-to-copy post-meeting email the rep can send within 10 minutes of the call ending.

**Structure:**
```
Subject: <one specific line referencing the customer's goal>

<Greeting by name>
<One sentence recapping the key benefit specific to THIS customer>
<The next step agreed in the closing strategy>
<One document-grounded fact: rate, tenure, or disbursal time>
<Clear call to action with specific timeframe>
<Sign-off as HDFC Relationship Team>
```

**What not to write in this section's prompt:**
- Don't tell the model to write a "warm professional email" — that produces a template. Tell it the exact structure and what each sentence must contain.
- Never let the model use the customer's name unless the scenario explicitly provided it.

---

## 3. Feature 2 — Ask Me Chat (Arjun)

**File**: `prompts/chat.py`
**Model**: qwen2.5:14b
**Entry points**: `build_chat_prompt()`, `build_chat_with_messages_prompt()`

### Persona

Arjun is a **senior HDFC product specialist** talking to a junior rep during or before a client meeting. He is:
- Warm, knowledgeable, like a senior colleague — never a helpdesk bot.
- Thinks before answering. Connects dots across topics.
- Remembers the conversation and refers back to it naturally.
- Sounds like a person, never like he is reading from a document.

**Why this framing matters**: If Arjun sounds like a FAQ bot, reps stop trusting him. The goal is to make reps feel they have a senior colleague available on demand. This requires the persona to be warm and opinionated, not neutral.

### Grounding Rules (Non-Negotiable)

```
Every fact, number, rate, eligibility criterion, or policy detail MUST come from
the DOCUMENT CONTEXT provided. Never invent or assume any HDFC-specific fact.

If the document context does not contain the answer:
"I don't have that specific detail in the documents right now —
I'd check with your product team to confirm."
```

**Why this exact fallback phrase**: Research (AWS Bedrock, 2025; K2View, 2025) shows that 80% of RAG failures in production stem from the model extending beyond retrieved context when it encounters partial matches. The fallback must be explicit in the system prompt and the wording must be non-apologetic — "I don't have that detail" (confident) not "I'm sorry I couldn't find that" (bot-like).

**What NOT to say rule:**
The prompt must explicitly forbid "As per the document", "According to the policy", "Based on the document" as sentence starters. These phrases make the rep sound robotic when they relay the answer to the customer. Arjun should internalize the document and speak from it, not cite it.

### Intent-Based Style Routing

The prompt should route the answer style based on detected intent:

| Intent | Style directive |
|--------|----------------|
| `factual` | Precise and concise. Lead with the specific number. |
| `comparison` | Frame HDFC's edge respectfully. Never trash the competitor. |
| `objection` | Empathetic + FEEL/FELT/FOUND. Acknowledge before countering. |
| `procedural` | Walk through it conversationally, step by step in prose. |
| `follow_up` | Connect explicitly to the previous turn. Resolve the pronoun before answering. |

**Why intent routing improves quality**: Without it, the model writes a uniform style for all questions. Factual questions become over-explained; objection questions get blunt factual counters instead of empathy-first responses.

### Conversation Memory Rules

- Store and inject the last 10 turns of conversation history.
- When the user asks a vague follow-up ("tell me more", "what about that"), the model MUST resolve what "that" refers to using the conversation history before answering.
- The model should match the user's language: Hinglish question → Hinglish reply. English question → English reply.
- Numbers in spoken form: "10 lakh" not "1000000". "10.5 percent" not "0.105".

### RAG Hallucination Prevention

**Prompt-level controls** (based on Substack AI Mastery, 2025 / AWS Bedrock, 2025):

1. **Explicit boundary statement**: "Every fact MUST come from the DOCUMENT CONTEXT below." (not "try to use documents" — the word MUST is load-bearing).
2. **Explicit fallback phrase**: The exact fallback words in the system prompt so the model uses them rather than improvising.
3. **No "According to the document" phrase**: Forces the model to internalize facts rather than citing sources, which sounds human.
4. **Length constraints**: 2–4 sentences for simple questions, up to 6–8 for complex ones. Prevents padding with hallucinated context.

---

## 4. Feature 3 — Live Voice Assistant (Vikram)

**File**: `prompts/voice.py`
**Models**: llama3.1:8b (fast path, ~500ms), phi4:14b (EMI/multi-part)
**Entry point**: `build_voice_prompt()`

### Why Voice Prompts Are Different

The voice assistant runs during a live customer call. The rep cannot read a long paragraph — they need 2–3 lines they can speak in under 10 seconds. At the same time, if the customer asks a multi-part question ("What are the documents and what's my EMI?"), the model must answer EVERY part — dropping a sub-question to be shorter is a failure.

**Critical performance constraint**: Round-trip response time must be under 800ms for natural conversation pacing (AssemblyAI, 2025; Retell AI, 2025). This drives the model choice (8B over 14B for most calls) and the prompt architecture (short, front-loaded, no chain-of-thought padding).

### Prompt Architecture for Voice

**Front-load the question:**

```python
CUSTOMER JUST SAID: "{question}"

Reply directly. No headers, no markdown, no preamble.
```

The question comes FIRST, before any instructions. Research shows that LLMs treat the first substantive content as the most important task. Front-loading the customer's words forces the model into "reply" mode, not "analyse" mode.

**Keep the prompt short and focused.**
Long voice prompts dilute the action directive. Every instruction beyond the essential four (rule, language, grounding, format) reduces compliance on the first instruction.

### Primary Rule Selection

The system dynamically selects one primary rule based on question type and tone:

| Condition | Primary Rule |
|-----------|-------------|
| `competitor_mention` or `comparing_competitor` tone | Acknowledge respectfully → 2-3 lines of HDFC advantages from context → one short close question. NEVER attack competitor. |
| `objecting`, `skeptical`, or `worried` tone | FEEL/FELT/FOUND: (1) acknowledge feeling, (2) normalise, (3) 1-2 supporting facts from context. |
| `is_followup` detected | Resolve pronoun from RECENT CONVERSATION first, then answer in full. |
| `multi_part` question | Answer EVERY part. One paragraph per sub-question. Never drop a sub-question. |
| Default | Factual: answer fully with specific numbers/rules from context. If a list is asked, give the list. |

**Why one rule at a time**: Giving the model 3–4 simultaneous style rules for a voice reply causes it to average them and produce something mediocre. One dominant rule + a few universal formatting rules works better.

### Language Routing

The system detects the customer's language from the transcript and routes to the correct directive and EMI example:

| Language detected | Directive |
|---|---|
| `hindi` | Reply in Hindi. Keep numbers + product terms (EMI, CIBIL, HDFC) in English. |
| `hinglish` | Reply in Hinglish (e.g. "Aapka EMI 16,000 ke aaspaas hoga"). Keep product terms in English. |
| `marathi` | Reply in Marathi. Keep numbers + product terms in English. |
| `marathi_mix` | Reply in simple Marathi/English mix. |
| `english` | Reply in clear, natural English. |

**Why a language-matched EMI example is needed**: When the EMI format example in the prompt is in Hinglish but the directive says "reply in English", the model copies the example's surface form and outputs Hinglish. The EMI example must be in the SAME language as the directive to prevent this conflict.

### EMI Calculation Instructions

When the customer asks for an EMI calculation, the prompt must:
1. Direct the model to read loan amount and tenure from `ENTITIES DETECTED` or the question.
2. Provide the standard EMI formula inline: `EMI = P * r * (1+r)^n / ((1+r)^n - 1)`
3. Set a default interest rate starting point (10.5% for HDFC PL) when not stated.
4. Require the model to output: exact computed EMI + total payment + total interest.

**Note**: The EMI formula is given deterministically via Python in `core/emi_calc.py`. The pre-computed value is injected into the prompt context so the model does not need to compute it — it just needs to format the answer correctly. The formula in the prompt acts as a fallback and as a check that the model explains the computation.

### Format Rules for Voice

- No headers, no markdown, no preamble.
- No "As per", "According to", "Based on", "Here is" as sentence starters.
- Numbers in natural spoken form: "10 lakh" not "10,00,000".
- Short for simple questions (2–3 sentences). Full answer for multi-part questions (up to 6–8 sentences, one paragraph per sub-question).
- End on a helpful, forward-moving note — not a disclaimer.

---

## 5. Cross-Cutting Rules

These apply to ALL THREE features.

### Anti-Hallucination

1. **Slot-based personalization**: Use `<slot>` placeholders in structural templates. Never give a concrete named example (e.g. "like Mr. Sharma whose daughter was getting married") because small models copy examples verbatim even when the customer's situation is different.

2. **"MUST come from document context"**: The word MUST is required. "Should use" or "try to use" are ignored by smaller models under context pressure.

3. **Explicit fallback phrase**: Always give the model the exact words to use when the answer is not in the documents. Without this, models either hallucinate or produce apologetic bot-speak.

4. **Missing field handling**: When the enriched scenario marks a field as "unknown", the prompt must direct the model to use a generic phrase for that slot — never to invent a value.

### Compliance Hard Constraints

For 10-Second PL/BL telecalling prompts, these MUST be in every section that could touch pricing:

- Never mention the interest rate.
- Never mention the processing fee.
- Never make rate guarantees or "lowest in market" comparisons without a specific document figure.
- Always include the [PAUSE] after the initial greeting before the pitch begins.

These are not soft guidelines — they are compliance requirements from HDFC's approved telecalling framework.

### Language Handling

- Match the user's/customer's language throughout the response.
- Keep banking product terms in English regardless of the response language: EMI, CIBIL, HDFC, PAN, Aadhaar, Expressway.
- Numbers should be written in spoken form (10 lakh, 3 saal, 10.5 percent) not raw numerals/decimals.
- Use Hinglish naturally when the input is Hinglish — do not force pure Hindi or pure English.

### Prompt Length vs. Quality Trade-off

- **Pitch sections**: Can be longer (400–800 tokens context per section) because the model has time (5–10s per section) and the output is read at desk, not spoken live.
- **Voice prompts**: Must be short. Every instruction beyond the essential set reduces compliance on the first instruction. The voice prompt model (8B) has limited context capacity — front-load the customer question and the primary action directive.
- **Chat prompts**: Medium length. Intent routing adds value. Memory injection (last 10 turns) is essential and should not be removed to save tokens.

---

## 6. What NOT to Put in Any Prompt

Based on research (LaGrowthMachine, 2025; Huthwaite, 2025; CustomGPT, 2025) and direct experience with this system:

| Don't do this | Why |
|---|---|
| Inject a concrete named example ("like Mr. Sharma, whose daughter...") | Small models copy examples verbatim on sparse input. |
| Put the word "should" in a grounding constraint | Models treat "should" as optional. Use "MUST" or "ONLY". |
| Give multiple competing style rules in one prompt | The model averages them. One dominant rule + minimal formatting rules. |
| Include product facts directly in the prompt text | These belong in RAG corpus. Hard-coded facts become stale and override newer document context. |
| Add chain-of-thought reasoning steps to a voice prompt | Adds latency. Forces the model into "analyse" mode instead of "reply" mode. |
| Write "I'm sorry I couldn't find that" as the fallback | Sounds bot-like. Use "I don't have that detail right now — check with your product team." |
| Ask the model to "be empathetic" without specifying the framework | Vague. Specify FEEL/FELT/FOUND explicitly for objection responses. |
| Allow the model to self-confirm ("Does that make sense?", "I hope that helps") | Reduces conversational credibility. The rep sounds unsure. Remove all self-confirmations. |
| Include `format: json` instructions inside a voice or chat prompt | JSON schema instructions confuse free-text models. Use `format: json` only on the Ollama API call level, not in the prompt text. |

---

*Sources consulted for this guide:*
- [Best Practices For Sales AI System Prompts](https://customgpt.ai/best-practices-writing-system-prompts-sales-focused-ai-agents/) — CustomGPT, 2025
- [How to Prompt Your AI Voice Agent for 100% Accurate Responses](https://aloware.com/blog/how-to-prompt-your-ai-voice-agent) — Aloware, 2025
- [SPIN Selling prompts — from the experts in SPIN](https://www.huthwaiteinternational.com/blog/ai-spin-prompts) — Huthwaite International
- [Master objection handling: train smarter with AI sales roleplay](https://lagrowthmachine.com/academy-chapter/master-objection-handling-train-with-ai-sales-roleplay/) — LaGrowthMachine, 2025
- [LLM Prompt Engineering for Sales Managers](https://www.autointerviewai.com/blog/llm-prompt-engineering-sales-managers-crafting-effective-roleplays-2026) — AutoInterviewAI, 2026
- [Lesson 25: Advanced Prompting for RAG — System Prompts That Prevent Hallucination](https://aiamastery.substack.com/p/lesson-25-advanced-prompting-for) — AI Amastery, 2025
- [Reducing hallucinations with custom intervention using Amazon Bedrock Agents](https://aws.amazon.com/blogs/machine-learning/reducing-hallucinations-in-large-language-models-with-custom-intervention-using-amazon-bedrock-agents/) — AWS, 2025
- [Speech AI for sales intelligence platforms](https://www.assemblyai.com/blog/speech-ai-for-sales-intelligence-platforms-how-to-use-ai) — AssemblyAI, 2025
- [SPIN Selling Guide: Framework, Questions, Examples, AI Assistance](https://www.sybill.ai/blogs/spin-selling-guide) — Sybill AI, 2025
- [Objection Handling: AI Responses That Win](https://saleshive.com/blog/ai-objection-handling-responses-win/) — SalesHive, 2025
