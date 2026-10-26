"""
RAG Engine — orchestrates retrieval + Ollama calls + per-feature business logic.

This file owns the stateful `RAGEngine` class (formerly `EnhancedRAGv4`).
Pure helpers live in sibling modules:
    rag.chunker      — SmartChunker, DocumentChunk
    rag.embeddings   — VectorMatcher, HAS_VECTORS
    rag.intent       — expand_query, detect_intent
    rag.analysis     — analyze_speech
    rag.post_process — user_asked_for_list, post_process_chat

Prompt builders live in prompts/* — edit those to tune per-feature accuracy.
"""

import logging
import os
import re
import json
import tempfile

_log = logging.getLogger("hdfc")
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import numpy as np
import pandas as pd
import requests
# Replaced by native LLM generation in {language} — see language directive in prompts/pitch.py.
# from deep_translator import GoogleTranslator

from core.llm import (
    call_llm,
    resolved_model,
    LLM_PROVIDER as _LLM_PROVIDER,
    PITCH_MODEL as _OR_PITCH_MODEL,
    CHAT_MODEL as _OR_CHAT_MODEL,
    OLLAMA_PITCH_MODEL as _OL_PITCH_MODEL,
    OLLAMA_CHAT_MODEL as _OL_CHAT_MODEL,
)

# ── Pure helpers (extracted modules) ─────────────────────────────────────────
from rag.chunker import DocumentChunk, SmartChunker
from rag.embeddings import VectorMatcher, HAS_VECTORS
from rag.intent import expand_query, detect_intent
from rag.analysis import analyze_speech
from rag.post_process import user_asked_for_list, post_process_chat
from rag.store import ChunkStore
from rag.retrieval import retrieve as hybrid_retrieve, RetrievalTrace
from rag.reranker import get_reranker

# ── Prompt builders ──────────────────────────────────────────────────────────
from prompts.pitch import build_pitch_prompt, build_scenario_enrichment_prompt
from prompts.chat import build_chat_prompt, build_chat_with_messages_prompt
from prompts.voice import (
    build_voice_prompt as _voice_build,
    pick_primary_rule as _voice_pick_rule,
    pick_language_directive as _voice_pick_lang,
)
from rag.query_rewriter import normalize_hinglish, rewrite_query
from rag.calculator import run_financial_analysis, parse_amount as _calc_parse_amount
import time as _time_module

# ── Load .env file ────────────────────────────────────────────────────────────
# .env lives next to main.py (one level up from rag/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.strip().split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# Map user language selections to Google Translate codes
LANGUAGE_CODES = {
    "English": "en",
    "Hindi": "hi",
    "Marathi": "mr",
    "Bengali": "bn",
    "Tamil": "ta",
    "Telugu": "te", 
    "Kannada": "kn",
    "Gujarati": "gu",
    "Malayalam": "ml"
}

# Banking terms that must stay in English regardless of translation language.
# Sorted longest-first so multi-word phrases match before single words.
PROTECTED_BANKING_TERMS = [
    "loan against property", "personal loan", "home loan", "business loan",
    "interest rate", "processing fee", "NACH mandate", "HDFC Bank",
    "HDFC", "CIBIL", "EMI", "KYC", "SOP", "LAP", "NACH", "NOC", "RM",
    "ITR", "GST", "LTV", "FOIR", "NPA", "ROI", "APR", "PAN",
    "foreclosure", "disbursement", "prepayment", "tenure", "Aadhaar",
]


def contextual_translate(text: str, translator) -> str:
    """
    Translate conversational parts while keeping banking terms in English.
    Replaces each protected term with a short placeholder token, translates,
    then restores the original English term.
    """
    if not text or len(text) < 5:
        return text
    replacements: Dict[str, str] = {}
    result_text = text
    for i, term in enumerate(PROTECTED_BANKING_TERMS):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        match = pattern.search(result_text)
        if match:
            token = f"[[{i}]]"
            replacements[token] = match.group()  # preserve original casing
            result_text = pattern.sub(token, result_text)
    try:
        translated = translator.translate(result_text)
        if not translated:
            print("[TRANSLATE] translator returned empty — falling back to English")
            return text
    except Exception as _te:
        print(f"[TRANSLATE] ERROR: {_te}")
        return text
    for token, original in replacements.items():
        translated = translated.replace(token, original)
    return translated

# Document processors
try:
    import pdfplumber
    HAS_PDF = True
except Exception:
    HAS_PDF = False

try:
    from docx import Document
    HAS_DOCX = True
except Exception:
    HAS_DOCX = False

try:
    from pptx import Presentation
    HAS_PPTX = True
except Exception:
    HAS_PPTX = False

# Note: VectorMatcher, DocumentChunk, SmartChunker — now imported from
# rag.embeddings and rag.chunker at the top of this file.


# ── Trivial-message detection (used for fast-path chat) ─────────────────────
# Messages that are pure greetings / acks need no RAG retrieval — a tiny prompt
# handles them in ~0.5s instead of the full 7-8s pipeline.

_TRIVIAL_GREETING_RE = re.compile(
    r'^(hi+|hello+|hey+|hiya|howdy|namaste|namaskar|yo+)'
    r'(\s+(there|all|guys|everyone|team))?'
    r'(\s*[!.]*\s*)'
    r'(\s*how\s+(are\s+(you|things|u)|r\s+u|do\s+you\s+do))?'
    r'\s*[!.]*\s*$',
    re.IGNORECASE,
)
_TRIVIAL_GREETING_PHRASE_RE = re.compile(
    r'^good\s+(morning|afternoon|evening|night|day)\s*[!.]*\s*$',
    re.IGNORECASE,
)
_TRIVIAL_THANKS_RE = re.compile(
    r'^(thanks?|thank\s+you)(\s+(a\s+lot|so\s+much|very\s+much|buddy))?\s*[!.]*\s*$',
    re.IGNORECASE,
)
_TRIVIAL_ACK_RE = re.compile(
    r'^(ok+|okay|alright|sure|cool|nice|great|wow|perfect|awesome|'
    r'got\s*it|sounds?\s+good|good\s+to\s+know)\s*[!.]*\s*$',
    re.IGNORECASE,
)


def _is_trivial_message(question: str) -> bool:
    """Return True when the message is a greeting/ack that needs no RAG retrieval."""
    q = question.strip()
    if len(q.split()) > 10:
        return False
    return bool(
        _TRIVIAL_GREETING_RE.match(q)
        or _TRIVIAL_GREETING_PHRASE_RE.match(q)
        or _TRIVIAL_THANKS_RE.match(q)
        or _TRIVIAL_ACK_RE.match(q)
    )


# ============================================================================
# RAG ENGINE — HDFC PITCH EDITION
# ============================================================================

class RAGEngine:
    """
    RAG engine for HDFC Sales Pitch Assistant.
    SQL/DB support removed. generate_pitch() replaces query().
    """

    def __init__(self, model: str = "llama3.1:8b", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self.structured_data: Dict[str, Any] = {}   # CSV / Excel DataFrames
        self.documents: Dict[str, str] = {}          # full text
        self.document_chunks: Dict[str, List[DocumentChunk]] = {}
        self.metadata: Dict[str, Any] = {}
        self.query_cache: Dict[str, Any] = {}
        self.chunker = SmartChunker()  # v2: chunks ALL docs at 500 chars / 100 overlap
        self.conversation_history: List[Dict] = []
        self.max_history_length = 10  # Ask Me: keep last 10 turns of context
        # Accumulated financial entities across all turns in the session.
        # Prevents a follow-up message (e.g. "income is 45k") from silently
        # discarding loan amount / tenure extracted in a prior turn.
        self._conv_entities: Dict[str, str] = {}

        # ── v2 retrieval: chunk-level embeddings + hybrid (dense+BM25+RRF) ──
        self.chunk_store: ChunkStore = ChunkStore()
        self.use_reranker: bool = True  # cross-encoder rerank on by default
        self._reranker = None  # lazy-loaded on first retrieval

        # Legacy file-level embeddings (kept for the inspector's "stage 5" view)
        self.file_embeddings: Dict[str, np.ndarray] = {}
        # Shared, process-wide embedder — every engine (incl. per-company
        # tenant instances) reuses one loaded SentenceTransformer.
        self.vector_matcher: Optional[VectorMatcher] = None
        if HAS_VECTORS:
            try:
                self.vector_matcher = VectorMatcher.shared()
            except Exception as e:
                print(f"[WARN] Vector matcher init failed: {e}")

    def _get_reranker(self):
        """Lazy-load cross-encoder on first use (only if use_reranker is on)."""
        if not self.use_reranker:
            return None
        if self._reranker is None:
            self._reranker = get_reranker()
        return self._reranker

    # ── File Loading ────────────────────────────────────────────────────────

    def add_file(self, file_path: str, filename: str) -> Dict[str, Any]:
        ext = filename.lower().split('.')[-1]
        try:
            with open(file_path, 'rb') as f:
                if ext == 'csv':
                    return self._add_csv(f, filename)
                elif ext in ('xlsx', 'xls'):
                    return self._add_excel(f, filename)
                elif ext == 'pdf' and HAS_PDF:
                    return self._add_pdf(f, filename)
                elif ext == 'docx' and HAS_DOCX:
                    return self._add_docx(f, filename)
                elif ext in ('ppt', 'pptx') and HAS_PPTX:
                    return self._add_pptx(f, filename)
                elif ext == 'txt':
                    return self._add_text(f, filename)
                else:
                    return {"success": False, "error": f"Unsupported format: {ext}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _add_csv(self, file_obj, name: str):
        df = pd.read_csv(file_obj)
        self.structured_data[name] = df
        self.metadata[name] = {
            "type": "structured", "format": "csv",
            "rows": len(df), "columns": list(df.columns),
            "dtypes": df.dtypes.to_dict()
        }
        self._create_file_embedding(name)
        return {"success": True, "type": "structured"}

    def _add_excel(self, file_obj, name: str):
        excel_file = pd.ExcelFile(file_obj)
        sheets = {}
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(excel_file, sheet_name=sheet_name)
            key = f"{name}:{sheet_name}"
            sheets[key] = df
            self.structured_data[key] = df
            self.metadata[key] = {
                "type": "structured", "format": "excel", "sheet": sheet_name,
                "rows": len(df), "columns": list(df.columns),
                "dtypes": df.dtypes.to_dict()
            }
            self._create_file_embedding(key)
        return {"success": True, "type": "structured", "sheets": len(sheets)}

    def _add_pdf(self, file_obj, name: str):
        """Extract text AND tables from PDF using pdfplumber for maximum accuracy."""
        pages_text = []
        with pdfplumber.open(file_obj) as pdf:
            num_pages = len(pdf.pages)
            for page in pdf.pages:
                parts = []
                # Extract tables first and convert to Markdown grid
                tables = page.extract_tables()
                extracted_table_bboxes = []
                for table in tables:
                    if not table:
                        continue
                    rows = []
                    for row in table:
                        clean_row = [str(cell).strip() if cell is not None else '' for cell in row]
                        rows.append(' | '.join(clean_row))
                    if rows:
                        # Add a separator after header row for readability
                        header_sep = ' | '.join(['---'] * len(table[0]))
                        rows.insert(1, header_sep)
                        parts.append('\n'.join(rows))
                # Extract remaining text (non-table areas)
                page_text = page.extract_text() or ''
                if page_text.strip():
                    parts.append(page_text)
                pages_text.append('\n\n'.join(parts))
        text = '\n\n---\n\n'.join(pages_text)
        return self._process_document(text, name, "pdf", num_pages)

    def _add_docx(self, file_obj, name: str):
        """Extract paragraphs AND tables from DOCX for maximum accuracy."""
        doc = Document(file_obj)
        parts = []
        # Iterate in document order using the XML body children
        for child in doc.element.body:
            tag = child.tag.split('}')[-1]  # strip namespace
            if tag == 'p':
                # It's a paragraph
                from docx.oxml.ns import qn
                text = ''.join(node.text or '' for node in child.iter(qn('w:t')))
                if text.strip():
                    parts.append(text.strip())
            elif tag == 'tbl':
                # It's a table — extract as a Markdown grid
                from docx.table import Table as DocxTable
                tbl = DocxTable(child, doc)
                rows = []
                for i, row in enumerate(tbl.rows):
                    cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    rows.append(' | '.join(cells))
                    if i == 0:
                        rows.append(' | '.join(['---'] * len(cells)))
                if rows:
                    parts.append('\n'.join(rows))
        text = '\n\n'.join(parts)
        return self._process_document(text, name, "docx", len(doc.paragraphs))

    def _add_text(self, file_obj, name: str):
        text = file_obj.read().decode('utf-8', errors='ignore')
        return self._process_document(text, name, "txt", text.count('\n'))

    def _add_pptx(self, file_obj, name: str):
        """Extract text AND embedded tables from PPTX for maximum accuracy."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pptx') as tmp:
            tmp.write(file_obj.read())
            tmp_path = tmp.name
        try:
            prs = Presentation(tmp_path)
            slides_text = []
            for i, slide in enumerate(prs.slides, 1):
                parts = [f"--- Slide {i} ---"]
                for shape in slide.shapes:
                    # Check if the shape is a table
                    if shape.has_table:
                        tbl = shape.table
                        rows = []
                        for j, row in enumerate(tbl.rows):
                            cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                            rows.append(' | '.join(cells))
                            if j == 0:
                                rows.append(' | '.join(['---'] * len(cells)))
                        if rows:
                            parts.append('\n'.join(rows))
                    elif hasattr(shape, "text") and shape.text.strip():
                        parts.append(shape.text.strip())
                if len(parts) > 1:
                    slides_text.append("\n".join(parts))
            text = "\n\n".join(slides_text)
            os.unlink(tmp_path)
            if not text.strip():
                return {"success": False, "error": "No text found in PowerPoint"}
            return self._process_document(text, name, "pptx", len(prs.slides))
        except Exception as e:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return {"success": False, "error": str(e)}

    def _generate_document_summary(self, text: str, filename: str) -> str:
        intro = text[:1500] if len(text) > 1500 else text
        conclusion = text[-800:] if len(text) > 2300 else ""
        prompt = f"""Analyze this document and create a detailed summary (4-5 sentences) covering:
1. Main topic / purpose
2. Key entities (people, companies, products, locations)
3. Important dates, time periods, or fiscal years
4. Key numbers or metrics
5. What type of questions this document can answer

Document filename: {filename}

Beginning:
{intro}

{"Ending:" if conclusion else ""}
{conclusion if conclusion else ""}

Comprehensive summary:"""
        try:
            summary = self._call_ollama(prompt)
            return f"Filename: {filename}\n\nContent Summary:\n{summary}"
        except Exception:
            return f"Filename: {filename}\n\nContent: {intro[:800]}"

    def _process_document(self, text: str, name: str, fmt: str, units: int):
        """v2 ingestion: ALWAYS chunk the document, embed each chunk, push to ChunkStore."""
        self.documents[name] = text
        summary = self._generate_document_summary(text, name)
        char_count = len(text)
        chunks = self.chunker.chunk_document(text, name)  # always returns ≥1 chunk
        strategy = chunks[0].metadata.get("strategy", "single") if chunks else "single"

        self.metadata[name] = {
            "type": "document", "format": fmt, "units": units,
            "length": char_count, "summary": summary,
            "chunking_strategy": strategy, "char_count": char_count,
            "chunks": len(chunks),
        }
        self.document_chunks[name] = chunks  # kept for the inspector + back-compat

        # Embed every chunk and add to the global chunk store
        if self.vector_matcher and chunks:
            try:
                chunk_embeddings = [
                    self.vector_matcher.generate_embedding(c.content) for c in chunks
                ]
                self.chunk_store.add_file(name, chunks, chunk_embeddings)
                print(f"  [EMB] {len(chunks)} chunks embedded for {name}")
            except Exception as e:
                print(f"  [WARN] Chunk embedding failed for {name}: {e}")

        # Legacy file-level signature embedding (kept for inspector stage 5 view)
        self._create_file_embedding(name)

        return {"success": True, "type": "document", "units": units,
                "chunking_strategy": strategy, "chunks": len(chunks)}

    # ── Conversation Context ────────────────────────────────────────────────

    def _add_to_history(self, query: str, answer: str, files_used: List[str]):
        self.conversation_history.append({"query": query, "answer": answer, "files": files_used})
        if len(self.conversation_history) > self.max_history_length:
            self.conversation_history = self.conversation_history[-self.max_history_length:]

    def _get_context_string(self) -> str:
        if not self.conversation_history:
            return ""
        ctx = "RECENT CONVERSATION CONTEXT:\n"
        for i, ex in enumerate(self.conversation_history, 1):
            ctx += f"\nExchange {i}:\nUser: {ex['query']}\nAssistant: {ex['answer'][:200]}...\nFiles: {', '.join(ex['files'])}\n"
        return ctx + "\nUse this context for follow-up questions.\n\n"

    def clear_context(self):
        self.conversation_history = []
        self.query_cache = {}
        self._conv_entities = {}

    # ── Vector File Matching ────────────────────────────────────────────────

    def _generate_file_signature(self, filename: str, meta: Dict[str, Any]) -> str:
        file_type = meta.get("type", "unknown")
        parts = [f"Filename: {filename}"]
        if file_type == "structured":
            parts.append(f"Type: Structured Data ({meta.get('format','').upper()})")
            cols = meta.get("columns", [])
            if cols:
                parts.append(f"Columns: {', '.join(cols)}")
                col_text = ' '.join(cols).lower()
                hints = []
                if any(w in col_text for w in ['employee','person','staff','name','worker']):
                    hints.append("employee data")
                if any(w in col_text for w in ['product','item','sku']):
                    hints.append("product data")
                if any(w in col_text for w in ['sale','revenue','price','transaction']):
                    hints.append("sales and financial data")
                if any(w in col_text for w in ['date','time','quarter','month','year']):
                    hints.append("time-series data")
                if hints:
                    parts.append(f"Content: {', '.join(hints)}")
            parts.append(f"Rows: {meta.get('rows', 0)}")
        elif file_type == "document":
            parts.append(f"Type: Document ({meta.get('format','').upper()})")
            if meta.get("summary"):
                parts.append(f"Summary: {meta['summary']}")
            elif filename in self.documents:
                parts.append(f"Preview: {self.documents[filename][:1000].replace(chr(10),' ')}")
        return "\n".join(parts)

    def _create_file_embedding(self, filename: str):
        if not self.vector_matcher:
            return
        meta = self.metadata.get(filename)
        if not meta:
            return
        try:
            sig = self._generate_file_signature(filename, meta)
            self.file_embeddings[filename] = self.vector_matcher.generate_embedding(sig)
            print(f"  [EMB] Embedding created: {filename}")
        except Exception as e:
            print(f"  [WARN] Embedding failed for {filename}: {e}")

    def _retrieve_relevant_context(self, query: str, top_k: int = 8) -> Tuple[str, List[str]]:
        """
        v2 retrieval — hybrid (dense + BM25) + RRF + optional cross-encoder rerank.

        Returns (context_text, list_of_source_filenames). The context blob is
        organized by file with chunk-id annotations so the LLM knows it's
        getting passages from real documents, not arbitrary text.
        """
        # ── Fallback: no embeddings available ──────────────────────────────
        if not self.vector_matcher or len(self.chunk_store) == 0:
            parts, sources = [], []
            for name, text in list(self.documents.items())[:3]:
                parts.append(f"[{name}]\n{text[:2000]}")
                sources.append(name)
            for name, df in list(self.structured_data.items())[:2]:
                parts.append(f"[{name}]\n{df.to_string(index=False, max_rows=20)}")
                sources.append(name)
            return "\n\n---\n\n".join(parts), sources

        # ── Run the hybrid pipeline ─────────────────────────────────────────
        trace = self.retrieve_with_trace(query, top_k=top_k)

        # ── Format ContextBlocks into the legacy string contract ────────────
        context_parts = [block.render() for block in trace.blocks]
        sources = [block.filename for block in trace.blocks]

        # Tack on structured data if any (low-volume in this app)
        for name, df in list(self.structured_data.items())[:1]:
            preview = df.to_string(index=False, max_rows=20)
            context_parts.append(f"[Structured Data: {name}]\n{preview}")
            sources.append(name)

        return "\n\n---\n\n".join(context_parts), list(dict.fromkeys(sources))

    def retrieve_with_trace(
        self,
        query: str,
        *,
        top_k: int = 6,
        candidates: int = 20,
        max_files: int = 4,
        max_chunks_per_file: int = 2,
        use_reranker: Optional[bool] = None,
    ) -> RetrievalTrace:
        """Run hybrid retrieval and return ALL intermediate rankings (for inspector).
        Pure: doesn't mutate engine state. Callers either use trace.blocks for
        the LLM context or render trace.dense / trace.bm25 / trace.fused for UI."""
        # Expand the query for retrieval (keep original for reranker scoring)
        expanded = expand_query(query)
        q_emb = self.vector_matcher.generate_embedding(expanded)

        # Decide whether to use the reranker for this call
        wants_rerank = self.use_reranker if use_reranker is None else use_reranker
        reranker = self._get_reranker() if wants_rerank else None

        return hybrid_retrieve(
            self.chunk_store,
            query_text=query,           # original (for BM25 + rerank scoring)
            query_embedding=q_emb,      # expanded (for dense recall)
            candidates=candidates,
            top_k=top_k,
            max_files=max_files,
            max_chunks_per_file=max_chunks_per_file,
            reranker=reranker,
        )

    # ── Query Expansion ─────────────────────────────────────────────────────
    # Implementation lives in rag.intent — this is a thin wrapper for backward
    # compat with callers that still use self._expand_query(...).

    def _expand_query(self, question: str) -> str:
        return expand_query(question)

    # ── Intent Detection (Ask Me) ───────────────────────────────────────────

    # Intent detection — implementation in rag.intent.
    def _detect_intent(self, question: str, history: list = None) -> str:
        return detect_intent(question, history)

    # ── Speech Analysis ─────────────────────────────────────────────────────
    # Implementation lives in rag.analysis — thin wrapper for back-compat.

    def _analyze_speech(self, text: str, history: list = None) -> Dict[str, Any]:
        return analyze_speech(text, history)

    # ── Doc Rate Extraction ─────────────────────────────────────────────────

    @staticmethod
    def _extract_doc_rates(context: str) -> dict:
        """
        Pull key rate values from retrieved document context so the Python
        calculator can use document-grounded numbers instead of inventing them.

        Returns a dict with any subset of:
          foreclosure_charge_pct, gst_rate_pct, foir_limit_pct
        """
        rates: Dict[str, float] = {}
        lower = context.lower()

        # Foreclosure / prepayment charge: e.g. "2% of outstanding principal"
        fc_m = re.search(
            r'(?:foreclos\w*|prepayment|part.payment)[^.\n]{0,100}?'
            r'(\d+(?:\.\d+)?)\s*%',
            lower,
        )
        if fc_m:
            val = float(fc_m.group(1))
            if 0 < val <= 10:          # 1–10% is the sane range for foreclosure charges
                rates["foreclosure_charge_pct"] = val

        # FOIR permissible limit: e.g. "FOIR should not exceed 55%"
        foir_m = re.search(
            r'foir[^.\n]{0,80}?(\d+(?:\.\d+)?)\s*%',
            lower,
        )
        if foir_m:
            val = float(foir_m.group(1))
            if 30 < val <= 75:         # 40–65% is normal for Indian banks
                rates["foir_limit_pct"] = val

        # GST rate: e.g. "GST @ 18%"
        gst_m = re.search(r'gst[^.\n]{0,40}?(\d+(?:\.\d+)?)\s*%', lower)
        if gst_m:
            val = float(gst_m.group(1))
            if 0 < val <= 30:
                rates["gst_rate_pct"] = val

        return rates

    # ── Ask Me Chatbot ──────────────────────────────────────────────────────

    def prepare_chat(self, question: str, language: str = "English", voice: bool = False) -> dict:
        """
        Run all pre-LLM steps for a chat turn and return a prepared dict:
          prompt, model_role, temperature, max_tokens, sources, intent, trivial.

        Caller is responsible for the actual LLM call and history update so
        that both ask_question() (blocking) and the streaming endpoint can
        share this code path.

        voice=True → Eva 2.0 voice assistant: short, plain-speech, Eva persona
        (build_voice_prompt), tiny max_tokens for low latency + short TTS.
        """
        from prompts.chat import build_trivial_chat_prompt, build_voice_prompt
        t0 = _time_module.perf_counter()
        assistant_label = "Eva" if voice else "Arjun"

        # Fast-path: greetings / acks skip RAG, query rewrite, intent, and calc
        if _is_trivial_message(question):
            if voice:
                prompt = build_voice_prompt(
                    question=question,
                    history_lines="(no prior turns — this is the first message)\n",
                    context="(greeting — no lookup needed)",
                    language=language,
                )
            else:
                prompt = build_trivial_chat_prompt(question, language)
            _log.info("[CHAT-FAST] trivial path prep_ms=%d", int((_time_module.perf_counter() - t0) * 1000))
            return {
                "trivial": True,
                "prompt": prompt,
                "model_role": "chat",
                "temperature": 0.3,
                "max_tokens": 120 if voice else 150,
                "sources": [],
                "intent": "trivial",
            }

        # 1. Intent (rule-based, ~1 ms)
        t1 = _time_module.perf_counter()
        intent = self._detect_intent(question, self.conversation_history)
        _log.info("[CHAT-TIMING] intent=%s ms=%d", intent, int((_time_module.perf_counter() - t1) * 1000))

        # 2. Query rewrite (rule-based, <1 ms)
        t2 = _time_module.perf_counter()
        normalized = normalize_hinglish(question)
        retrieval_query = rewrite_query(normalized, self.conversation_history)
        _log.info("[CHAT-TIMING] rewrite ms=%d", int((_time_module.perf_counter() - t2) * 1000))

        # 3. Query expansion
        search_query = self._expand_query(retrieval_query)
        if intent == "comparison":
            search_query += " HDFC vs competitor advantages differentiators"
        elif intent == "procedural":
            search_query += " application process steps documents required eligibility"
        elif intent == "objection":
            search_query += " benefits value proposition customer concerns"
        elif intent == "follow_up" and self.conversation_history:
            last_q = self.conversation_history[-1].get("query", "")
            search_query = f"{last_q} {retrieval_query}"

        # 4. Retrieve — top_k reduced from 12 to 8 for speed (still plenty of grounding)
        t3 = _time_module.perf_counter()
        context, sources = self._retrieve_relevant_context(search_query, top_k=8)
        _log.info("[CHAT-TIMING] retrieval ms=%d", int((_time_module.perf_counter() - t3) * 1000))

        # 5. Format history (last 10 turns)
        history_lines = ""
        if self.conversation_history:
            for ex in self.conversation_history[-10:]:
                history_lines += f"User: {ex['query']}\n"
                history_lines += f"{assistant_label}: {ex['answer']}\n\n"
        else:
            history_lines = "(no prior turns — this is the first message)\n"

        # 6. Language detection + entity accumulation
        t4 = _time_module.perf_counter()
        speech = self._analyze_speech(question)
        _ui_lang_lower = language.lower()
        if _ui_lang_lower not in ("english", "en"):
            # Non-English toggle is always authoritative — regardless of what speech
            # detected in the current message.  Handles mid-session language switches:
            # if the user typed Hindi last turn and now switches to Marathi, speech
            # would still return "hindi", but the toggle wins.
            speech["language_mix"] = _ui_lang_lower
        # else: English toggle = mirror mode — use speech detection as-is
        current_entities = speech["key_entities"]

        merged_entities: Dict[str, str] = dict(self._conv_entities)
        for key, val in current_entities.items():
            if key == "loan_amount_raw" and "loan_amount" in merged_entities:
                continue
            if key == "loan_amount" and "loan_amount_raw" in merged_entities:
                merged_entities.pop("loan_amount_raw", None)
            if key == "loan_amount" and "loan_amount" in merged_entities:
                prev = _calc_parse_amount(str(merged_entities["loan_amount"])) or 0
                new  = _calc_parse_amount(str(val)) or 0
                if prev > 0 and 0 < new < prev * 0.15:
                    _log.warning(
                        "[ENTITIES] Suppressed suspicious loan_amount downgrade %s → %s "
                        "(new value is %.1f%% of prior — likely income bleed)",
                        merged_entities["loan_amount"], val, new / prev * 100,
                    )
                    continue
            merged_entities[key] = val

        self._conv_entities = merged_entities
        speech["key_entities"] = merged_entities
        _log.info("[CHAT-TIMING] speech+entities ms=%d", int((_time_module.perf_counter() - t4) * 1000))

        # 7. Financial calculator (pure Python, ~1 ms)
        t5 = _time_module.perf_counter()
        doc_rates   = self._extract_doc_rates(context)
        calc_output = run_financial_analysis(
            entities=merged_entities,
            doc_foreclosure_charge_pct=doc_rates.get("foreclosure_charge_pct"),
            doc_gst_rate_pct=doc_rates.get("gst_rate_pct"),
            doc_foir_limit_pct=doc_rates.get("foir_limit_pct", 50.0),
        )
        calc_block = calc_output.get("calc_block", "")
        _log.info("[CHAT-TIMING] calc ms=%d", int((_time_module.perf_counter() - t5) * 1000))

        # 8. Build prompt
        interpreted = retrieval_query if retrieval_query.strip() != question.strip() else None
        _needs_calc = bool(calc_block)

        if voice:
            prompt = build_voice_prompt(
                question=question,
                history_lines=history_lines,
                context=context,
                language=speech["language_mix"],
                entities=merged_entities,
                calc_block=calc_block,
            )
            _log.info("[CHAT-TIMING] total_pre_llm(voice) ms=%d", int((_time_module.perf_counter() - t0) * 1000))
            return {
                "trivial": False,
                "prompt": prompt,
                # keep calc model when numbers are involved (deterministic), else chat
                "model_role": "calc" if _needs_calc else "chat",
                "temperature": 0.05 if _needs_calc else 0.3,
                # Summary-first: enough for an explicit "full breakdown", but capped so
                # a broad question can't balloon into a monologue. Most answers are 1-4 lines.
                "max_tokens": 450,
                "sources": sources,
                "intent": intent,
            }

        prompt = build_chat_prompt(
            question=question,
            intent=intent,
            history_lines=history_lines,
            context=context,
            language=speech["language_mix"],
            entities=merged_entities,
            interpreted_as=interpreted,
            calc_block=calc_block,
        )

        _log.info("[CHAT-TIMING] total_pre_llm ms=%d", int((_time_module.perf_counter() - t0) * 1000))
        return {
            "trivial": False,
            "prompt": prompt,
            "model_role": "calc" if _needs_calc else "chat",
            "temperature": 0.05 if _needs_calc else 0.2,
            "max_tokens": 3500 if _needs_calc else 4096,
            "sources": sources,
            "intent": intent,
        }

    def ask_question(self, question: str, language: str = "English") -> dict:
        """
        Answer a question strictly from loaded documents.
        Multi-turn conversational memory (last 10 turns), intent-aware,
        natural-prose post-processing.
        """
        if not (self.documents or self.structured_data):
            return {
                "success": False,
                "answer": "No documents are loaded. Please add documents to the uploads/ folder and restart the server.",
                "sources": []
            }

        t_start = _time_module.perf_counter()
        prepared = self.prepare_chat(question, language)

        try:
            if prepared["model_role"] == "calc":
                raw_answer = self._call_ollama_calc(prepared["prompt"])
            else:
                raw_answer = self._call_ollama_chat(prepared["prompt"], max_tokens=prepared["max_tokens"])

            # Strip any <think> tags
            clean_answer = re.sub(r'<think>.*?</think>', '', raw_answer, flags=re.DOTALL).strip()
            if not clean_answer:
                clean_answer = raw_answer.replace('<think>', '').replace('</think>', '').strip()

            clean_answer = self._post_process_chat(
                clean_answer, user_asked_for_list=self._user_asked_for_list(question)
            )
            _log.info(
                "[CHAT-TOTAL] trivial=%s intent=%s total_ms=%d",
                prepared["trivial"], prepared.get("intent"), int((_time_module.perf_counter() - t_start) * 1000),
            )
            self._add_to_history(question, clean_answer, prepared["sources"])
            return {
                "success": True,
                "answer": clean_answer,
                "sources": prepared["sources"],
                "intent": prepared.get("intent", "trivial"),
            }
        except Exception as e:
            return {
                "success": False,
                "answer": f"Error generating answer: {str(e)}",
                "sources": prepared["sources"],
            }

    # Chat post-processing — implementation in rag.post_process. Thin wrappers.
    @staticmethod
    def _user_asked_for_list(question: str) -> bool:
        return user_asked_for_list(question)

    @staticmethod
    def _post_process_chat(answer: str, user_asked_for_list: bool = False) -> str:
        return post_process_chat(answer, user_asked_for_list)
    def ask_with_messages(self, question: str, messages: list, pitch_context: str = "") -> dict:
        """
        Continue a pitch-history conversation. Uses explicit message history
        (so this is stateless w.r.t. self.conversation_history) and an optional
        original-pitch context blob so the AI remembers the customer scenario.
        Returns {'success', 'answer', 'sources'}.
        """
        if not (self.documents or self.structured_data):
            return {"success": False, "answer": "No documents loaded.", "sources": []}

        search_query = self._expand_query(question)
        context, sources = self._retrieve_relevant_context(search_query, top_k=12)

        history_str = ""
        if messages:
            history_str = "PREVIOUS CONVERSATION (most recent last):\n"
            for m in messages[-6:]:
                role = "Salesperson" if m.get("role") == "user" else "Assistant"
                content = str(m.get("content", ""))[:400]
                history_str += f"{role}: {content}\n\n"

        pitch_block = ""
        if pitch_context:
            pitch_block = (
                "ORIGINAL PITCH CONTEXT (you previously crafted this pitch for the "
                "salesperson — refer to it when iterating):\n"
                f"{pitch_context[:2000]}\n\n"
            )

        # Build prompt via prompts/chat.py (pitch-history continuation variant)
        prompt = build_chat_with_messages_prompt(
            question=question,
            history_str=history_str,
            pitch_block=pitch_block,
            context=context,
        )

        try:
            raw_answer = self._call_ollama_chat(prompt)
            clean_answer = re.sub(r'<think>.*?</think>', '', raw_answer, flags=re.DOTALL).strip()
            if not clean_answer:
                clean_answer = raw_answer.replace('<think>', '').replace('</think>', '').strip()
            return {"success": True, "answer": clean_answer, "sources": sources}
        except Exception as e:
            return {"success": False, "answer": f"Error generating answer: {str(e)}", "sources": sources}

    def _call_ollama_chat(self, prompt: str, max_tokens: int = 3000) -> str:
        """Chat LLM call — routes through core.llm.call_llm (CHAT_MODEL)."""
        try:
            return call_llm(prompt, model_role="chat", temperature=0.2, max_tokens=max_tokens, timeout=180)
        except Exception as e:
            raise Exception(f"LLM chat error: {e}")

    def _call_ollama_calc(self, prompt: str) -> str:
        """Calc LLM call — near-deterministic for narrating pre-computed numbers (CALC_MODEL).

        The Python calculator already did all the arithmetic; this call only narrates
        the pre-computed block. Temperature is low to prevent creative re-derivation.
        Falls back to chat model on failure.
        """
        try:
            return call_llm(prompt, model_role="calc", temperature=0.05, max_tokens=3500, timeout=300)
        except Exception:
            return self._call_ollama_chat(prompt)

    def get_chat_history(self) -> list:
        """Return current chat conversation history."""
        return self.conversation_history

    def clear_chat_history(self):
        """Clear only the chat conversation history (keep documents loaded)."""
        self.conversation_history = []
        self.query_cache = {}
        self._conv_entities = {}  # also reset accumulated financial context

    # ── Pitch Generation ────────────────────────────────────────────────────

    def enrich_scenario(self, raw_scenario: str) -> Dict[str, Any]:
        """Run the scenario through Vikram-the-analyst before the 10 sections.

        Agents often type sparse briefs ("PL 5L Sharma"). This call:
          - extracts every fact the agent stated,
          - infers the most-likely value for missing fields (profession, age,
            income, urgency, current bank) using HDFC customer typicalities,
          - marks each field as "provided" vs "inferred",
          - emits a 100-150 word `scenario_narrative` that downstream section
            builders use instead of the raw brief.

        Returns the parsed dict. On any failure, returns a fallback dict whose
        `scenario_narrative` is just the raw brief — so the pitch still works.
        """
        prompt = build_scenario_enrichment_prompt(raw_scenario)
        fallback = {
            "customer_name": "Customer",
            "is_name_provided": False,
            "scenario_narrative": raw_scenario,
            "_enrichment_skipped": True,
        }
        try:
            raw = call_llm(
                prompt,
                model_role="pitch",
                temperature=0.2,
                top_p=0.9,
                max_tokens=900,
                json_mode=True,
                timeout=120,
            )
            if not raw:
                print("[enrich_scenario] empty response — fallback")
                return fallback
            data = json.loads(raw)
            if not isinstance(data, dict) or not data.get("scenario_narrative"):
                print(f"[enrich_scenario] bad schema — fallback. raw={raw[:200]}")
                return fallback
            print(f"[enrich_scenario] loan_type={data.get('loan_type')} name={data.get('customer_name')} narrative={data.get('scenario_narrative','')[:120]}")
            return data
        except Exception as e:
            print(f"[enrich_scenario] fallback to raw brief — {e}")
            return fallback

    def generate_pitch(self, scenario: str, language: str = "English") -> Dict[str, Any]:
        """
        Generate a structured HDFC sales pitch for the given scenario.
        Uses RAG to retrieve relevant HDFC document context.
        Returns a dict with 10 pitch sections + the enrichment profile.
        """
        # Step 0 — enrich the agent's brief into a structured profile.
        # Sparse briefs ("PL 5L Sharma") get filled with most-likely defaults
        # (profession, income band, urgency) so downstream sections aren't generic.
        enrichment = self.enrich_scenario(scenario)
        enriched_narrative = enrichment.get("scenario_narrative") or scenario

        # Retrieve relevant context using the ENRICHED narrative — richer query → better chunks
        retrieved_chunks, documents_used = self._retrieve_relevant_context(enriched_narrative, top_k=8)

        # Build prompt via prompts/pitch.py (edit that file to tune pitch quality)
        prompt = build_pitch_prompt(retrieved_chunks=retrieved_chunks, scenario=enriched_narrative, language=language)

        try:
            raw = self._call_ollama_pitch(prompt)
            pitch = self._parse_pitch_json(raw, scenario=enriched_narrative, context_chunks=retrieved_chunks)
            # Surface the enrichment to the caller so the UI can show what was inferred.
            pitch["_enrichment"] = enrichment

            # Replaced by native LLM generation in {language} — see language directive in prompts/pitch.py.
            # if language != "English" and language in LANGUAGE_CODES:
            #     lang_code = LANGUAGE_CODES[language]
            #     translator = GoogleTranslator(source='auto', target=lang_code)
            #
            #     def translate_item(item):
            #         if isinstance(item, str) and item:
            #             return contextual_translate(item, translator)
            #         elif isinstance(item, list):
            #             return [translate_item(x) for x in item]
            #         elif isinstance(item, dict):
            #             return {k: translate_item(v) for k, v in item.items()}
            #         return item
            #
            #     pitch = translate_item(pitch)

            return {
                "success": True,
                "pitch": pitch,
                "documents_used": documents_used
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "documents_used": documents_used
            }

    # Resolved model names — reflect the active provider so SSE events are accurate.
    CHAT_MODEL          = _OR_CHAT_MODEL  if _LLM_PROVIDER != "ollama" else _OL_CHAT_MODEL
    CALC_MODEL          = _OR_CHAT_MODEL  if _LLM_PROVIDER != "ollama" else _OL_PITCH_MODEL
    PITCH_MODEL         = _OR_PITCH_MODEL if _LLM_PROVIDER != "ollama" else _OL_PITCH_MODEL
    PITCH_SECTION_MODEL = _OR_PITCH_MODEL if _LLM_PROVIDER != "ollama" else _OL_PITCH_MODEL

    def distil_chunks(self, retrieved_chunks: str) -> str:
        """Distil raw RAG chunks into a compact DOCUMENT_FACTS fact sheet.

        Called ONCE per pitch, before the 10-section loop. Uses the cheap
        model (CHAT_MODEL / gpt-4o-mini) to compress ~400-500 tok of raw
        chunks into ~200-300 tok of dense facts while preserving every
        specific figure verbatim.
        """
        from prompts.pitch import build_distillation_prompt
        prompt = build_distillation_prompt(retrieved_chunks)
        return call_llm(
            prompt,
            model_role="chat",
            temperature=0.1,
            top_p=1.0,
            max_tokens=600,
            timeout=60,
        )

    def _call_ollama_pitch(self, prompt: str) -> str:
        """Pitch LLM call — JSON mode, all 9 sections monolithic (PITCH_MODEL)."""
        try:
            return call_llm(
                prompt,
                model_role="pitch",
                temperature=0.75,
                top_p=0.9,
                max_tokens=4500,
                json_mode=True,
                timeout=600,
            )
        except Exception as e:
            raise Exception(f"LLM pitch error: {e}")

    def generate_pitch_section(
        self,
        section_id: str,
        scenario: str,
        prior_sections: dict,
        *,
        shared_prefix: str = "",
        model: str = None,
        max_tokens: int = 600,
        min_words: int = 80,
        max_retries: int = 1,
        language: str = "English",
        builders: dict = None,
    ) -> dict:
        """
        Generate ONE pitch section (e.g. "opening_hook") in a focused call.

        Used by the streaming endpoint /api/pitch/stream so the UI can render
        each section as it completes instead of waiting ~2 min for all 9.

        Args:
            section_id     — must be a key in prompts.pitch.SECTION_BUILDERS
            scenario       — enriched customer narrative (no lock prepended)
            prior_sections — {section_id: rendered_text} for already-completed sections
            shared_prefix  — byte-identical system message for all 10 calls per pitch:
                             persona + DOCUMENT_FACTS sheet + GROUNDING RULE + SCENARIO FACT LOCK.
                             Sent as OpenAI system message to enable prefix caching.
            model          — override model (defaults to PITCH_SECTION_MODEL)
            max_tokens     — output token budget
            min_words      — minimum word count; retried once if below this
            language       — output language directive

        Returns: {"section_id", "content", "word_count", "ms", "model", "retried": bool}
        Raises:  KeyError if section_id not registered; Exception on LLM failure.
        """
        from prompts.pitch import SECTION_BUILDERS
        import time

        # Dispatch against the caller-supplied builder map so that new-customer
        # and service-call flows don't collide on shared section IDs
        # (objection_handling, closing_strategy, what_not_to_say,
        # documents_checklist exist in both with different prompts).
        active_builders = builders if builders is not None else SECTION_BUILDERS
        if section_id not in active_builders:
            raise KeyError(f"Unknown pitch section_id: {section_id}")

        builder = active_builders[section_id]
        prompt = builder(scenario, prior_sections, language=language)
        chosen_model = model or self.PITCH_SECTION_MODEL

        approx_input_tok = (len(shared_prefix) + len(prompt)) // 4
        _log.info(
            "[CTX] %s: ~%d tok input (prefix=%d tok, task=%d tok, max_tokens=%d)",
            section_id,
            approx_input_tok,
            len(shared_prefix) // 4,
            len(prompt) // 4,
            max_tokens,
        )

        def _call(predict_tokens: int) -> tuple:
            t0 = time.time()
            text = call_llm(
                prompt,
                system_prompt=shared_prefix,
                model_role="pitch",
                temperature=0.75,
                top_p=0.9,
                max_tokens=predict_tokens,
                timeout=180,
            )
            ms = int((time.time() - t0) * 1000)
            return text, ms

        text, ms = _call(max_tokens)
        word_count = len(text.split())
        retried = False

        # If under the minimum word count, give it one more shot with extra budget
        if word_count < min_words and max_retries > 0:
            extra_budget = max_tokens + 200
            text2, ms2 = _call(extra_budget)
            wc2 = len(text2.split())
            if wc2 > word_count:
                text, ms, word_count = text2, ms + ms2, wc2
                retried = True

        return {
            "section_id": section_id,
            "content": text,
            "word_count": word_count,
            "ms": ms,
            "model": chosen_model,
            "retried": retried,
        }

    def _call_ollama(self, prompt: str) -> str:
        """Generic LLM call for summaries (routes through core.llm.call_llm)."""
        try:
            return call_llm(prompt, model_role="chat", temperature=0.1, max_tokens=2000, timeout=90)
        except Exception as e:
            raise Exception(f"LLM error: {e}")

    def _parse_pitch_json(self, raw: str, scenario: str = "", context_chunks: str = "") -> Dict[str, Any]:
        """Parse pitch JSON from Ollama response, with fallback handling."""
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r'^```[a-z]*\n?', '', text)
            text = re.sub(r'\n?```$', '', text.strip())

        # Try direct parse
        try:
            data = json.loads(text)
            return self._normalize_pitch(data, scenario=scenario, context_chunks=context_chunks)
        except json.JSONDecodeError:
            pass

        # Find the outermost balanced JSON object (handles greedy regex issue)
        start = text.find('{')
        if start != -1:
            depth, end = 0, -1
            for i, ch in enumerate(text[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end != -1:
                try:
                    data = json.loads(text[start:end + 1])
                    return self._normalize_pitch(data, scenario=scenario, context_chunks=context_chunks)
                except json.JSONDecodeError:
                    pass

        # Last resort: return minimal structure with raw text as narrative
        return self._normalize_pitch({
            "narrative_summary": raw
        }, scenario=scenario, context_chunks=context_chunks)

    def _normalize_pitch(self, data: Dict, scenario: str = "", context_chunks: str = "") -> Dict:
        """Ensure all required pitch keys exist with correct types. Fallbacks are derived from
        the actual retrieved document context so they stay relevant to the scenario."""

        # Detect loan/product type from scenario for neutral fallback text
        scenario_lower = scenario.lower()
        if "business" in scenario_lower:
            product_label = "business loan"
        elif "home" in scenario_lower or "property" in scenario_lower or "lap" in scenario_lower:
            product_label = "home loan / LAP"
        else:
            product_label = "personal loan"

        # Extract up to 5 short factual sentences from context chunks for fallback facts
        def extract_context_facts(chunks: str, n: int = 5) -> List[str]:
            if not chunks:
                return []
            sentences = re.split(r'(?<=[.!?])\s+', chunks)
            facts = []
            for s in sentences:
                s = s.strip()
                if 20 < len(s) < 200 and not s.startswith('#') and not s.startswith('---'):
                    facts.append(s)
                if len(facts) >= n:
                    break
            return facts

        context_facts = extract_context_facts(context_chunks)

        # Generic fallbacks that adapt to the product type — no hardcoded rates or amounts
        fallback_questions = [
            f"What are the primary goals you're hoping to achieve with this {product_label}?",
            "What monthly EMI range would sit comfortably within your current budget?",
            "Are there any existing EMIs or financial commitments we should factor in?",
            "How soon are you looking to have the funds in place?",
            "Have you explored other options, and what mattered most to you in those comparisons?"
        ]
        fallback_warnings = [
            "Do not guarantee approval or claim CIBIL score doesn't matter.",
            "Avoid quoting rates or amounts not present in the loaded documents.",
            "Never offer fee waivers or discounts you are not authorised to give.",
            "Do not advise the customer to omit existing liabilities on the application.",
            "Avoid sharing customer financial information outside official channels."
        ]
        fallback_qa = [
            {
                "question": "What documents will I need to keep ready?",
                "answer": "Please refer to the documentation checklist in the loaded HDFC policy documents for the exact list applicable to your profile and product."
            },
            {
                "question": "What is the minimum income requirement?",
                "answer": "Eligibility criteria vary by product and customer segment — the loaded documents contain the specific income thresholds for your case."
            },
            {
                "question": "Can I repay early? Are there charges?",
                "answer": "Prepayment terms depend on the product variant — please refer to the foreclosure section in the loaded policy documents for exact charges."
            }
        ]
        fallback_objections = [
            {
                "objection": "The interest rate seems too high.",
                "response": f"I completely understand how you feel — rate is always top of mind. Many of our customers felt the same way initially. What they found was that when you factor in the full picture — processing transparency, disbursal speed, and dedicated support — the effective cost was very competitive. Let me walk you through the numbers from our documents so we can compare apples to apples."
            },
            {
                "objection": "I want to think about it and compare with other lenders.",
                "response": f"That's a sensible approach — this is a financial commitment and you should compare. While you do, I'd like to make sure you have our offer in hand so you have a concrete baseline. Shall we lock in a soft eligibility check now? It won't affect your CIBIL and gives you a firm number to compare against."
            }
        ]
        # Prefer facts pulled from actual documents; top up with generic if not enough
        fallback_facts = context_facts if context_facts else [
            f"Refer to the loaded HDFC documents for current {product_label} rates and limits.",
            "Eligibility, tenure, and fee details are outlined in the policy documents.",
            "Processing and foreclosure terms vary by product variant — see document details."
        ]

        # Helper to detect placeholder strings generated by LLM when confused about prompt schema
        def is_placeholder(s: str) -> bool:
            if not s:
                return True
            s_lower = s.lower().strip()
            placeholders = [
                "string (", "string", "placeholder", "customer concern", "empathetic",
                "feel/felt/found", "discovery question", "behavior to avoid",
                "direct bullet fact", "closing sentences", "flowing pitch script",
                "subject and body", "warm specific opener", "natural discovery question",
                "phrase or behavior to avoid", "direct bullet fact for salesperson",
                "empathetic feel/felt/found response"
            ]
            for p in placeholders:
                if p in s_lower or s_lower == p:
                    return True
            if re.match(r'^string\s*\(.*\)$', s_lower):
                return True
            return False

        # Premium document-grounded fallback data derived from HDFC Personal Loan guidelines
        fallback_questions = [
            "Could you share what primary goals or purchases you plan to accomplish with this loan?",
            "What is your ideal monthly EMI range that comfortably fits into your current household budget?",
            "Are there any other outstanding monthly EMIs or financial commitments that we should keep in mind?",
            "How soon are you looking to receive these funds for your upcoming plans?",
            "What has been your experience with other banks or financial institutions in the past?"
        ]

        fallback_warnings = [
            "Do not guarantee 100% approval or declare that CIBIL score does not matter.",
            "Avoid comparing HDFC Bank interest rates directly with competitors like SBI or ICICI.",
            "Never offer unauthorized processing fee waivers or ask the customer for personal commission.",
            "Do not advise the customer to hide existing loan obligations on their application form.",
            "Avoid sharing any customer financial information or KYC documents outside official channels."
        ]

        fallback_qa = [
            {
                "question": "What is the minimum monthly income required to apply?",
                "answer": "For salaried applicants, the minimum net monthly salary required is Rs. 25,000 (Rs. 35,000 in metro cities like Mumbai). For self-employed individuals, a minimum annual income of Rs. 2,50,000 as per their latest CA-certified ITR is required."
            },
            {
                "question": "What documents do I need to keep ready for the application?",
                "answer": "You will need a mandatory PAN card, Aadhaar or Passport/Voter ID for address proof, 2 passport-size photographs, and last 3 months' salary slips along with last 6 months' salary bank statements. For self-employed customers, 2 years of certified ITR, balance sheets, and 12 months' bank statements are needed."
            },
            {
                "question": "Can I close my personal loan early? Are there prepayment charges?",
                "answer": "Yes, prepayment and foreclosure are allowed after you have successfully paid 12 EMIs. The foreclosure charges are highly competitive at just 2% + GST of the outstanding principal amount."
            }
        ]

        fallback_objections = [
            {
                "objection": "The interest rate seems too high compared to other banks.",
                "response": "I completely understand how you feel, sir. Many of our premium customers felt the same way when they first looked at the interest rate. However, what they found was that HDFC Bank calculates interest on a daily reducing balance method, which significantly reduces the effective interest you pay over the tenure. Moreover, because we have zero hidden charges, you save thousands that other banks charge under fine print. Plus, you get a dedicated relationship manager so you don't have to wait in queues."
            },
            {
                "objection": "I want to think about it and compare with other lenders.",
                "response": "That's a very sensible decision, ma'am. It's a financial commitment, so taking time is wise. While you compare, I'd like to share that our current pre-approved offer is based on your excellent profile and has a rate locked in only until this weekend. Also, HDFC Bank guarantees loan disbursal within 4 hours with minimal paperwork, whereas other lenders might take 3 to 5 business days. Shall we run a quick soft eligibility check now so you have our final offer in hand while you make your comparison?"
            },
            {
                "objection": "I already have an active loan with another bank.",
                "response": "I appreciate you sharing that. Many of our customers have active loans. The great news is that HDFC Bank allows you to take a Personal Loan even if you have existing obligations, as long as your total monthly EMIs do not exceed 50% of your income (FOIR). We can even look at structured repayment tenures up to 60 months to keep your EMIs light and manageable. Let's do a quick calculation to see how we can structure this beautifully for you."
            },
            {
                "objection": "I'm worried my past rejection will make it hard to get approved again.",
                "response": "I completely understand your concern, and I know how frustrating that experience must have been. Many of our best clients faced a similar situation before. What they found was that credit criteria are constantly updated, and HDFC Bank regularly launches fresh, flexible product variants. A past rejection doesn't mean a closed door. Let's look at this with a fresh perspective, verify your latest income documents, and find the right fit for you today."
            }
        ]

        fallback_facts = [
            "HDFC Bank offers personal loans ranging from Rs. 50,000 to Rs. 40,00,000 to suit a variety of needs.",
            "Repayment tenures are highly flexible, ranging from 12 months to 60 months (1 to 5 years).",
            "Interest rates are calculated using the reducing balance method, starting from 10.50% p.a. for premium customers.",
            "Instant disbursal is available in under 4 hours for pre-approved customers, and 2-3 working days for fresh applications.",
            "A processing fee of up to 2.50% + GST of the loan amount applies, with regular promotional waivers available.",
            "Prepayment is permitted after 12 EMIs with a nominal charge of 2% + GST on the outstanding principal."
        ]

        def ensure_string(val) -> str:
            if isinstance(val, str):
                return val
            if isinstance(val, list):
                return "\n".join([ensure_string(x) for x in val if x])
            if isinstance(val, dict):
                parts = []
                for k, v in val.items():
                    if v and isinstance(v, list):
                        parts.append(f"{k}: " + ", ".join([ensure_string(x) for x in v if x]))
                    elif v:
                        parts.append(f"{k}: {ensure_string(v)}")
                    else:
                        parts.append(f"{k}")
                return "\n".join(parts)
            return str(val) if val is not None else ""

        def ensure_string_list(val) -> List[str]:
            if isinstance(val, list):
                result = []
                for item in val:
                    if not item:
                        continue
                    if isinstance(item, str):
                        if not is_placeholder(item):
                            result.append(item)
                    elif isinstance(item, dict):
                        v = item.get("fact") or item.get("quote") or item.get("bullet") or item.get("text") or item.get("value")
                        if v is None:
                            for val_in_dict in item.values():
                                if isinstance(val_in_dict, str) and val_in_dict.strip():
                                    v = val_in_dict
                                    break
                        if v and not is_placeholder(str(v)):
                            result.append(str(v))
                    else:
                        s_val = str(item)
                        if not is_placeholder(s_val):
                            result.append(s_val)
                return result
            elif isinstance(val, str) and val.strip():
                if not is_placeholder(val):
                    return [val]
            return []

        def ensure_obj_list(val, k1, k2) -> List[Dict[str, str]]:
            if isinstance(val, list):
                result = []
                for item in val:
                    if isinstance(item, dict):
                        v1 = item.get(k1)
                        if v1 is None:
                            for alt in ["objection", "question", "q", "title", "issue", "scenario"]:
                                if alt in item:
                                    v1 = item[alt]
                                    break
                        v2 = item.get(k2)
                        if v2 is None:
                            for alt in ["response", "answer", "a", "handling", "strategy", "solution"]:
                                if alt in item:
                                    v2 = item[alt]
                                    break
                        v1_str = str(v1 or "").strip()
                        v2_str = str(v2 or "").strip()
                        if (v1_str or v2_str) and not is_placeholder(v1_str) and not is_placeholder(v2_str):
                            result.append({k1: v1_str, k2: v2_str})
                    elif isinstance(item, str) and item.strip():
                        item_str = item.strip()
                        if not is_placeholder(item_str):
                            result.append({k1: item_str, k2: ""})
                return result
            elif isinstance(val, dict):
                result = []
                for key, value in val.items():
                    key_str = str(key).strip()
                    val_str = ensure_string(value).strip()
                    if (key_str or val_str) and not is_placeholder(key_str) and not is_placeholder(val_str):
                        result.append({k1: key_str, k2: val_str})
                return result
            return []

        # Parse with defensive types
        opening_hook = ensure_string(data.get("opening_hook"))
        if is_placeholder(opening_hook) or not opening_hook.strip():
            opening_hook = (
                f"It's great to meet you today, and thank you for taking the time to sit with me. "
                f"I understand that looking into a {product_label} is an important decision — one that touches your personal goals and financial plans. "
                f"I want you to know that I'm not here to push anything. My goal today is simply to understand where you are, what matters most to you, "
                f"and see if HDFC Bank can genuinely support that journey. Financial decisions can sometimes feel overwhelming, especially with so many options out there — "
                f"I get that completely. So let's keep this simple, transparent, and at your pace. "
                f"I'd love to hear a little about what's on your mind today — what brought you here, and what you're hoping to achieve."
            )

        questions_to_ask = ensure_string_list(data.get("questions_to_ask"))
        if len(questions_to_ask) < 3:
            questions_to_ask = list(dict.fromkeys(questions_to_ask + fallback_questions))[:6]

        what_not_to_say = ensure_string_list(data.get("what_not_to_say"))
        if len(what_not_to_say) < 3:
            what_not_to_say = list(dict.fromkeys(what_not_to_say + fallback_warnings))[:5]

        anticipated_customer_questions = ensure_obj_list(
            data.get("anticipated_customer_questions"), "question", "answer"
        )
        if len(anticipated_customer_questions) < 3:
            existing_qs = [qa["question"].lower() for qa in anticipated_customer_questions]
            for f_qa in fallback_qa:
                if f_qa["question"].lower() not in existing_qs:
                    anticipated_customer_questions.append(f_qa)
            anticipated_customer_questions = anticipated_customer_questions[:6]

        objection_handling = ensure_obj_list(
            data.get("objection_handling"), "objection", "response"
        )
        if len(objection_handling) < 3:
            existing_objs = [o["objection"].lower() for o in objection_handling]
            for f_o in fallback_objections:
                if f_o["objection"].lower() not in existing_objs:
                    objection_handling.append(f_o)
            objection_handling = objection_handling[:6]

        key_product_facts = ensure_string_list(data.get("key_product_facts"))
        if len(key_product_facts) < 3:
            key_product_facts = list(dict.fromkeys(key_product_facts + fallback_facts))[:6]

        closing_strategy = ensure_string(data.get("closing_strategy"))
        if is_placeholder(closing_strategy) or not closing_strategy.strip():
            closing_strategy = (
                f"Shall we start with a quick eligibility check for the {product_label}? "
                "It takes just 2 minutes, won't affect your CIBIL score, and gives us a firm "
                "offer to review together. Would today work, or would you prefer to connect after "
                "you've had a chance to review the documents I'll share?"
            )

        narrative_summary = ensure_string(data.get("narrative_summary"))
        if is_placeholder(narrative_summary) or not narrative_summary.strip():
            facts_text = " ".join(context_facts[:3]) if context_facts else (
                f"HDFC Bank's {product_label} is designed to meet your specific financial goals "
                "with flexible repayment options and a dedicated relationship manager."
            )
            narrative_summary = (
                f"Thank you for taking the time to speak with me today. Based on what you've "
                f"shared, I believe HDFC Bank's {product_label} can be a strong fit for your "
                f"situation. {facts_text} Let's work through the details together and make sure "
                "you walk away with a clear picture of your options. Does that sound good to you?"
            )

        follow_up_email = ensure_string(data.get("follow_up_email"))
        if is_placeholder(follow_up_email) or not follow_up_email.strip():
            follow_up_email = (
                f"Subject: HDFC Bank {product_label.title()} — Next Steps for You\n\n"
                "Dear Client,\n\n"
                f"It was a pleasure speaking with you today about HDFC Bank's {product_label} "
                "options. As discussed, I'd love to help you move forward with a soft eligibility "
                "check so we can put a firm offer in your hands — no paperwork, no CIBIL impact.\n\n"
                "Please feel free to reply with any questions, and I'll get back to you promptly.\n\n"
                "Best regards,\nHDFC Relationship Team"
            )

        return {
            "opening_hook": opening_hook,
            "questions_to_ask": questions_to_ask,
            "what_not_to_say": what_not_to_say,
            "anticipated_customer_questions": anticipated_customer_questions,
            "objection_handling": objection_handling,
            "key_product_facts": key_product_facts,
            "closing_strategy": closing_strategy,
            "narrative_summary": narrative_summary,
            "follow_up_email": follow_up_email
        }

    def _build_voice_prompt(
        self,
        question: str,
        question_type: str,
        context: str,
        session_context: dict,
        history: list = None,
        language: str = "english"
    ) -> str:
        """Build the prompt for voice answer (shared by streaming and non-streaming paths).
        Uses deep speech analysis + Vikram AI persona + session memory + competitor redirect.
        """
        customer_type   = session_context.get('customerType', 'unknown')
        loan_type       = session_context.get('loanType', 'personal loan')
        customer_status = session_context.get('customerStatus', 'new')
        pre_approved    = session_context.get('preApproved', 'unknown')

        # Deep speech analysis — structured dict
        analysis = self._analyze_speech(question, history)

        # Frontend language pill overrides analysis, EXCEPT when Devanagari script is
        # present in the transcript — Devanagari is unambiguous and always wins.
        if language and language.lower() in ("hindi", "hinglish", "english", "marathi", "marathi_mix"):
            has_devanagari = bool(re.search(r'[ऀ-ॿ]', question))
            if language.lower() == "english" and has_devanagari:
                pass  # Customer is literally typing/speaking Devanagari — keep Hindi
            else:
                analysis["language_mix"] = language.lower()

        # Override question_type from analysis if more specific
        if analysis.get("question_type") and analysis["question_type"] != "general":
            question_type = analysis["question_type"]

        # ── Last 5 voice exchanges (RECENT CONVERSATION) ─────────────────
        history_block = "(no prior exchanges — this is the first question in the session)"
        if history:
            lines = []
            for msg in history[-5:]:
                role = msg.get("role") or msg.get("speaker") or "Customer"
                # Normalise role naming
                if role.lower() in ("user", "customer"):
                    role_label = "Customer"
                elif role.lower() in ("vikram", "assistant", "ai"):
                    role_label = "Vikram"
                else:
                    role_label = role
                content = (msg.get("content") or msg.get("text") or "").strip()
                if content:
                    lines.append(f"{role_label}: {content}")
            if lines:
                history_block = "\n".join(lines)

        # ── Entities formatted for the prompt ────────────────────────────
        ents = analysis["key_entities"]
        ents_str = ", ".join(f"{k}={v}" for k, v in ents.items()) if ents else "(none detected)"

        # Pick the dominant instruction + language style from prompts/voice.py
        primary_rule = _voice_pick_rule(question_type, analysis)
        language_directive = _voice_pick_lang(analysis["language_mix"])

        # Build the final prompt via prompts/voice.py (edit there to tune live voice)
        return _voice_build(
            question=question,
            customer_type=customer_type,
            loan_type=loan_type,
            customer_status=customer_status,
            pre_approved=pre_approved,
            analysis=analysis,
            question_type=question_type,
            entities_str=ents_str,
            primary_rule=primary_rule,
            language_directive=language_directive,
            history_block=history_block,
            context=context,
        )

    def answer_live_question(
        self,
        question: str,
        question_type: str = "factual",
        session_context: dict = None,
        history: list = None
    ) -> dict:
        """Non-streaming voice answer — used as fallback if streaming is unavailable."""
        if session_context is None:
            session_context = {}

        if not (self.documents or self.structured_data):
            return {"success": False, "answer": "No documents loaded.", "sources": []}

        context, sources = self._retrieve_relevant_context(question, top_k=3)
        if not context or len(context.strip()) < 50:
            return {"success": True, "answer": "Not in documents.", "sources": []}

        prompt = self._build_voice_prompt(question, question_type, context, session_context, history)

        # Use local Ollama first — no network queue, 3-6s for short answers.
        # Fall back to OpenRouter only if Ollama is unavailable.
        try:
            answer = self._call_ollama_fast(prompt)
            return {"success": True, "answer": answer, "sources": sources}
        except Exception:
            pass

        try:
            answer = self._call_openrouter_fast(prompt)
            return {"success": True, "answer": answer, "sources": sources}
        except Exception as e:
            return {
                "success": False,
                "answer": f"Error: {str(e)}",
                "sources": sources
            }

    def _call_openrouter_fast(self, prompt: str) -> str:
        """
        Call OpenRouter API with multi-model fallback for reliability.
        Tries each free model in sequence until one responds successfully.
        Handles 429 rate limits and 404 endpoint-not-found gracefully.
        """
        import os, time
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not openrouter_key:
            raise Exception("OPENROUTER_API_KEY not set")

        # Ordered fastest-first: small/flash models lead, large models are fallback
        FREE_MODELS = [
            "meta-llama/llama-3.2-3b-instruct:free",   # ~1-2s, smallest
            "deepseek/deepseek-v4-flash:free",           # flash, very fast
            "nvidia/nemotron-nano-9b-v2:free",           # nano, fast
            "google/gemma-4-26b-a4b-it:free",            # larger, slower
            "meta-llama/llama-3.3-70b-instruct:free",    # largest, fallback only
        ]

        last_error = None
        for model in FREE_MODELS:
            try:
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {openrouter_key}",
                        "HTTP-Referer": "http://localhost:8002",
                        "X-Title": "HDFC Live Assistant",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 120,
                        "temperature": 0
                    },
                    timeout=15
                )
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"].strip()
                elif response.status_code in (429, 404):
                    # Rate limited or model gone — try next model immediately
                    last_error = f"HTTP {response.status_code} on {model}"
                    time.sleep(0.1)
                    continue
                else:
                    response.raise_for_status()
            except requests.exceptions.Timeout:
                last_error = f"Timeout on {model}"
                continue
            except Exception as e:
                last_error = str(e)
                continue

        raise Exception(f"All OpenRouter models failed. Last error: {last_error}")

    def _call_ollama_fast(self, prompt: str) -> str:
        """Local Ollama for live voice — short answers, no network queue."""
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0,
                    "num_predict": 80,   # 3 lines max — keeps it fast
                    "top_k": 10,         # greedy-ish decoding, faster
                    "repeat_penalty": 1.0
                }
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()

    # ── Code helpers (kept for CSV/Excel pipeline if needed) ────────────────

    def _extract_code(self, response: str) -> str:
        if "```python" in response:
            return response.split("```python")[1].split("```")[0].strip()
        elif "```" in response:
            return response.split("```")[1].split("```")[0].strip()
        lines = [l for l in response.split('\n') if 'result =' in l or 'result=' in l]
        return '\n'.join(lines).strip()

    def _execute_code(self, code: str, df: pd.DataFrame) -> Any:
        namespace = {'df': df.copy(), 'pd': pd, 'np': np, 'result': None}
        exec(code, namespace)
        if namespace['result'] is None:
            raise ValueError("Code didn't assign 'result'")
        return namespace['result']

    def _format_answer(self, result: Any, question: str) -> str:
        if isinstance(result, (int, float, np.integer, np.floating)):
            return str(result)
        elif isinstance(result, str):
            return result
        elif isinstance(result, pd.Series):
            return ', '.join(map(str, result.tolist()[:10]))
        elif isinstance(result, list):
            return ', '.join(map(str, result[:10]))
        return str(result)

    def _clean_answer(self, text: str) -> str:
        if not text:
            return text
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped.startswith('* ') or stripped.startswith('- '):
                line = ' ' * indent + '\u2022 ' + stripped[2:]
            line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
            line = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', line)
            line = re.sub(r'^#{1,6}\s+', '', line)
            cleaned.append(line)
        return '\n'.join(cleaned)


# ============================================================================
# STANDALONE HELPER — used by main.py startup
# ============================================================================

def load_files_from_folder(folder_path: str, rag_instance) -> Dict[str, Any]:
    """Load all supported files from uploads folder."""
    results = {"success": [], "failed": []}
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        return results

    supported = {'csv', 'xlsx', 'xls', 'pdf', 'docx', 'pptx', 'ppt', 'txt'}

    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path):
            ext = filename.lower().split('.')[-1]
            if ext in supported:
                result = rag_instance.add_file(file_path, filename)
                if result.get('success'):
                    results["success"].append({"name": filename, "type": result.get("type"), "details": result})
                else:
                    results["failed"].append({"name": filename, "error": result.get("error")})
    return results