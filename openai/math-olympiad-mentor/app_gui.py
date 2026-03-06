"""
Grade 6 Maths Olympiad Coach – GUI app

Chat-style window: type your question, get the coach's reply.
Renders coach replies as HTML (tables, lists, bold) if tkhtmlview and markdown
are installed; otherwise falls back to plain text.

Uses Ollama by default (model: gemma3). To use OpenAI, set USE_OLLAMA=0 and OPENAI_API_KEY.
"""

import html as html_lib
import os
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont, scrolledtext, filedialog

# Default to Ollama. To use OpenAI, set USE_OLLAMA=0 and OPENAI_API_KEY.
os.environ.setdefault("USE_OLLAMA", "1")
os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:latest")

from agent import (
    get_reply,
    get_concepts_from_book,
    extract_pdf_text,
    load_directory,
    get_books_dir_path,
    get_source_signature,
    parse_concepts_from_response,
    parse_quiz_options,
    parse_quiz_question_text,
    parse_last_answer_correct,
    load_persistent_memory,
    save_persistent_memory,
    QUIZ_MC_INSTRUCTION,
    QUIZ_CONFIRM_INSTRUCTION,
    QUIZ_ABOUT_CONCEPT,
    EXPLAIN_CONCEPT_END,
    EXPLAIN_FROM_BOOK_INSTRUCTION,
    NUM_QUIZ_QUESTIONS,
    QUIZ_PASS_PERCENT,
)

# Optional: nice HTML rendering
try:
    from tkhtmlview import HTMLScrolledText
    import markdown
    HAS_HTML = True
except ImportError:
    HAS_HTML = False

# Base CSS for the chat content when using HTML (readable markdown)
_CHAT_CSS = """
<style>
body { font-family: system-ui, -apple-system, sans-serif; font-size: 13pt; line-height: 1.5; color: #111; padding: 10px; max-width: 720px; }
.msg-user { margin: 12px 0; padding: 10px 12px; background: #eff6ff; border-left: 4px solid #2563eb; border-radius: 0 8px 8px 0; }
.msg-coach { margin: 12px 0; padding: 10px 12px; background: #f8fafc; border-left: 4px solid #059669; border-radius: 0 8px 8px 0; }
.msg-label { font-weight: 700; margin-bottom: 6px; color: #1e293b; }
.msg-coach table { border-collapse: collapse; margin: 10px 0; width: 100%; font-size: 12pt; }
.msg-coach th, .msg-coach td { border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }
.msg-coach th { background: #f1f5f9; font-weight: 600; }
.msg-coach tr:nth-child(even) { background: #f8fafc; }
.msg-coach ul, .msg-coach ol { margin: 8px 0; padding-left: 24px; }
.msg-coach li { margin: 4px 0; }
.msg-coach p { margin: 8px 0; }
.msg-coach h1, .msg-coach h2, .msg-coach h3 { margin: 12px 0 6px; font-weight: 600; }
.msg-coach h1 { font-size: 1.25rem; }
.msg-coach h2 { font-size: 1.1rem; background: #f0fdf4; padding: 8px 12px; border-radius: 6px; border-left: 4px solid #059669; }
.msg-coach code { background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
.msg-coach pre { background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 10px 0; font-size: 12px; }
.msg-coach pre code { background: none; padding: 0; color: inherit; }
.msg-coach blockquote { border-left: 4px solid #94a3b8; margin: 8px 0; padding-left: 16px; color: #475569; }
.msg-coach strong { font-weight: 700; color: #0f172a; }
</style>
"""


def _latex_to_plain(text: str) -> str:
    """Convert simple LaTeX/math notation to plain text for display."""
    if not text:
        return text
    text = re.sub(r"\\\[\s*(.*?)\s*\\\]", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\\\((.*?)\\\)", r"\1", text, flags=re.DOTALL)
    text = text.replace("\\times", "×").replace("\\div", "÷")
    text = re.sub(r"\\boxed\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", text)
    return text.strip()


def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML (tables, lists, bold, code, etc.) for readable output."""
    if not HAS_HTML or not text:
        return html_lib.escape(text)
    try:
        html = markdown.markdown(
            text.strip(),
            extensions=["tables", "nl2br", "fenced_code", "sane_lists"],
            output_format="html",
        )
        return html
    except Exception:
        return html_lib.escape(text)


def _build_chat_html(display_messages: list[dict]) -> str:
    """Build one HTML document for the full conversation."""
    parts = [_CHAT_CSS, "<body>"]
    for m in display_messages:
        role = m["role"]
        content = m["content"] or ""
        if role == "user":
            safe = html_lib.escape(content).replace("\n", "<br>\n")
            parts.append(
                '<div class="msg-user">'
                '<span class="msg-label">You:</span><br>\n' + safe + "</div>"
            )
        else:
            body = _markdown_to_html(content)
            parts.append(
                '<div class="msg-coach">'
                '<span class="msg-label">Coach:</span>\n' + body + "</div>"
            )
    parts.append("</body>")
    return "\n".join(parts)


def _parse_score_from_reply(reply: str) -> int | None:
    """Extract score N from reply (e.g. 'Score: 4/5' or 'Score: 7/10'). Returns N or None."""
    m = re.search(r"Score:\s*(\d+)\s*/\s*\d+", reply, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*/\s*(?:5|10)", reply)
    if m:
        return int(m.group(1))
    return None


def run_gui() -> None:
    root = tk.Tk()
    root.title("Grade 6 Maths Olympiad Coach")
    root.geometry("800x600")
    root.minsize(500, 400)

    history: list[dict] = []
    display_messages: list[dict] = []
    update_queue: queue.Queue[str | None] = queue.Queue()
    loaded_pdf_text: str | None = None
    loaded_pdf_name: str | None = None
    loaded_reference_path: str | None = None  # path used for persistence (PDF path or directory path)
    memory_notes: str = ""  # persistent notes about the student (saved to disk)
    # Book-as-resource: concepts from PDF, quiz state
    concepts_list: list[tuple[str, str]] = []  # (chapter_label, concept)
    concept_status: dict[str, str] = {}  # concept -> "fully understood" | "not fully understood"
    mode: str = "chat"  # "chat" | "quiz"
    quiz_phase: str = "explain"  # explain | q1..q10 | summary | done
    quiz_concept: str = ""
    quiz_correct_results: list[bool] = []  # track correct/incorrect per question
    concepts_analyze_queue: queue.Queue[str | None] = queue.Queue()
    start_quiz_reply_queue: queue.Queue[str | None] = queue.Queue()
    concept_cache: dict = {}  # path -> { "signature": str, "concepts": [[ch, cpt], ...] }

    # Restore persistent memory (concept_status, memory_notes, concept_cache)
    try:
        persisted = load_persistent_memory()
        concept_status.update(persisted.get("concept_status", {}))
        memory_notes = persisted.get("memory_notes", "") or ""
        concept_cache.update(persisted.get("concept_cache", {}))
    except Exception:
        pass

    base_font = tkfont.nametofont("TkDefaultFont")
    base_font.configure(size=11)
    bold_font = tkfont.Font(family=base_font.cget("family"), size=11, weight="bold")

    def set_log_html(html: str) -> None:
        if hasattr(log, "set_html"):
            log.set_html(html)
        elif hasattr(log, "html"):
            log.html = html
            if hasattr(log, "update"):
                log.update()
        else:
            log.delete("1.0", tk.END)
            log.insert("1.0", html)

    if HAS_HTML:
        log = HTMLScrolledText(root, html="", padx=10, pady=10, font=base_font)
        log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        display_messages.append({
            "role": "assistant",
            "content": "Ask a question or paste a problem below. Press Enter to send, Shift+Enter for new line.",
        })
        set_log_html(_build_chat_html(display_messages))
    else:
        log = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=base_font,
            padx=10,
            pady=10,
            bg="#fafafa",
            fg="black",
            relief=tk.FLAT,
            takefocus=False,
        )
        log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        log.tag_configure("you_label", font=bold_font, foreground="black")
        log.tag_configure("you", foreground="black")
        log.tag_configure("coach_label", font=bold_font, foreground="black")
        log.tag_configure("thinking", foreground="black")
        log.config(state=tk.NORMAL)
        log.insert(tk.END, "Grade 6 Maths Olympiad Coach\n", "coach_label")
        log.insert(
            tk.END,
            "Ask a question or paste a problem below. Press Enter to send, Shift+Enter for new line.\n\n",
            "you",
        )
        log.config(state=tk.DISABLED)

    def load_pdf() -> None:
        nonlocal loaded_pdf_text, loaded_pdf_name, loaded_reference_path
        path = filedialog.askopenfilename(
            title="Select a PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            loaded_pdf_text = extract_pdf_text(path)
            loaded_pdf_name = path.split("/")[-1].split("\\")[-1]
            loaded_reference_path = path
            update_pdf_status()
            _append_coach_msg(
                f"**Loaded PDF:** {loaded_pdf_name} ({len(loaded_pdf_text):,} characters). Analyzing for concepts…"
            )
            _save_memory()
            root.after(50, _start_analyze)
        except Exception as e:
            _append_coach_msg(f"Could not load PDF: {e}")

    def load_dir() -> None:
        nonlocal loaded_pdf_text, loaded_pdf_name, loaded_reference_path
        path = filedialog.askdirectory(title="Select directory of PDFs/text files")
        if not path:
            return
        try:
            loaded_pdf_text, loaded_pdf_name = load_directory(path)
            loaded_reference_path = path
            if not loaded_pdf_text:
                _append_coach_msg("No PDF or .txt files found in that directory.")
                loaded_pdf_text = None
                loaded_pdf_name = None
                loaded_reference_path = None
            else:
                update_pdf_status()
                _append_coach_msg(
                    f"**Loaded directory:** {loaded_pdf_name} ({len(loaded_pdf_text):,} characters). Analyzing for concepts…"
                )
                _save_memory()
                root.after(50, _start_analyze)
        except Exception as e:
            _append_coach_msg(f"Could not load directory: {e}")

    def _append_coach_msg(msg: str) -> None:
        display_messages.append({"role": "assistant", "content": msg})
        if HAS_HTML:
            set_log_html(_build_chat_html(display_messages))
            log.see(tk.END)
        else:
            log.config(state=tk.NORMAL)
            log.insert(tk.END, "Coach: ", "coach_label")
            log.insert(tk.END, msg + "\n\n", "coach")
            log.config(state=tk.DISABLED)
            log.see(tk.END)
        entry.focus_set()

    def _build_memory_notes_for_agent() -> str:
        """Build the string passed to the agent as persistent memory (notes + concept mastery)."""
        parts = []
        if memory_notes_widget:
            notes = memory_notes_widget.get("1.0", tk.END).strip()
            if notes:
                parts.append(notes)
        if concept_status:
            parts.append("Concept mastery:")
            for cpt, status in concept_status.items():
                parts.append(f"  - {cpt}: {status}")
        return "\n\n".join(parts) if parts else ""

    def _save_memory() -> None:
        try:
            notes = memory_notes_widget.get("1.0", tk.END).strip() if memory_notes_widget else ""
            save_persistent_memory(
                concept_status=concept_status,
                reference_path=loaded_reference_path,
                memory_notes=notes,
                concept_cache=concept_cache,
            )
        except Exception:
            pass

    input_frame = tk.Frame(root, padx=8, pady=8, bg="#e5e7eb", relief=tk.GROOVE, borderwidth=1)
    input_frame.pack(fill=tk.X)
    btn_frame = tk.Frame(input_frame, bg="#e5e7eb")
    btn_frame.pack(anchor=tk.W, pady=(0, 4))
    load_pdf_btn = tk.Button(
        btn_frame,
        text="Load PDF",
        command=load_pdf,
        font=base_font,
        bg="#059669",
        fg="black",
        activebackground="#047857",
        activeforeground="black",
        relief=tk.FLAT,
        padx=12,
        pady=4,
        cursor="hand2",
    )
    load_pdf_btn.pack(side=tk.LEFT)
    load_dir_btn = tk.Button(
        btn_frame,
        text="Load directory",
        command=load_dir,
        font=base_font,
        bg="#0d9488",
        fg="black",
        activebackground="#0f766e",
        activeforeground="black",
        relief=tk.FLAT,
        padx=12,
        pady=4,
        cursor="hand2",
    )
    load_dir_btn.pack(side=tk.LEFT, padx=(4, 0))
    pdf_status = tk.Label(btn_frame, text="No source loaded", font=base_font, fg="#6b7280", bg="#e5e7eb")
    pdf_status.pack(side=tk.LEFT, padx=(8, 0))

    def update_pdf_status() -> None:
        if loaded_pdf_name:
            pdf_status.config(text=f"Source: {loaded_pdf_name}", fg="black")
        else:
            pdf_status.config(text="No source loaded", fg="#6b7280")

    # Concepts panel (book as resource): Analyze book, list concepts, Start quiz
    concepts_frame = tk.LabelFrame(input_frame, text="Book concepts (analyze PDF first)", font=base_font, fg="black", bg="#e5e7eb")
    concepts_inner = tk.Frame(concepts_frame, bg="#e5e7eb")
    concepts_inner.pack(fill=tk.X, pady=(0, 4))
    concepts_hint = tk.Label(concepts_frame, text="Click a concept to select it, then click Start quiz.", font=base_font, fg="#6b7280", bg="#e5e7eb")
    concepts_hint.pack(anchor=tk.W)
    analyze_btn = tk.Button(
        concepts_inner,
        text="Analyze book",
        command=lambda: _start_analyze(),
        font=base_font,
        bg="#7c3aed",
        fg="black",
        relief=tk.FLAT,
        padx=10,
        pady=4,
        cursor="hand2",
    )
    analyze_btn.pack(side=tk.LEFT)
    reanalyze_btn = tk.Button(
        concepts_inner,
        text="Re-analyze book",
        command=lambda: _start_analyze(force_reanalyze=True),
        font=base_font,
        bg="#6b7280",
        fg="white",
        relief=tk.FLAT,
        padx=10,
        pady=4,
        cursor="hand2",
    )
    reanalyze_btn.pack(side=tk.LEFT)
    concepts_listbox = tk.Listbox(
        concepts_inner,
        height=6,
        font=base_font,
        selectmode=tk.SINGLE,
        exportselection=False,
        selectbackground="#2563eb",
        selectforeground="white",
        highlightthickness=1,
        highlightcolor="#2563eb",
    )
    concepts_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
    start_quiz_btn = tk.Button(
        concepts_inner,
        text="Start quiz (5 Q, pass ≥60%)",
        command=lambda: _start_quiz(),
        font=base_font,
        bg="#dc2626",
        fg="black",
        relief=tk.FLAT,
        padx=10,
        pady=4,
        cursor="hand2",
    )
    start_quiz_btn.pack(side=tk.LEFT)
    quiz_status_label = tk.Label(concepts_frame, text="", font=base_font, fg="#374151", bg="#e5e7eb")
    quiz_status_label.pack(anchor=tk.W)
    ready_btn = tk.Button(
        concepts_frame,
        text="Ready",
        command=lambda: do_send("READY"),
        font=base_font,
        bg="#16a34a",
        fg="white",
        relief=tk.FLAT,
        padx=12,
        pady=6,
        cursor="hand2",
    )
    # Ready button shown only when in quiz explain phase (see update_ready_button)
    ready_btn.pack_forget()

    def update_ready_button() -> None:
        if mode == "quiz" and quiz_phase == "explain":
            ready_btn.pack(anchor=tk.W, pady=(4, 0))
        else:
            ready_btn.pack_forget()

    def update_quiz_status_label() -> None:
        if mode != "quiz" or not quiz_concept:
            return
        if quiz_phase == "explain":
            quiz_status_label.config(text=f"Quiz: {quiz_concept} | Read above, then click Ready")
        elif quiz_phase == "done":
            status = concept_status.get(quiz_concept, "")
            correct = sum(quiz_correct_results)
            quiz_status_label.config(text=f"Concept: {quiz_concept} | Done. {status} · {correct}/{NUM_QUIZ_QUESTIONS} correct")
        elif quiz_phase.startswith("q") and quiz_phase[1:].isdigit():
            n = int(quiz_phase[1:])
            correct = sum(quiz_correct_results)
            total = len(quiz_correct_results)
            suffix = f" · {correct} correct so far" if total else ""
            quiz_status_label.config(text=f"Question {n} of {NUM_QUIZ_QUESTIONS}{suffix}")
        # else leave label as-is (e.g. "Explaining…")

    concepts_frame.pack(fill=tk.X, pady=(0, 6))

    # Persistent memory: notes about the student (saved across sessions)
    memory_frame = tk.LabelFrame(input_frame, text="Persistent memory (saved across sessions)", font=base_font, fg="black", bg="#e5e7eb")
    memory_inner = tk.Frame(memory_frame, bg="#e5e7eb")
    memory_inner.pack(fill=tk.X, pady=(0, 4))
    memory_notes_widget = scrolledtext.ScrolledText(memory_inner, height=3, font=base_font, wrap=tk.WORD, bg="white", fg="black")
    memory_notes_widget.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    memory_notes_widget.insert("1.0", memory_notes)
    def on_save_memory() -> None:
        _save_memory()
        _append_coach_msg("Memory saved. The coach will use it in future replies.")
    save_memory_btn = tk.Button(
        memory_inner,
        text="Save memory",
        command=on_save_memory,
        font=base_font,
        bg="#6b7280",
        fg="black",
        relief=tk.FLAT,
        padx=10,
        pady=4,
        cursor="hand2",
    )
    save_memory_btn.pack(side=tk.LEFT)
    memory_frame.pack(fill=tk.X, pady=(0, 6))

    def _load_books_folder() -> None:
        """Load PDFs from the default books folder on startup, if it exists."""
        nonlocal loaded_pdf_text, loaded_pdf_name, loaded_reference_path
        books_path = get_books_dir_path()
        if not Path(books_path).is_dir():
            return
        try:
            loaded_pdf_text, loaded_pdf_name = load_directory(books_path)
            if loaded_pdf_text:
                loaded_reference_path = books_path
                update_pdf_status()
                _append_coach_msg(
                    f"**Loaded from books folder:** {loaded_pdf_name} ({len(loaded_pdf_text):,} characters). Analyzing for concepts…"
                )
                _save_memory()
                root.after(50, _start_analyze)
        except Exception:
            pass

    def _start_analyze(force_reanalyze: bool = False) -> None:
        nonlocal concepts_list, concept_cache
        if not loaded_pdf_text:
            display_messages.append({"role": "assistant", "content": "Load a PDF first, then click **Analyze book**."})
            if HAS_HTML:
                set_log_html(_build_chat_html(display_messages))
            else:
                log.config(state=tk.NORMAL)
                log.insert(tk.END, "Coach: Load a PDF first.\n\n", "coach_label")
                log.config(state=tk.DISABLED)
            log.see(tk.END)
            return
        cache_key = str(Path(loaded_reference_path).resolve()) if loaded_reference_path else None
        if cache_key and not force_reanalyze:
            try:
                sig = get_source_signature(loaded_reference_path)
                cached = concept_cache.get(cache_key)
                if cached and cached.get("signature") == sig and cached.get("concepts"):
                    concepts_list = [tuple(c) for c in cached["concepts"]]
                    concepts_listbox.delete(0, tk.END)
                    for ch, concept in concepts_list:
                        concepts_listbox.insert(tk.END, f"{ch} → {concept}")
                    display_messages.append({"role": "assistant", "content": f"**Found {len(concepts_list)} concepts (from cache).** Select one and click **Start quiz**."})
                    if HAS_HTML:
                        set_log_html(_build_chat_html(display_messages))
                    else:
                        log.config(state=tk.NORMAL)
                        log.insert(tk.END, "Coach: " + (display_messages[-1]["content"] if display_messages else "") + "\n\n", "coach_label")
                        log.config(state=tk.DISABLED)
                    log.see(tk.END)
                    entry.focus_set()
                    return
            except Exception:
                pass
        analyze_btn.config(state=tk.DISABLED, text="Re-analyzing…" if force_reanalyze else "Analyzing…")
        reanalyze_btn.config(state=tk.DISABLED)
        def worker() -> None:
            try:
                raw = get_concepts_from_book(loaded_pdf_text)
                concepts_analyze_queue.put(raw)
            except Exception as e:
                concepts_analyze_queue.put(f"ERROR:{e}")
        threading.Thread(target=worker, daemon=True).start()
        def poll_analyze() -> None:
            nonlocal concept_cache
            try:
                raw = concepts_analyze_queue.get_nowait()
            except queue.Empty:
                root.after(200, poll_analyze)
                return
            analyze_btn.config(state=tk.NORMAL, text="Analyze book")
            reanalyze_btn.config(state=tk.NORMAL)
            if isinstance(raw, str) and raw.startswith("ERROR:"):
                display_messages.append({"role": "assistant", "content": f"Analysis failed: {raw[6:]}"})
            else:
                concepts_list = parse_concepts_from_response(raw)
                concepts_listbox.delete(0, tk.END)
                for ch, concept in concepts_list:
                    concepts_listbox.insert(tk.END, f"{ch} → {concept}")
                if cache_key:
                    concept_cache[cache_key] = {
                        "signature": get_source_signature(loaded_reference_path),
                        "concepts": [[ch, cpt] for ch, cpt in concepts_list],
                    }
                    _save_memory()
                display_messages.append({"role": "assistant", "content": f"**Found {len(concepts_list)} concepts.** Select one and click **Start quiz**."})
            if HAS_HTML:
                set_log_html(_build_chat_html(display_messages))
            else:
                log.config(state=tk.NORMAL)
                log.insert(tk.END, "Coach: " + (display_messages[-1]["content"] if display_messages else "") + "\n\n", "coach_label")
                log.config(state=tk.DISABLED)
            log.see(tk.END)
            entry.focus_set()
        root.after(200, poll_analyze)

    def _start_quiz() -> None:
        nonlocal mode, quiz_phase, quiz_concept, history, display_messages
        sel = concepts_listbox.curselection()
        if not sel or not concepts_list:
            return
        idx = int(sel[0])
        chapter, concept = concepts_list[idx]
        quiz_concept = f"{chapter} → {concept}"
        mode = "quiz"
        quiz_phase = "explain"
        quiz_correct_results.clear()
        quiz_status_label.config(text=f"Quiz: {quiz_concept} | Explaining…")
        history.append({"role": "user", "content": f"Start quiz for concept: {concept}"})
        display_messages.append({"role": "user", "content": f"Start quiz for: {quiz_concept}"})
        phase_inst = (
            f"The exact concept to explain is: {quiz_concept}. "
            f"You must explain ONLY this concept—do not explain a different topic. "
            f"The heading at the start of your reply must use this exact concept name (e.g. '{concept}' or '{quiz_concept}'). "
            "Explain from the book using the standard structure (concept name, definition, 3–5 examples with described diagrams, real-world example if any, Important things to remember). "
            + EXPLAIN_CONCEPT_END
        )
        if HAS_HTML:
            set_log_html(_build_chat_html(display_messages))
            log.see(tk.END)
        else:
            log.config(state=tk.NORMAL)
            log.insert(tk.END, "You: Start quiz for " + quiz_concept + "\n\n", "you_label")
            log.insert(tk.END, "Coach: ", "coach_label")
            reply_insert_pos = log.index(tk.END)
            log.config(state=tk.DISABLED)
            log.see(tk.END)
        def worker() -> None:
            try:
                reply = get_reply(
                    history,
                    pdf_text=loaded_pdf_text,
                    phase_instruction=phase_inst,
                    memory_notes=_build_memory_notes_for_agent() or None,
                )
                history.append({"role": "assistant", "content": reply})
                start_quiz_reply_queue.put(reply)
            except Exception as e:
                start_quiz_reply_queue.put(f"[Error: {e}]")
        threading.Thread(target=worker, daemon=True).start()
        def poll() -> None:
            try:
                payload = start_quiz_reply_queue.get_nowait()
            except queue.Empty:
                root.after(100, poll)
                return
            if HAS_HTML:
                display_messages.append({"role": "assistant", "content": payload})
                set_log_html(_build_chat_html(display_messages))
            else:
                log.config(state=tk.NORMAL)
                log.insert(reply_insert_pos, _latex_to_plain(payload) + "\n\n")
                log.config(state=tk.DISABLED)
            log.see(tk.END)
            quiz_status_label.config(text=f"Quiz: {quiz_concept} | Read above, then click Ready")
            update_ready_button()
            entry.focus_set()
        root.after(100, poll)

    input_label = tk.Label(
        input_frame,
        text="Type your question below, then press Enter or click Send:",
        font=base_font,
        fg="black",
        bg="#e5e7eb",
    )
    input_label.pack(anchor=tk.W, pady=(4, 2))

    entry = tk.Text(
        input_frame,
        height=3,
        wrap=tk.WORD,
        font=base_font,
        relief=tk.SOLID,
        borderwidth=1,
        padx=8,
        pady=6,
        insertbackground="black",
        bg="white",
        fg="black",
    )
    entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8), pady=(0, 6))
    entry.focus_set()

    # Quiz multiple-choice: frame shown when coach sends a question with A) B) C) D)
    quiz_choice_frame = tk.Frame(input_frame, bg="#e5e7eb")
    quiz_choice_vars: list[tk.Variable] = []

    def hide_quiz_choices() -> None:
        for w in quiz_choice_frame.winfo_children():
            w.destroy()
        quiz_choice_frame.pack_forget()
        quiz_choice_vars.clear()

    def show_quiz_choices(options: list[tuple[str, str]], is_multiple: bool, question_text: str | None = None) -> None:
        for w in quiz_choice_frame.winfo_children():
            w.destroy()
        quiz_choice_vars.clear()
        label_text = "Multiple choice – select all that apply:" if is_multiple else "Multiple choice – choose one:"
        tk.Label(quiz_choice_frame, text=label_text, font=bold_font, fg="#14532d", bg="#e5e7eb").pack(anchor=tk.W)
        if question_text and question_text.strip():
            q_frame = tk.Frame(quiz_choice_frame, bg="#dcfce7", relief=tk.GROOVE, borderwidth=1, padx=8, pady=6)
            q_frame.pack(anchor=tk.W, fill=tk.X, pady=(6, 8))
            tk.Label(
                q_frame, text=question_text.strip(), font=base_font, fg="black", bg="#dcfce7",
                wraplength=580, justify=tk.LEFT, anchor=tk.W,
            ).pack(anchor=tk.W)
        inner = tk.Frame(quiz_choice_frame, bg="#e5e7eb")
        inner.pack(anchor=tk.W, pady=(4, 4))
        if is_multiple:
            vars_list = [tk.BooleanVar(value=False) for _ in options]
            quiz_choice_vars.extend(vars_list)
            for (letter, opt_text), var in zip(options, vars_list):
                cb = tk.Checkbutton(
                    inner, text=f"{letter}) {opt_text}",
                    variable=var, font=base_font, fg="black", bg="#e5e7eb", activebackground="#e5e7eb",
                    anchor=tk.W, wraplength=560,
                )
                cb.pack(anchor=tk.W)
        else:
            sv = tk.StringVar(value="")
            quiz_choice_vars.append(sv)
            for letter, opt_text in options:
                rb = tk.Radiobutton(
                    inner, text=f"{letter}) {opt_text}",
                    variable=sv, value=letter, font=base_font, fg="black", bg="#e5e7eb", activebackground="#e5e7eb",
                    anchor=tk.W, wraplength=560,
                )
                rb.pack(anchor=tk.W)

        def submit_quiz_answer() -> None:
            if is_multiple and quiz_choice_vars:
                selected = [options[i][0] for i, v in enumerate(quiz_choice_vars) if getattr(v, "get", lambda: False)()]
                answer = "My answer: " + " and ".join(selected) if selected else None
            else:
                answer = (quiz_choice_vars[0].get() if quiz_choice_vars else "").strip()
                answer = "My answer: " + answer if answer else None
            if answer:
                hide_quiz_choices()
                do_send(answer)
            entry.focus_set()

        tk.Button(
            quiz_choice_frame, text="Submit answer", command=submit_quiz_answer,
            font=base_font, bg="#2563eb", fg="black", relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
        ).pack(anchor=tk.W)
        quiz_choice_frame.pack(anchor=tk.W, fill=tk.X, pady=(8, 0))

    def do_send(text: str) -> None:
        nonlocal quiz_phase, concept_status
        if not text or not text.strip():
            return
        if quiz_choice_frame.winfo_ismapped():
            hide_quiz_choices()

        # Quiz mode: compute phase instruction and next phase
        phase_instruction = None
        if mode == "quiz" and quiz_concept:
            if quiz_phase == "explain":
                if "ready" in text.lower():
                    phase_instruction = (
                        f"The student is ready. Ask question 1 of {NUM_QUIZ_QUESTIONS} about the concept '{concept}' only. "
                        f"The concept is: {quiz_concept}. {QUIZ_ABOUT_CONCEPT} "
                        "Do NOT ask about 'Object', tangibility, or any other topic—only about this concept. One question only. "
                        + QUIZ_MC_INSTRUCTION
                    )
                    quiz_phase = "q1"
            elif quiz_phase.startswith("q") and quiz_phase[1:].isdigit():
                n = int(quiz_phase[1:])
                answer_part = text
                if "my answer:" in text.lower():
                    answer_part = text.split(":", 1)[-1].strip() or text
                if n < NUM_QUIZ_QUESTIONS:
                    phase_instruction = (
                        f"The student's answer for this question was: {answer_part}. "
                        f"Evaluate and confirm: say 'Correct!' or 'Incorrect. The correct answer is X.' Then ask question {n + 1} of {NUM_QUIZ_QUESTIONS} about the concept '{quiz_concept}' only. "
                        f"The concept is: {quiz_concept}. {QUIZ_ABOUT_CONCEPT} One question only. "
                        + QUIZ_CONFIRM_INSTRUCTION + " " + QUIZ_MC_INSTRUCTION
                    )
                    quiz_phase = f"q{n + 1}"
                else:
                    phase_instruction = (
                        f"The student's answer for this question was: {answer_part}. "
                        "Evaluate and confirm: say 'Correct!' or 'Incorrect. The correct answer is X.' Then give a short summary. "
                        f"The summary must refer ONLY to the concept that was quizzed ({quiz_concept}). Do not mention other topics (e.g. polygons, geometry). "
                        f"You must include a line: Score: N/{NUM_QUIZ_QUESTIONS} (M%). Base the score only on the 5 answers in this quiz. "
                        f"If M >= {QUIZ_PASS_PERCENT} say 'Concept marked as fully understood.' Otherwise say 'Concept not yet fully understood.' "
                        + QUIZ_CONFIRM_INSTRUCTION
                    )
                    quiz_phase = "summary"
        else:
            # Standalone "Explain this concept from the book" (chat mode): require Important things to remember
            if text.strip().startswith("Explain this concept from the book"):
                phase_instruction = EXPLAIN_FROM_BOOK_INSTRUCTION
        update_ready_button()
        update_quiz_status_label()

        history.append({"role": "user", "content": text})
        display_messages.append({"role": "user", "content": text})

        if HAS_HTML:
            set_log_html(_build_chat_html(display_messages))
            log.see(tk.END)
        else:
            log.config(state=tk.NORMAL)
            log.insert(tk.END, "You: ", "you_label")
            log.insert(tk.END, text + "\n\n", "you")
            log.insert(tk.END, "Coach: ", "coach_label")
            reply_insert_pos = log.index(tk.END)
            log.config(state=tk.DISABLED)
            log.see(tk.END)

        def worker() -> None:
            nonlocal quiz_phase, concept_status
            try:
                reply = get_reply(
                    history,
                    model="gpt-5.2",
                    pdf_text=loaded_pdf_text,
                    phase_instruction=phase_instruction,
                    memory_notes=_build_memory_notes_for_agent() or None,
                )
                history.append({"role": "assistant", "content": reply})
                # If we just finished the last question, parse score and set concept status
                if mode == "quiz" and quiz_phase == "summary":
                    score = _parse_score_from_reply(reply)
                    if score is not None:
                        pass_threshold = (NUM_QUIZ_QUESTIONS * QUIZ_PASS_PERCENT + 99) // 100
                        if score >= pass_threshold:
                            concept_status[quiz_concept] = "fully understood"
                        else:
                            concept_status[quiz_concept] = "not fully understood"
                    quiz_phase = "done"
                update_queue.put(reply)
            except Exception as e:
                update_queue.put(f"[Error: {e}]")

        threading.Thread(target=worker, daemon=True).start()

        def poll_queue() -> None:
            try:
                reply = update_queue.get_nowait()
            except queue.Empty:
                root.after(100, poll_queue)
                return
            if HAS_HTML:
                display_messages.append({"role": "assistant", "content": reply})
                set_log_html(_build_chat_html(display_messages))
                log.see(tk.END)
            else:
                log.config(state=tk.NORMAL)
                plain = _latex_to_plain(reply.strip())
                log.insert(reply_insert_pos, plain + "\n\n")
                log.config(state=tk.DISABLED)
                log.see(tk.END)
            if mode == "quiz" and quiz_phase in ("q2", "q3", "q4", "q5", "summary"):
                correct = parse_last_answer_correct(reply)
                if correct is not None:
                    quiz_correct_results.append(correct)
                update_quiz_status_label()
            if mode == "quiz" and quiz_phase not in ("explain", "summary", "done"):
                parsed = parse_quiz_options(reply)
                if parsed:
                    question_text = parse_quiz_question_text(reply)
                    show_quiz_choices(parsed[0], parsed[1], question_text)
            if mode == "quiz" and quiz_phase == "done":
                update_quiz_status_label()
                # Update listbox to show status for this concept
                for i, (ch, cpt) in enumerate(concepts_list):
                    if f"{ch} → {cpt}" == quiz_concept:
                        suffix = " ✓ fully understood" if status == "fully understood" else " (not yet)"
                        concepts_listbox.delete(i)
                        concepts_listbox.insert(i, quiz_concept + suffix)
                        break
                _save_memory()
            entry.focus_set()

        root.after(100, poll_queue)

    def send() -> None:
        text = entry.get("1.0", tk.END).strip()
        if text:
            entry.delete("1.0", tk.END)
            entry.focus_set()
            do_send(text)

    def on_enter(event: tk.Event) -> None:
        if event.state & 0x1:
            return
        send()
        return "break"

    entry.bind("<Return>", on_enter)

    btn = tk.Button(
        input_frame,
        text="Send",
        command=send,
        font=bold_font,
        bg="#2563eb",
        fg="black",
        activebackground="#1d4ed8",
        activeforeground="black",
        relief=tk.FLAT,
        padx=16,
        pady=6,
        cursor="hand2",
    )
    btn.pack(side=tk.RIGHT)

    root.after(50, entry.focus_set)
    root.after(100, _load_books_folder)  # Load from books folder if present
    root.mainloop()


if __name__ == "__main__":
    run_gui()
