"""
Prompt builders — one subpackage per feature.

Each feature gets its own folder (prompts/<feature>/__init__.py) holding the
builder(s), plus any prompt assets (e.g. prompts/roleplay/avatar_prompt_v5.md).
Each builder returns a fully-formatted prompt string ready to send to Ollama.
Keeping prompts here (separate from app.py methods that orchestrate retrieval +
Ollama calls) means you can iterate on prompt quality without touching the RAG
plumbing. Import paths are unchanged: `from prompts.<feature> import ...`.

When fine-tuning a feature for accuracy, edit ONE folder:
  - Pitch quality      → prompts/pitch/
  - Ask Me chat        → prompts/chat/
  - Live voice replies → prompts/voice/
  - RolePlay avatar    → prompts/roleplay/
"""
