// Avatar (Tavus replica) mapping by profession → "Gender|AgeGroup".
// Used to auto-assign the avatar from the custom form's profession/gender/age,
// so the manager never has to pick an avatar or a model.
// Age groups match formOptions.js: "25-35" | "35-50" | "50+".
//
// All replica IDs verified against the current Tavus account (June 2026).

export const AVATAR_MAP = {
  "Doctor": {
    "Male|25-35":   "r158ac53345d", // Liam 2
    "Male|35-50":   "r18e9aebdc33", // Raj - Doctor (Indian male)
    "Male|50+":     "r18e9aebdc33", // Raj - Doctor
    "Female|25-35": "r4dcf31b60e1", // Anna - Office
    "Female|35-50": "rd3ba0f30551", // Olivia - Doctor
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Teacher": {
    "Male|25-35":   "rfe12d8b9597", // Nathan - Bookshelf
    "Male|35-50":   "r92debe21318", // James
    "Male|50+":     "r873e4707689", // James - Home Office
    "Female|25-35": "r6ae5b6efc9d", // Anna
    "Female|35-50": "rec4a4153a78", // Beth
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Businessman": {
    "Male|25-35":   "r31e11adf1d3", // Kai
    "Male|35-50":   "r5fb46c843a8", // Jakey
    "Male|50+":     "r92debe21318", // James
    "Female|25-35": "r1af76e94d00", // Rose
    "Female|35-50": "r38e4c3bc562", // Samantha - Office V2
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Government Employee": {
    "Male|25-35":   "r90a0339d496", // Liam
    "Male|35-50":   "rca8a38779a8", // Carter
    "Male|50+":     "r873e4707689", // James - Home Office
    "Female|25-35": "r4317e64d25a", // Gloria
    "Female|35-50": "rbe2c395e725", // Gloria - Conversational
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Housewife": {
    "Female|25-35": "r84698ecbd7a", // Shailaja (custom)
    "Female|35-50": "r84698ecbd7a", // Shailaja (custom)
    "Female|50+":   "r68fe8906e53", // Mary - Office
    // Housewife is female-only; male fallback → Businessman map
    "Male|25-35":   "r31e11adf1d3",
    "Male|35-50":   "r5fb46c843a8",
    "Male|50+":     "r92debe21318",
  },
  "Self-Employed": {
    "Male|25-35":   "r31e11adf1d3", // Kai
    "Male|35-50":   "rdba02690e60", // Vikas (custom)
    "Male|50+":     "r92debe21318", // James
    "Female|25-35": "r1af76e94d00", // Rose
    "Female|35-50": "r38e4c3bc562", // Samantha - Office V2
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Retired": {
    "Male|50+":     "r873e4707689", // James - Home Office
    "Female|50+":   "r68fe8906e53", // Mary - Office
  },
  "Student": {
    "Male|25-35":   "r62baeccd777", // Danny
    "Female|25-35": "r9d30b0e55ac", // Luna
  },
};

// The avatar's brain (LLM) — gpt-4o-mini via OpenRouter, used for BOTH English
// and Hindi sessions (single source of truth: custom form + presets both read
// this). It follows the long in-character roleplay rules (role lock, difficulty,
// no-repeat, no-hallucination) far more reliably than a small model across a
// multi-turn call, is fluent in Hindi/Devanagari, and is still fast (~1-1.5s/turn).
export const ROLEPLAY_MODEL = "openai/gpt-4o-mini";

// Professions with no explicit map fall back to this one (generic professionals).
const FALLBACK_PROFESSION = "Businessman";

// Professions whose age is fixed.
export const LOCKED_AGE = {
  "Retired": "50+",
  "Student": "25-35",
};

export function resolveAvatar(profession, gender, age) {
  const map = AVATAR_MAP[profession] || AVATAR_MAP[FALLBACK_PROFESSION];
  return (
    map[`${gender}|${age}`] ||
    map[`Male|${age}`] ||
    map[`Female|${age}`] ||
    Object.values(map)[0] ||
    ""
  );
}
