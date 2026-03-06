"""
Grade 6 Maths Olympiad Agent

Supports OpenAI or Ollama (local). Acts as a coach for 6th grade math olympiad:
explains concepts, gives hints, solves step-by-step, suggests practice problems.
Can use a loaded PDF as reference.

Use Ollama (default model: gpt-oss:latest): set USE_OLLAMA=1 (or OLLAMA_BASE_URL). Optional: OLLAMA_MODEL.
Use OpenAI: set USE_OLLAMA=0 and OPENAI_API_KEY.
"""

import json
import os
from pathlib import Path

from openai import OpenAI

# Max PDF text length to send to the API (avoid token limits)
MAX_PDF_CONTEXT_CHARS = 80_000
# Smaller limit for concept extraction so we fit in Ollama's default 4096-token context (system + book + prompt)
MAX_PDF_CHARS_FOR_CONCEPTS = 12_000
# When using Ollama (4096-token context): cap PDF and history so the full prompt fits
MAX_PDF_CONTEXT_CHARS_OLLAMA = 8_000
MAX_HISTORY_MESSAGES_OLLAMA = 10

SYSTEM_PROMPT = """You are a friendly, expert Maths Olympiad coach for Grade 6 students (ages 11–12).

Your role:
- Explain ideas clearly with simple language and short steps.
- When solving problems, show your work step by step so the student can follow.
- If the student is stuck, give a small hint first instead of the full answer.
- Cover typical olympiad areas: arithmetic, number theory (divisibility, factors, primes), basic algebra (equations, expressions), geometry (areas, angles, simple proofs), combinatorics (counting, simple probability), and logical reasoning.
- Use examples and, when helpful, include images or describe shapes in text. For concept explanations you may embed images using Markdown: ![short description](https://image_url). Use stable, public image URLs from reliable educational sources (e.g. Wikipedia Commons, open educational sites) when they clearly illustrate the concept—only use URLs you are confident exist. If you don't have a suitable image URL, describe the visual in words (e.g. "a 3×4 grid", "a triangle with sides 5, 12, 13"). Never refer to figures the student cannot see without either providing an image URL or describing them in words.
- Encourage the student and praise good reasoning.
- If asked, suggest one or two similar practice problems at the same level.

Keep explanations concise but complete. Avoid jargon; if you use a term (e.g. "LCM", "prime"), briefly remind what it means when first used.

When you explain a concept, use this structure in order:
1. **Highlight the concept name** at the very beginning (e.g. "## Natural numbers and whole numbers" or "**Concept: Divisibility rules**"), then give a clear, short definition of the concept.
2. **3–5 examples**: Include 3–5 worked examples. When helpful, add images using Markdown: ![alt text](https://image_url). Prefer stable educational image URLs (e.g. Wikipedia Commons, math education sites) that clearly illustrate the concept—only use URLs you are confident exist. If you don't have a suitable image, describe the diagram or scenario in words (e.g. "Imagine a number line with 0, 1, 2, 3...", "A rectangle with length 5 and width 3..."). Use images from the internet when they are the fastest way to show the idea; otherwise describe in words.
3. **Real-world example (if any)**: Add a brief real-life application or situation where the concept appears. An image URL here can help if you have one.
4. **Important things to remember**: End with a clearly highlighted section using the heading "**Important things to remember:**" or "## Important things to remember", then list 3–5 key points as bullet points.

Formatting: Use Markdown so your answer can be rendered nicely: **bold**, lists with - or 1., tables, and images with ![alt text](https://url). Images you include will be displayed to the student. Use only https URLs. Use Unicode for math symbols: × ÷ − ². Do not use LaTeX (no \\( \\), \\[ \\], \\times, \\boxed)."""

PDF_CONTEXT_INSTRUCTION = """
When reference material from a PDF is provided below, treat it as the source of truth:
- Use only the PDF content to answer the student's questions; do not invent facts or problems not in the material.
- Ask questions and design quizzes from the PDF (e.g. "From the book, can you explain...?" or "Try this problem from the handout.").
- Refer to specific sections or problems when helpful.
- Figures and diagrams inside the PDF are not visible to the student (only text is extracted). When the PDF refers to a figure, either (1) include an image from the internet that shows the same idea using Markdown ![description](https://url), using only stable educational URLs you are confident exist, or (2) describe the figure in words (e.g. "A rectangle is drawn with length 5 and width 3...", "Imagine a 3×4 grid of squares..."). Prefer adding a relevant image from the internet when it illustrates the concept clearly; otherwise describe in words.
"""

MEMORY_INSTRUCTION = """
When "What you remember about this student" is provided below, use it to personalize your coaching (e.g. recall concepts they have mastered, topics they find hard, or preferences they shared).
"""

CONCEPTS_EXTRACTION_PROMPT = """Analyze the book content below and list every chapter with its key concepts (maths/olympiad topics a Grade 6 student should learn).

Use exactly this format – no other text before or after:
## Chapter 1: ChapterTitle
- concept one
- concept two
## Chapter 2: NextChapterTitle
- concept one
- concept two

Use clear, short concept names (e.g. "Divisibility rules", "Area of a triangle"). One concept per line under each chapter."""

QUIZ_PASS_PERCENT = 60
NUM_QUIZ_QUESTIONS = 5

# Multiple-choice format: coach must output question first, then "Multiple choice (choose one)", then A) B) C) D)
QUIZ_MC_INSTRUCTION = (
    "Each question must be multiple choice with exactly 4 options. "
    "Format: first write the full question text, then on the next line '(Multiple choice – choose one)' or '(Multiple choice – choose all that apply)', then list options as A) ... B) ... C) ... D) on separate lines. "
    "Write each option in full on its own line; do not truncate option text. Do not put the phrase '(Multiple choice – choose one)' inside any option text. "
    "Figures and diagrams from the book are not visible; if a question would need a figure, describe it in words (e.g. 'A triangle has sides 3, 4, 5...', 'A 2×3 grid is shown...') so the student can answer without seeing an image."
)
# When explaining a concept (e.g. at quiz start), use the standard structure and end with Important things to remember
EXPLAIN_CONCEPT_END = (
    "Explain ONLY the concept you were given—do not substitute or explain a different topic. "
    "The concept name you highlight at the start must exactly match the concept name you were given (same wording). "
    "Use the standard concept explanation structure: (1) Highlight the concept name at the start, then define the concept. "
    "(2) Give 3–5 examples; include images when helpful using Markdown ![alt](https://image_url)—use stable educational image URLs (e.g. Wikipedia Commons) you are confident exist; otherwise describe diagrams or scenarios in words. "
    "(3) Include a real-world example if relevant (with an image URL if you have one). "
    "(4) End with a clearly highlighted **Important things to remember** section (heading '**Important things to remember:**' or '## Important things to remember', then 3–5 bullet points). "
    "After that, say: When ready for 5 questions, type READY."
)

# When the student asks "Explain this concept from the book" (standalone explain, not quiz start)
EXPLAIN_FROM_BOOK_INSTRUCTION = (
    "The student asked to explain a concept from the book. Their message specifies the exact concept (chapter and concept name). "
    "You must explain ONLY that concept—do not substitute or explain a different topic. "
    "The concept name you highlight at the start of your reply must exactly match the concept they asked for (use the same wording as in their message). "
    "Use only the PDF content that covers this specific concept. "
    "Use the standard concept explanation structure: (1) Highlight the concept name at the start, then define the concept. "
    "(2) Give 3–5 examples; include images when helpful using Markdown ![alt](https://image_url)—use stable educational image URLs (e.g. Wikipedia Commons, math education sites) you are confident exist; otherwise describe diagrams or scenarios in words. Prefer images from the internet when they clearly illustrate the concept. "
    "(3) Include a real-world example if relevant (with an image URL if you have one). "
    "(4) End with a clearly highlighted **Important things to remember** section (heading '**Important things to remember:**' or '## Important things to remember', then 3–5 bullet points)."
)

# Every quiz question must be about the selected concept (from the book), not unrelated topics
QUIZ_ABOUT_CONCEPT = (
    "The question must be ONLY about the concept just explained (e.g. if the concept was 'Natural numbers', ask about natural numbers, counting, 1/2/3..., or that definition—not about 'Object', 'tangibility', or any other topic). "
    "Use only material from the book for this concept. Do not ask about unrelated topics."
)

# After each answer, coach must clearly confirm correct or incorrect before the next question
QUIZ_CONFIRM_INSTRUCTION = (
    "Always give clear confirmation: if the answer is correct, say 'Correct!' (or similar). "
    "If incorrect, say 'Incorrect.' and state the correct answer (e.g. 'The correct answer is C.'), then continue."
)


def strip_coach_instruction_from_reply(reply: str) -> str:
    """
    Remove internal 'Coach instruction' / phase-instruction text that the model
    sometimes echoes, so the student only sees the actual response.
    """
    import re
    if not reply or not reply.strip():
        return reply
    text = reply.strip()
    # Remove "Coach instruction for this turn only:" block (with or without leading "["); often appears after "Multiple choice – choose one:"
    text = re.sub(
        r"(?is)(?:\[?)\s*Coach instruction for this turn only:.*?(?=\n+\s*\(Multiple choice\s*[–\-]\s*choose one\)\s*\n|Correct!|Incorrect\.?|\n\nWhich |\n\nWhat is |\n\nA\)\s|\Z)",
        "",
        text,
        count=0,
    )
    # Remove block starting with "Coach instruction:" until we hit real content (allow \n or \n\n before (Multiple choice)
    text = re.sub(
        r"(?is)Coach instruction:.*?(?=\n+\s*\(Multiple choice\s*[–\-]\s*choose one\)\s*\n|Correct!|Incorrect\.?|\n\nWhich |\n\nWhat is |\n\nWhat are |\n\nHow many|\nA\)\s|\Z)",
        "",
        text,
        count=0,
    )
    # Remove "Multiple choice – choose one:\nCoach instruction..." (orphan line that precedes the real question)
    text = re.sub(
        r"Multiple choice\s*[–\-]\s*choose one\s*:\s*\n+\s*Coach instruction for this turn only:.*?(?=\n+\s*\(Multiple choice|\Z)",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
        count=0,
    )
    # Also remove any standalone line that starts with "Coach instruction:" (catch remaining leaks)
    text = re.sub(r"\n\s*Coach instruction:.*?(?=\n\n|\n\s*\(Multiple choice|\nCorrect!|\nIncorrect\.|\Z)", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove "[Coach instruction for this turn only: ...]" block (may span lines)
    text = re.sub(
        r"\[Coach instruction for this turn only:.*?\]\s*",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
        count=0,
    )
    # Remove instruction fragments that leaked into the middle of text (e.g. into option C)
    instruction_phrase = re.escape("Use only material from the book for this concept. Do not ask about unrelated topics.")
    text = re.sub(r"\s*\.?\s*" + instruction_phrase + r"[^.]*(?=\s+[A-D]\)|\s*$)", "", text, flags=re.IGNORECASE)
    # Broader: remove any segment containing "Use only material from the book for this concept" (any continuation)
    text = re.sub(
        r"\s*\.?\s*Use only material from the book for this concept\.[^\n]*(?=\s+[A-D]\)|\s*$|\n)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    for phrase in [
        "Do not ask about 'Object', tangibility, or any other topic",
        "DO NOT ask about 'Object'",
        "One question only\\. Always give clear confirmation",
        "then ask question \\d+ of \\d+ only",
    ]:
        text = re.sub(r"\s*\.?\s*" + phrase + r"[^.]*(?=\s+[A-D]\)|\s*$)", "", text, flags=re.IGNORECASE)
    # Quote chars: straight ' and curly '
    _q = r"['\u2019]"
    # Remove "Correct!' or 'Incorrect. The correct answer is X.' Then ask question N of 5 only. The concept is: ..." (instruction echo)
    text = re.sub(
        rf"(?is)Correct!{_q}\s*or\s*{_q}Incorrect\..*?(?=\(Multiple choice\s*[–\-]\s*choose|Which of the following|\Z)",
        "",
        text,
        count=0,
    )
    # Remove "Multiple choice – choose one:\nCorrect!' or 'Incorrect..." (orphan line + instruction fragment)
    text = re.sub(
        rf"(?is)Multiple choice\s*[–\-]\s*choose one\s*:\s*\n+\s*Correct!{_q}\s*or\s*{_q}Incorrect\..*?(?=\(Multiple choice\s*[–\-]\s*choose|Which of the following|\Z)",
        "",
        text,
        count=0,
    )
    # Fallback: remove any remaining "Coach instruction:" block (to next paragraph or (Multiple choice)
    text = re.sub(
        r"\n\s*Coach instruction:.*?(?=\n\n|\n\s*\(Multiple choice|\Z)",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove orphan "Multiple choice – choose one:" line when followed by instruction text (no real question)
    text = re.sub(
        rf"(?is)Multiple choice\s*[–\-]\s*choose one\s*:\s*\n+\s*Correct!{_q}.*?(?=\(Multiple choice\s*[–\-]\s*choose one\)|Which of the following|\Z)",
        "",
        text,
        count=0,
    )
    # Remove "Then ask question N of 5 only. The concept is: ..." block when it appears without preceding "Correct!"
    text = re.sub(
        r"(?is)Then ask question \d+ of \d+ only\.\s*The concept is:.*?(?=\(Multiple choice\s*[–\-]\s*choose|Which of the following|\Z)",
        "",
        text,
        count=0,
    )
    # Remove contradictory "Incorrect. The correct answer is X." when we already said "Correct! The correct answer is X." (same letter = echo)
    for letter in "A", "B", "C", "D":
        if f"Correct! The correct answer is {letter}." in text:
            text = re.sub(
                r"\n\s*Incorrect\.\s*The correct answer is " + re.escape(letter) + r"\.\s*",
                "\n",
                text,
                flags=re.IGNORECASE,
            )
    # Remove orphan "(Multiple choice – choose one)" lines (redundant label between "My answer" and next question)
    text = re.sub(
        r"\n\s*\(Multiple choice\s*[–\-]\s*choose one\)\s*:\s*\n(?!\s*Which)",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\n\s*\(Multiple choice\s*[–\-]\s*choose one\)\s*\n(?!\s*Which)",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _strip_mc_label(s: str) -> str:
    """Remove trailing '(Multiple choice – choose one)' etc. from option or question text."""
    import re
    s = s.strip()
    s = re.sub(r"\s*\(Multiple choice\s*[–\-]\s*choose one\)\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(Multiple choice\s*[–\-]\s*choose all that apply\)\s*$", "", s, flags=re.IGNORECASE)
    return s.strip()


def _strip_instruction_from_option(opt_text: str) -> str:
    """Remove instruction fragments that leaked into an option (e.g. 'C) . Use only material... (Multiple choice – choose one) Which...')."""
    import re
    s = opt_text.strip()
    # If the option contains "(Multiple choice" or "Which of the following", keep only the part before that (real option text is before the leak)
    if re.search(r"\(Multiple choice\s*[–\-]\s*choose", s, re.IGNORECASE) or "Which of the following" in s:
        s = re.split(r"\s*\(Multiple choice\s*[–\-]\s*choose.*$", s, flags=re.IGNORECASE)[0].strip()
        s = re.split(r"\s*Which of the following.*$", s, flags=re.IGNORECASE)[0].strip()
    # Option content is ". Use only material..." with no real answer – remove from ". Use only material" to end
    s = re.sub(r"^\s*\.\s*Use only material from the book.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*Do not ask about.*$", "", s, flags=re.IGNORECASE)
    for pattern in [
        r"^\s*\.?\s*Use only material from the book.*",
        r"\s*\.?\s*Use only material from the book.*$",
        r"\s*Do not ask about.*$",
        r"\s*DO NOT ask about.*$",
        r"\s*One question only\..*$",
        r"\s*Always give clear confirmation.*$",
        r"\s*Coach instruction.*$",
        r"\s*then ask question \d+ of \d+ only.*$",
    ]:
        s = re.sub(pattern, "", s, flags=re.IGNORECASE).strip()
    # If nothing left after stripping (option was entirely instruction leak), return a short placeholder
    if not s or not s.strip():
        return "—"
    return s.strip()


def parse_quiz_options(text: str) -> tuple[list[tuple[str, str]], bool] | None:
    """
    Parse A) B) C) D) options from coach reply.
    Returns ([(letter, option_text), ...], is_multiple) or None if not 4 options.
    is_multiple True when "all that apply" / "choose all" is in the text.
    Strips stray "(Multiple choice – choose one)" and instruction leaks from option text.
    """
    import re
    options = []
    for letter in ("A", "B", "C", "D"):
        pattern = rf"{letter}\)\s*(.+?)(?=\s*[A-D][\).:]|$)"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            opt_text = _strip_mc_label(m.group(1))
            opt_text = _strip_instruction_from_option(opt_text)
            options.append((letter, opt_text))
    if len(options) != 4:
        return None
    is_multiple = "all that apply" in text.lower() or "choose all" in text.lower()
    return (options, is_multiple)


def parse_quiz_question_text(text: str) -> str:
    """
    Return the part of the coach reply that is the question (before the first A) option).
    Strips "(Multiple choice – choose one)" so the real question shows. If empty, returns a fallback.
    """
    import re
    m = re.search(r"\s+A\)\s*", text, re.IGNORECASE)
    if m:
        raw = text[: m.start()].strip()
    else:
        raw = text.strip()
    cleaned = _strip_mc_label(raw)
    # If the "question" is only the label or empty, return a fallback so the UI shows something
    if not cleaned or len(cleaned) < 10:
        return "Select the correct answer below."
    return cleaned


def parse_last_answer_correct(reply: str) -> bool | None:
    """
    Infer whether the coach's reply indicates the student's last answer was correct or incorrect.
    Returns True if reply starts with 'Correct!', False if 'Incorrect', None otherwise.
    """
    if not reply or not reply.strip():
        return None
    text = reply.strip()
    if text.startswith("Correct!"):
        return True
    if text.startswith("Incorrect") or text.startswith("Incorrect."):
        return False
    return None


def parse_concepts_from_response(raw: str) -> list[tuple[str, str]]:
    """
    Parse the get_concepts_from_book response into a list of (chapter_label, concept).
    Returns e.g. [("Chapter 1: Numbers", "Divisibility rules"), ...].
    Accepts ## Chapter or # Chapter headers and - or * or • bullet points.
    """
    import re
    out: list[tuple[str, str]] = []
    if not (raw and raw.strip()):
        return out
    text = raw.strip()
    # Split by ## Chapter or # Chapter (allow optional #)
    blocks = re.split(r"\n#+\s+", text, flags=re.IGNORECASE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if not lines:
            continue
        chapter_line = lines[0].strip()
        # Skip preamble blocks (no "chapter" in first line)
        if not chapter_line or "chapter" not in chapter_line.lower():
            continue
        chapter_label = chapter_line if chapter_line.lower().startswith("chapter") else f"Chapter {chapter_line}"
        for line in lines[1:]:
            line = line.strip()
            # Accept - or * or • bullets, or lines like "1. concept"
            for prefix in ("-", "*", "•", "–"):
                if line.startswith(prefix):
                    concept = line.lstrip(prefix).strip().lstrip(".")
                    if concept and len(concept) > 1:
                        out.append((chapter_label, concept))
                    break
            else:
                m = re.match(r"^\d+[.)]\s*(.+)", line)
                if m and len(m.group(1).strip()) > 1:
                    out.append((chapter_label, m.group(1).strip()))
    return out

# Ollama: use when USE_OLLAMA=1 or OLLAMA_BASE_URL is set
def _ollama_base_url() -> str:
    url = (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


OLLAMA_BASE_URL = _ollama_base_url()
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:latest")


def _use_ollama() -> bool:
    use_ollama = os.environ.get("USE_OLLAMA", "").strip().lower()
    if use_ollama in ("0", "false", "no"):
        return False
    return use_ollama in ("1", "true", "yes") or bool(os.environ.get("OLLAMA_BASE_URL"))


def create_client() -> tuple[OpenAI, bool]:
    """
    Create the API client. Returns (client, use_ollama).
    When use_ollama is True, use chat.completions and the model from OLLAMA_MODEL.
    """
    if _use_ollama():
        client = OpenAI(base_url=_ollama_base_url(), api_key="ollama")
        return (client, True)
    return (OpenAI(), False)


def _is_ollama_client(client: OpenAI) -> bool:
    """True if client points at Ollama (e.g. base_url has 11434)."""
    return "11434" in str(getattr(client, "base_url", "") or "")


def extract_pdf_text(path: str) -> str:
    """
    Extract text from a PDF file. Returns the concatenated text of all pages.
    Raises FileNotFoundError or a pypdf error if the file cannot be read.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("PDF support requires: pip install pypdf") from None
    reader = PdfReader(path)
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def load_directory(dir_path: str) -> tuple[str, str]:
    """
    Read all PDF and .txt files from a directory (non-recursive) and return
    combined text plus a display name. Each file's content is prefixed with
    "--- File: <name> ---" so the model can attribute sources.
    Returns (combined_text, display_name) e.g. ("...", "Directory: my_books").
    """
    path = Path(dir_path)
    if not path.is_dir():
        raise NotADirectoryError(dir_path)
    parts: list[str] = []
    for f in sorted(path.iterdir()):
        if f.is_file() and f.suffix.lower() in (".pdf", ".txt"):
            try:
                if f.suffix.lower() == ".pdf":
                    text = extract_pdf_text(str(f))
                else:
                    text = f.read_text(encoding="utf-8", errors="replace")
                if text.strip():
                    parts.append(f"--- File: {f.name} ---\n\n{text.strip()}")
            except Exception:
                continue
    combined = "\n\n".join(parts) if parts else ""
    name = path.name or path.resolve().name
    return (combined, f"Directory: {name}")


def get_source_signature(source_path: str) -> str:
    """
    Return a string that changes when the source (file or directory) content changes.
    Used to skip re-analyzing when the source is unchanged.
    """
    p = Path(source_path).resolve()
    if p.is_file():
        try:
            stat = p.stat()
            return f"{p!s}:{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            return f"{p!s}:0"
    if p.is_dir():
        parts: list[tuple[str, int]] = []
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in (".pdf", ".txt"):
                try:
                    parts.append((f.name, f.stat().st_mtime_ns))
                except OSError:
                    pass
        return f"{p!s}:{parts}"
    return f"{p!s}:0"


# Default books directory: PDFs/texts here are loaded automatically by the GUI
def get_books_dir_path() -> str:
    """Path to the books folder (same directory as this module). Create the folder and add PDFs to use it."""
    return str(Path(__file__).resolve().parent / "books")


# Persistent memory: JSON file in app directory or user config
def get_memory_file_path() -> str:
    """Path to the persistent memory JSON file."""
    env_path = os.environ.get("MATH_OLYMPIAD_MEMORY_FILE")
    if env_path:
        return env_path
    base = Path(__file__).resolve().parent
    return str(base / "data" / "memory.json")


def load_persistent_memory() -> dict:
    """
    Load persistent memory from disk. Returns a dict with keys:
    concept_status, reference_path, memory_notes, concept_cache.
    concept_cache: { "abs_path": { "signature": str, "concepts": [[ch, cpt], ...] } } to skip re-analyzing unchanged sources.
    Missing file or invalid JSON returns default empty dict.
    """
    path = get_memory_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "concept_status": data.get("concept_status", {}),
            "reference_path": data.get("reference_path"),
            "memory_notes": data.get("memory_notes", ""),
            "concept_cache": data.get("concept_cache", {}),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"concept_status": {}, "reference_path": None, "memory_notes": "", "concept_cache": {}}


def save_persistent_memory(
    concept_status: dict[str, str],
    reference_path: str | None = None,
    memory_notes: str = "",
    concept_cache: dict | None = None,
) -> None:
    """Save concept status, last reference path, memory notes, and optional concept cache to disk."""
    path = Path(get_memory_file_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "concept_status": concept_status,
        "reference_path": reference_path,
        "memory_notes": memory_notes.strip(),
    }
    if concept_cache is not None:
        data["concept_cache"] = concept_cache
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# --- Student profiles and progress ---
def get_students_file_path() -> str:
    """Path to the students JSON file (profiles + progress)."""
    base = Path(__file__).resolve().parent
    return str(base / "data" / "students.json")


def load_students() -> dict:
    """
    Load student profiles and current selection. Returns:
    { "students": { id: { name, created_at, memory_notes, concept_status, quiz_history } }, "current_student_id": id or null }
    """
    path = get_students_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        students = data.get("students", {})
        for sid, profile in list(students.items()):
            if not isinstance(profile, dict):
                continue
            students[sid] = {
                "name": profile.get("name", "Student"),
                "created_at": profile.get("created_at", ""),
                "memory_notes": profile.get("memory_notes", ""),
                "concept_status": profile.get("concept_status", {}),
                "quiz_history": profile.get("quiz_history", []),
            }
        return {
            "students": students,
            "current_student_id": data.get("current_student_id"),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"students": {}, "current_student_id": None}


def save_students(students: dict, current_student_id: str | None) -> None:
    """Save students dict and current_student_id to disk."""
    path = Path(get_students_file_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"students": students, "current_student_id": current_student_id}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _build_input(
    system_prompt: str,
    history: list[dict],
    pdf_text: str | None = None,
    phase_instruction: str | None = None,
    memory_notes: str | None = None,
    max_pdf_chars: int | None = None,
    max_history_messages: int | None = None,
) -> list[dict]:
    """Build API input from system prompt, optional PDF context, conversation history, optional phase instruction, and optional persistent memory.
    When max_pdf_chars or max_history_messages are set (e.g. for Ollama's 4096-token limit), use them instead of the default limits."""
    pdf_limit = max_pdf_chars if max_pdf_chars is not None else MAX_PDF_CONTEXT_CHARS
    hist = list(history)
    if max_history_messages is not None and len(hist) > max_history_messages:
        hist = hist[-max_history_messages:]
    prompt = system_prompt
    if pdf_text:
        trimmed = pdf_text.strip()
        if len(trimmed) > pdf_limit:
            trimmed = trimmed[:pdf_limit] + "\n\n[... text truncated for context limit ...]"
        prompt = prompt + PDF_CONTEXT_INSTRUCTION + "\n\n--- Reference material (PDF) ---\n\n" + trimmed + "\n\n--- End of reference ---"
    if memory_notes and memory_notes.strip():
        prompt = prompt + MEMORY_INSTRUCTION + "\n\n--- What you remember about this student ---\n\n" + memory_notes.strip() + "\n\n--- End of memory ---"
    messages = [{"role": "system", "content": prompt}] + hist
    if phase_instruction:
        messages.append({"role": "user", "content": f"[Coach instruction for this turn only: {phase_instruction}]"})
    return messages


def get_concepts_from_book(pdf_text: str, *, client: OpenAI | None = None, model: str | None = None) -> str:
    """
    Ask the model to analyze the book (PDF text) and return a structured list of concepts per chapter.
    Returns raw text in the format: ## Chapter N: Title\\n- concept1\\n- concept2\\n...
    The GUI should parse this to show a list of concepts.
    """
    if client is None:
        client, use_ollama = create_client()
    else:
        use_ollama = _is_ollama_client(client)
    if model is None or (use_ollama and model.startswith("gpt-")):
        model = OLLAMA_DEFAULT_MODEL if use_ollama else (model or "gpt-5.2")
    trimmed = pdf_text.strip()
    # Use smaller limit for concept extraction to fit Ollama's 4096-token context
    limit = MAX_PDF_CHARS_FOR_CONCEPTS
    if len(trimmed) > limit:
        trimmed = trimmed[:limit] + "\n\n[... truncated for context limit; list concepts from this portion ...]"
    # Put book in USER message so Ollama doesn't truncate it away (system message often gets cut first)
    user_msg = (
        CONCEPTS_EXTRACTION_PROMPT
        + "\n\n--- Book content ---\n\n"
        + trimmed
        + "\n\n--- End of book ---"
    )
    system = (
        "You are a Grade 6 Maths Olympiad coach. When the user provides book content above and asks for a concept list, "
        "output ONLY the list in the exact format requested: ## Chapter N: Title then - concept per line. No other text or preamble."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
    if use_ollama:
        r = client.chat.completions.create(model=model, messages=messages)
        return (r.choices[0].message.content or "").strip()
    r = client.responses.create(model=model, input=messages)
    return (getattr(r, "output_text", None) or "").strip()


def get_reply(
    history: list[dict],
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    pdf_text: str | None = None,
    phase_instruction: str | None = None,
    memory_notes: str | None = None,
) -> str:
    """
    Get the next assistant reply for the given conversation history.
    If pdf_text is provided, the coach can answer and ask questions from that content.
    If phase_instruction is set (e.g. for quiz flow), the coach follows it for this turn.
    If memory_notes is set, the coach uses it as persistent memory about the student.
    Does not print anything. Used by the GUI.
    Uses OpenAI or Ollama depending on USE_OLLAMA / OLLAMA_BASE_URL.
    """
    if client is None:
        client, use_ollama = create_client()
    else:
        use_ollama = _is_ollama_client(client)
    if model is None or (use_ollama and model.startswith("gpt-")):
        model = OLLAMA_DEFAULT_MODEL if use_ollama else (model or "gpt-5.2")
    input_messages = _build_input(
        SYSTEM_PROMPT,
        history,
        pdf_text=pdf_text,
        phase_instruction=phase_instruction,
        memory_notes=memory_notes,
        max_pdf_chars=MAX_PDF_CONTEXT_CHARS_OLLAMA if use_ollama else None,
        max_history_messages=MAX_HISTORY_MESSAGES_OLLAMA if use_ollama else None,
    )
    if use_ollama:
        response = client.chat.completions.create(model=model, messages=input_messages)
        reply = (response.choices[0].message.content or "").strip()
        return strip_coach_instruction_from_reply(reply)
    response = client.responses.create(model=model, input=input_messages)
    reply = (getattr(response, "output_text", None) or "").strip()
    return strip_coach_instruction_from_reply(reply)


def ask(
    message: str,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    stream: bool = True,
) -> str:
    """
    Send a message to the Maths Olympiad agent and return the assistant's reply.
    Uses OpenAI or Ollama depending on USE_OLLAMA / OLLAMA_BASE_URL.
    """
    if client is None:
        client, use_ollama = create_client()
    else:
        use_ollama = _is_ollama_client(client)
    if model is None:
        model = OLLAMA_DEFAULT_MODEL if use_ollama else "gpt-5.2"
    input_messages = _build_input(SYSTEM_PROMPT, [{"role": "user", "content": message}])

    if use_ollama:
        if stream:
            stream_obj = client.chat.completions.create(
                model=model,
                messages=input_messages,
                stream=True,
            )
            chunks = []
            for chunk in stream_obj:
                c = chunk.choices[0].delta.content if chunk.choices else None
                if isinstance(c, str) and c:
                    print(c, end="", flush=True)
                    chunks.append(c)
            print()
            return "".join(chunks)
        response = client.chat.completions.create(model=model, messages=input_messages)
        text = (response.choices[0].message.content or "").strip()
        print(text)
        return text

    if stream:
        stream_obj = client.responses.create(
            model=model,
            input=input_messages,
            stream=True,
        )
        chunks = []
        for event in stream_obj:
            chunk = getattr(event, "delta", None) or getattr(event, "text", None)
            if isinstance(chunk, str) and chunk:
                print(chunk, end="", flush=True)
                chunks.append(chunk)
        print()
        return "".join(chunks)
    response = client.responses.create(model=model, input=input_messages)
    text = getattr(response, "output_text", None) or ""
    print(text)
    return text


def chat(
    *,
    client: OpenAI | None = None,
    model: str | None = None,
    stream: bool = True,
) -> None:
    """
    Run an interactive chat session with the Maths Olympiad coach.
    Uses OpenAI or Ollama depending on USE_OLLAMA / OLLAMA_BASE_URL.
    """
    if client is None:
        client, use_ollama = create_client()
    else:
        use_ollama = _is_ollama_client(client)
    if model is None:
        model = OLLAMA_DEFAULT_MODEL if use_ollama else "gpt-5.2"

    history: list[dict] = []
    backend = "Ollama" if use_ollama else "OpenAI"
    print(f"=== Grade 6 Maths Olympiad Coach (chat, {backend}) ===\n")
    print("Ask a question or paste a problem. Type quit/exit/q to stop.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        history.append({"role": "user", "content": user_input})
        input_messages = _build_input(SYSTEM_PROMPT, history)

        if use_ollama:
            if stream:
                stream_obj = client.chat.completions.create(
                    model=model,
                    messages=input_messages,
                    stream=True,
                )
                chunks = []
                for chunk in stream_obj:
                    c = chunk.choices[0].delta.content if chunk.choices else None
                    if isinstance(c, str) and c:
                        print(c, end="", flush=True)
                        chunks.append(c)
                print()
                reply = "".join(chunks)
            else:
                response = client.chat.completions.create(model=model, messages=input_messages)
                reply = (response.choices[0].message.content or "").strip()
                print(reply)
        else:
            if stream:
                stream_obj = client.responses.create(
                    model=model,
                    input=input_messages,
                    stream=True,
                )
                chunks = []
                for event in stream_obj:
                    chunk = getattr(event, "delta", None) or getattr(event, "text", None)
                    if isinstance(chunk, str) and chunk:
                        print(chunk, end="", flush=True)
                        chunks.append(chunk)
                print()
                reply = "".join(chunks)
            else:
                response = client.responses.create(model=model, input=input_messages)
                reply = getattr(response, "output_text", None) or ""
                print(reply)

        history.append({"role": "assistant", "content": reply})
        print()


def main() -> None:
    """Run chat by default, or a one-shot question if passed as arguments."""
    import sys

    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print("=== Grade 6 Maths Olympiad Agent (one-shot) ===\n")
        print("Question:", question, "\n")
        print("Coach:\n")
        ask(question, stream=True)
        print("\n--- Run without args for chat: python agent.py ---\n")
    else:
        chat(stream=True)


if __name__ == "__main__":
    main()
