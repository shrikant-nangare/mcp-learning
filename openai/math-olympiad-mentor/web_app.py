"""
Grade 6 Maths Olympiad Coach – Web app

FastAPI app for chat and concepts. Uses Ollama by default (model: gemma3). Set USE_OLLAMA=0 for OpenAI.
Loads PDFs from the books/ folder on startup and exposes REST API + simple chat UI.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Default to Ollama. To use OpenAI, set USE_OLLAMA=0 and OPENAI_API_KEY.
os.environ.setdefault("USE_OLLAMA", "1")
os.environ.setdefault("OLLAMA_MODEL", "gpt-oss:latest")

from agent import (
    get_reply,
    get_concepts_from_book,
    load_directory,
    get_books_dir_path,
    get_source_signature,
    parse_concepts_from_response,
    parse_quiz_options,
    parse_quiz_question_text,
    parse_last_answer_correct,
    load_persistent_memory,
    save_persistent_memory,
    load_students,
    save_students,
    QUIZ_MC_INSTRUCTION,
    QUIZ_ABOUT_CONCEPT,
    NUM_QUIZ_QUESTIONS,
    QUIZ_PASS_PERCENT,
    EXPLAIN_FROM_BOOK_INSTRUCTION,
)

# In-memory state (per process; use single replica or sticky sessions in K8s)
_state = {
    "reference_text": None,
    "reference_path": None,
    "concepts_list": [],
    "concept_cache": {},
    "concept_status": {},
    "memory_notes": "",
    "students": {},  # id -> { name, created_at, memory_notes, concept_status, quiz_history }
    "current_student_id": None,
}


def _ensure_books_loaded() -> bool:
    """Load from books folder and analyze if not already loaded. Returns True if we have content."""
    if _state["reference_text"]:
        return True
    books_path = get_books_dir_path()
    if not Path(books_path).is_dir():
        log.warning("Books folder not found: %s – add PDFs to this folder", books_path)
        return False
    try:
        text, name = load_directory(books_path)
        if not text:
            log.warning("No PDF or .txt files in %s (or all empty)", books_path)
            return False
        _state["reference_text"] = text
        _state["reference_path"] = books_path
        log.info("Loaded book from %s (%d chars)", name, len(text))
        try:
            _run_analysis()
            log.info("Concept analysis done: %d concepts", len(_state["concepts_list"]))
        except Exception as e:
            log.warning("Concept analysis failed (API?): %s – click Re-analyze after fixing", e)
        return True
    except Exception as e:
        log.warning("Failed to load books from %s: %s", books_path, e)
        return False


def _run_analysis(force: bool = False) -> None:
    """Populate concepts_list from reference_text (use cache if signature matches unless force=True)."""
    text = _state.get("reference_text")
    path = _state.get("reference_path")
    if not text or not path:
        return
    cache_key = str(Path(path).resolve())
    if not force:
        try:
            sig = get_source_signature(path)
            cached = _state["concept_cache"].get(cache_key)
            if cached and cached.get("signature") == sig and cached.get("concepts"):
                _state["concepts_list"] = [tuple(c) for c in cached["concepts"]]
                return
        except Exception:
            pass
    raw = get_concepts_from_book(text)
    _state["concepts_list"] = parse_concepts_from_response(raw)
    if not _state["concepts_list"] and raw:
        log.warning("Concept extraction returned 0 concepts. Raw response (first 800 chars): %s", (raw[:800] + "..." if len(raw) > 800 else raw))
    _state["concept_cache"][cache_key] = {
        "signature": get_source_signature(path),
        "concepts": [[ch, cpt] for ch, cpt in _state["concepts_list"]],
    }
    try:
        save_persistent_memory(
            concept_status=_state["concept_status"],
            reference_path=path,
            memory_notes=_state["memory_notes"],
            concept_cache=_state["concept_cache"],
        )
        if _state.get("current_student_id"):
            _save_current_student()
    except Exception:
        pass


def _load_students() -> None:
    """Load students from disk and apply current student to _state."""
    try:
        data = load_students()
        _state["students"] = data.get("students", {})
        _state["current_student_id"] = data.get("current_student_id")
        _apply_current_student()
    except Exception:
        _state["students"] = {}
        _state["current_student_id"] = None


def _apply_current_student() -> None:
    """Copy current student's concept_status and memory_notes into _state."""
    sid = _state.get("current_student_id")
    students = _state.get("students", {})
    if sid and sid in students:
        s = students[sid]
        _state["concept_status"] = dict(s.get("concept_status", {}))
        _state["memory_notes"] = s.get("memory_notes", "") or ""
    else:
        _state["concept_status"] = {}
        _state["memory_notes"] = ""


def _save_current_student() -> None:
    """Write _state concept_status and memory_notes back to current student and persist."""
    sid = _state.get("current_student_id")
    students = _state.get("students", {})
    if sid and sid in students:
        students[sid]["concept_status"] = dict(_state["concept_status"])
        students[sid]["memory_notes"] = (_state.get("memory_notes") or "").strip()
        save_students(students, sid)


def _load_persisted() -> None:
    try:
        data = load_persistent_memory()
        _state["concept_cache"] = data.get("concept_cache", {})
        _load_students()
        if not _state["students"]:
            _state["concept_status"] = data.get("concept_status", {})
            _state["memory_notes"] = data.get("memory_notes", "") or ""
    except Exception:
        pass


app = FastAPI(title="Math Olympiad Coach", version="1.0.0")


@app.on_event("startup")
def startup() -> None:
    _load_persisted()
    books_path = get_books_dir_path()
    log.info("Books folder: %s (exists: %s)", books_path, Path(books_path).is_dir())
    _ensure_books_loaded()
    log.info("Startup complete – concepts: %d", len(_state["concepts_list"]))


@app.get("/health", tags=["health"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/concepts", tags=["api"])
def get_concepts(reanalyze: bool = False) -> dict:
    try:
        _ensure_books_loaded()
    except Exception:
        pass  # leave concepts_list empty
    if reanalyze and _state.get("reference_text") and _state.get("reference_path"):
        try:
            _run_analysis(force=True)
            log.info("Re-analyze done: %d concepts", len(_state["concepts_list"]))
        except Exception as e:
            log.warning("Re-analyze failed: %s", e)
    concepts = [
        {"chapter": ch, "concept": cpt, "status": _state["concept_status"].get(f"{ch} → {cpt}", "")}
        for ch, cpt in _state["concepts_list"]
    ]
    source = _state.get("reference_path") or ""
    message = None
    if not concepts and not source:
        message = "No book loaded. Add PDF or .txt files to the books/ folder (next to the app) and click Re-analyze book."
    elif not concepts and source:
        message = "No concepts found. Ensure Ollama is running (gpt-oss:latest), or set OPENAI_API_KEY and USE_OLLAMA=0, then click Re-analyze book."
    return {"concepts": concepts, "source": source, "message": message}


class ChatRequest(BaseModel):
    message: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": "..."}]
    phase_instruction: str | None = None  # for quiz: "Ask question 1 of 10", "Evaluate then ask Q2", etc.


class ChatResponse(BaseModel):
    reply: str
    options: list[list[str]] | None = None  # [[letter, text], ...] for multiple choice
    is_multiple: bool = False  # true when "choose all that apply"
    question_text: str | None = None  # question part only (before A)) for multiple-choice display
    last_answer_correct: bool | None = None  # quiz: True/False when coach evaluated an answer; None otherwise


@app.post("/api/chat", response_model=ChatResponse, tags=["api"])
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    history = list(req.history) + [{"role": "user", "content": req.message}]
    memory_parts = []
    if _state["memory_notes"].strip():
        memory_parts.append(_state["memory_notes"].strip())
    if _state["concept_status"]:
        memory_parts.append("Concept mastery:")
        for cpt, status in _state["concept_status"].items():
            memory_parts.append(f"  - {cpt}: {status}")
    memory_notes = "\n\n".join(memory_parts) or None
    phase_instruction = req.phase_instruction
    if not phase_instruction and req.message.strip().startswith("Explain this concept from the book"):
        prefix = "Explain this concept from the book:"
        concept_spec = req.message.strip()[len(prefix):].strip()
        if concept_spec:
            phase_instruction = (
                f"The exact concept the student asked to explain is: {concept_spec}. "
                "You must explain ONLY this concept—do not substitute or explain a different topic. "
                "The concept name you highlight at the start must exactly match the above (same wording). "
            ) + EXPLAIN_FROM_BOOK_INSTRUCTION
        else:
            phase_instruction = EXPLAIN_FROM_BOOK_INSTRUCTION
    try:
        reply = get_reply(
            history,
            pdf_text=_state.get("reference_text"),
            memory_notes=memory_notes,
            phase_instruction=phase_instruction,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
    options_data = parse_quiz_options(reply)
    options = [list(o) for o in options_data[0]] if options_data else None
    is_multiple = options_data[1] if options_data else False
    question_text = parse_quiz_question_text(reply) if options_data else None
    last_answer_correct = None
    if phase_instruction and "The student's answer for this question" in phase_instruction:
        last_answer_correct = parse_last_answer_correct(reply)
    return ChatResponse(
        reply=reply,
        options=options,
        is_multiple=is_multiple,
        question_text=question_text,
        last_answer_correct=last_answer_correct,
    )


# --- Student profiles and progress API ---
class CreateStudentRequest(BaseModel):
    name: str


class SelectStudentRequest(BaseModel):
    student_id: str


class QuizResultRequest(BaseModel):
    concept: str  # e.g. "Chapter 1 → Natural Numbers"
    score_pct: int  # 0–100
    passed: bool


class UpdateStudentRequest(BaseModel):
    name: str | None = None
    memory_notes: str | None = None


@app.get("/api/students", tags=["api"])
def list_students() -> dict:
    """List all student profiles (id, name, created_at). Progress details in GET /api/students/current."""
    students = _state.get("students", {})
    current = _state.get("current_student_id")
    list_ = [
        {
            "id": sid,
            "name": s.get("name", "Student"),
            "created_at": s.get("created_at", ""),
            "is_current": sid == current,
        }
        for sid, s in students.items()
    ]
    return {"students": list_, "current_student_id": current}


@app.post("/api/students", tags=["api"])
def create_student(req: CreateStudentRequest) -> dict:
    """Create a new student profile and optionally set as current."""
    name = (req.name or "").strip() or "Student"
    sid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _state.setdefault("students", {})[sid] = {
        "name": name,
        "created_at": now,
        "memory_notes": "",
        "concept_status": {},
        "quiz_history": [],
    }
    _state["current_student_id"] = sid
    _apply_current_student()
    save_students(_state["students"], sid)
    return {"id": sid, "name": name, "created_at": now}


@app.post("/api/students/select", tags=["api"])
def select_student(req: SelectStudentRequest) -> dict:
    """Set the current student by id."""
    sid = req.student_id
    if sid not in _state.get("students", {}):
        raise HTTPException(status_code=404, detail="Student not found")
    _state["current_student_id"] = sid
    _apply_current_student()
    save_students(_state["students"], sid)
    return {"current_student_id": sid}


@app.get("/api/students/current", tags=["api"])
def get_current_student() -> dict:
    """Get current student profile and progress (concept_status, quiz_history)."""
    sid = _state.get("current_student_id")
    students = _state.get("students", {})
    if not sid or sid not in students:
        return {"current_student_id": None, "profile": None, "progress": None}
    s = students[sid]
    return {
        "current_student_id": sid,
        "profile": {
            "id": sid,
            "name": s.get("name", "Student"),
            "created_at": s.get("created_at", ""),
            "memory_notes": s.get("memory_notes", ""),
        },
        "progress": {
            "concept_status": s.get("concept_status", {}),
            "quiz_history": s.get("quiz_history", []),
        },
    }


@app.post("/api/students/current/quiz-result", tags=["api"])
def record_quiz_result(req: QuizResultRequest) -> dict:
    """Record a quiz result for the current student (call when quiz is done)."""
    sid = _state.get("current_student_id")
    if not sid or sid not in _state.get("students", {}):
        raise HTTPException(status_code=400, detail="No current student selected")
    students = _state["students"]
    students[sid].setdefault("concept_status", {})[req.concept] = (
        "fully understood" if req.passed else "not fully understood"
    )
    students[sid].setdefault("quiz_history", []).append(
        {
            "concept": req.concept,
            "score_pct": req.score_pct,
            "passed": req.passed,
            "date": datetime.now(timezone.utc).isoformat(),
        }
    )
    _state["concept_status"] = dict(students[sid]["concept_status"])
    save_students(students, sid)
    return {"ok": True}


@app.patch("/api/students/current", tags=["api"])
def update_current_student(req: UpdateStudentRequest) -> dict:
    """Update current student name and/or memory notes."""
    sid = _state.get("current_student_id")
    if not sid or sid not in _state.get("students", {}):
        raise HTTPException(status_code=400, detail="No current student selected")
    if req.name is not None:
        _state["students"][sid]["name"] = (req.name or "").strip() or "Student"
    if req.memory_notes is not None:
        _state["students"][sid]["memory_notes"] = (req.memory_notes or "").strip()
        _state["memory_notes"] = _state["students"][sid]["memory_notes"]
    save_students(_state["students"], sid)
    return {"ok": True}


# Serve the single-page chat UI
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = _CHAT_HTML
    return HTMLResponse(html)


_CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sixth grade math · Math Coach</title>
  <style>
    :root{
      --bg: #f6f7fb;
      --card: #ffffff;
      --muted: #6b7280;
      --text: #111827;
      --border: #e5e7eb;
      --green: #63b000;
      --green-2: #4ea200;
      --blue: #2563eb;
      --red: #dc2626;
      --shadow: 0 10px 30px rgba(17,24,39,0.08);
    }
    * { box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
    a { color: inherit; text-decoration: none; }

    .topbar { position: sticky; top: 0; z-index: 50; background: var(--green); color: #fff; }
    .topbar-inner { max-width: 1320px; margin: 0 auto; padding: 10px 16px; display: flex; align-items: center; gap: 14px; }
    .brand { display: flex; align-items: center; gap: 10px; min-width: 180px; }
    .brand-mark { width: 34px; height: 34px; border-radius: 10px; background: rgba(255,255,255,0.18); display: grid; place-items: center; font-weight: 900; letter-spacing: 0.5px; }
    .brand-text { font-weight: 800; }
    .search { flex: 1; display: flex; }
    .search input { width: 100%; padding: 10px 12px; border-radius: 999px; border: 1px solid rgba(255,255,255,0.35); background: rgba(255,255,255,0.18); color: #fff; outline: none; }
    .search input::placeholder { color: rgba(255,255,255,0.85); }
    .top-actions { display: flex; align-items: center; gap: 8px; }
    .top-pill { padding: 8px 10px; border-radius: 999px; background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.25); font-weight: 700; font-size: 0.85rem; }

    .subnav { background: #ffffff; border-bottom: 1px solid var(--border); }
    .subnav-inner { max-width: 1320px; margin: 0 auto; padding: 10px 16px; display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
    .subnav-right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
    .mini-btn { display: none; padding: 7px 10px; border-radius: 999px; border: 1px solid var(--border); background: #fff; font-weight: 900; cursor: pointer; }
    .mini-btn:hover { background: #f3f4f6; }
    body.coach-mode .mini-btn { display: inline-flex; }
    .tab { padding: 6px 10px; border-radius: 999px; font-weight: 700; font-size: 0.9rem; color: #374151; }
    .tab.active { background: #eaf7d9; color: #14532d; border: 1px solid #c7f0a5; }
    .tab.muted { color: #6b7280; font-weight: 600; }

    .page { max-width: 1320px; margin: 0 auto; padding: 16px; display: grid; grid-template-columns: 1fr; gap: 16px; align-items: start; }
    .top-area { display: grid; grid-template-columns: 420px minmax(0, 1fr); gap: 16px; align-items: stretch; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); }
    .card-pad { padding: 14px; }
    body.coach-mode #skillsHero { display: none; }

    .h1 { margin: 0; font-size: 2rem; letter-spacing: -0.02em; }
    .sub { color: var(--muted); margin: 6px 0 0; line-height: 1.35; }

    /* Left */
    .side-title { margin: 0 0 10px; font-size: 1rem; font-weight: 800; }
    .student-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
    .student-row select { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); min-width: 180px; }
    #newStudentName { padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border); width: 180px; }
    .btn { padding: 10px 12px; border-radius: 12px; border: 1px solid transparent; cursor: pointer; font-weight: 800; }
    .btn:disabled { opacity: 0.55; cursor: not-allowed; }
    .btn-green { background: var(--green); color: #fff; }
    .btn-green:hover { background: var(--green-2); }
    .btn-blue { background: var(--blue); color: #fff; }
    .btn-blue:hover { filter: brightness(0.95); }
    .btn-red { background: var(--red); color: #fff; }
    .btn-red:hover { filter: brightness(0.95); }
    .btn-gray { background: #6b7280; color: #fff; }
    .btn-gray:hover { background: #4b5563; }
    .stack { display: grid; gap: 10px; }
    #progressSummary { font-size: 0.9rem; color: #6b4f00; }

    .selected-box { border: 1px solid var(--border); border-radius: 12px; padding: 12px; background: #fbfbff; }
    .selected-label { font-size: 0.9rem; color: #374151; font-weight: 800; display: block; margin-bottom: 10px; }
    .action-row { display: grid; grid-template-columns: 1fr; gap: 10px; }

    /* Center skills grid */
    .skills-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; padding: 14px 14px 10px; border-bottom: 1px solid var(--border); }
    .skills-head h2 { margin: 0; font-size: 1.1rem; font-weight: 900; }
    .skills-meta { display: flex; align-items: center; gap: 10px; }
    .reanalyze { font-size: 0.9rem; padding: 10px 12px; border-radius: 12px; border: 1px solid var(--border); background: #fff; font-weight: 900; cursor: pointer; }
    .reanalyze:hover { background: #f3f4f6; }
    .hint { padding: 12px 14px; color: var(--muted); }

    .skills-grid { list-style: none; margin: 0; padding: 14px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
    .chapter-row { border: 1px solid var(--border); border-radius: 14px; overflow: hidden; background: #fff; }
    .chapter-header { display: flex; align-items: center; gap: 10px; padding: 12px 12px; background: #f7fafb; border-bottom: 1px solid var(--border); }
    .chapter-badge { width: 28px; height: 28px; border-radius: 999px; display: grid; place-items: center; font-weight: 900; color: #fff; background: #3b82f6; flex-shrink: 0; }
    .chapter-title { font-weight: 900; font-size: 0.95rem; color: #111827; }
    .chapter-sub { font-size: 0.85rem; color: var(--muted); margin-top: 2px; }
    .chapter-title-wrap { display: grid; }
    .chapter-concepts { margin: 0; padding: 10px 12px 12px 34px; }
    .concept-item { margin: 0; padding: 6px 10px; border-radius: 10px; cursor: pointer; border: 1px solid transparent; display: flex; justify-content: space-between; gap: 10px; }
    .concept-item:hover { background: #eef6ff; }
    .concept-item.selected { background: #dcfce7; border-color: #86efac; }
    .concept-name { font-weight: 700; color: #111827; }
    .concept-status { font-size: 0.78rem; font-weight: 900; padding: 4px 8px; border-radius: 999px; background: #f3f4f6; color: #374151; white-space: nowrap; }
    .concept-status.mastered { background: #dcfce7; color: #14532d; border: 1px solid #86efac; }
    .concept-status.partial { background: #fff7ed; color: #7c2d12; border: 1px solid #fdba74; }
    .error { color: #dc2626; }

    /* Top chat (Coach) */
    .coach-card { display: flex; flex-direction: column; min-height: 520px; overflow: hidden; }
    .chat-head { padding: 12px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .chat-title { font-weight: 900; }
    #quizStatus { padding: 6px 10px; border-radius: 999px; background: #e0f2fe; border: 1px solid #7dd3fc; font-weight: 900; font-size: 0.82rem; color: #0c4a6e; display: none; }
    #log { flex: 1; overflow-y: auto; padding: 12px 14px; background: #fff; }
    #log .user { margin: 10px 0; padding: 10px 12px; border-radius: 12px; background: #eff6ff; border: 1px solid #bfdbfe; }
    #log .coach { margin: 10px 0; padding: 10px 12px; border-radius: 12px; background: #f8fafc; border: 1px solid #e2e8f0; line-height: 1.5; }
    #log .coach h2 { background: #f0fdf4; padding: 8px 10px; border-radius: 10px; border-left: 4px solid #22c55e; margin: 10px 0 6px; }
    #log .coach table { border-collapse: collapse; margin: 10px 0; width: 100%; }
    #log .coach th, #log .coach td { border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left; }
    #log .coach th { background: #f1f5f9; font-weight: 800; }
    #log .coach code { background: #e2e8f0; padding: 2px 6px; border-radius: 6px; font-size: 0.9em; }
    #log .coach pre { background: #111827; color: #e5e7eb; padding: 12px; border-radius: 12px; overflow-x: auto; margin: 10px 0; font-size: 12px; }
    #log .coach pre code { background: none; padding: 0; color: inherit; }
    #log .thinking { color: var(--muted); font-style: italic; }

    #quizChoiceBox { margin: 10px 14px 0; padding: 12px; background: #f0fdf4; border-radius: 12px; border: 2px solid #22c55e; }
    #quizChoiceBox .quiz-question { font-weight: 800; color: #14532d; margin-bottom: 10px; padding: 10px; background: #dcfce7; border-radius: 10px; border-left: 4px solid #22c55e; line-height: 1.4; }
    #quizChoiceBox h4 { margin: 0 0 6px; font-size: 0.9rem; color: #166534; }
    #quizChoiceBox label { display: block; margin: 8px 0; cursor: pointer; padding: 6px 8px; border-radius: 8px; line-height: 1.4; }
    #quizChoiceBox label:hover { background: #dcfce7; }
    #quizChoiceBox input[type=radio], #quizChoiceBox input[type=checkbox] { margin-right: 10px; vertical-align: top; margin-top: 3px; }
    #quizChoiceBox .submit-quiz { margin-top: 10px; padding: 10px 14px; background: #16a34a; color: #fff; border: none; border-radius: 12px; cursor: pointer; font-weight: 900; }

    #readyBtnWrap { margin: 10px 14px 0; padding: 12px; background: #f0fdf4; border-radius: 12px; border: 2px solid #22c55e; }
    #readyBtnWrap .ready-btn { padding: 10px 14px; background: #16a34a; color: #fff; border: none; border-radius: 12px; cursor: pointer; font-weight: 900; width: 100%; }
    #readyBtnWrap .ready-btn:hover { background: #15803d; }

    .input-row { display: flex; gap: 10px; padding: 12px 14px; border-top: 1px solid var(--border); background: #fff; }
    #msg { flex: 1; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; font-size: 1rem; }
    #send { padding: 10px 16px; background: var(--blue); color: #fff; border: none; border-radius: 12px; cursor: pointer; font-weight: 900; }
    #send:hover { filter: brightness(0.95); }
    #send:disabled { opacity: 0.6; cursor: not-allowed; }

    @media (max-width: 900px) {
      .top-area { grid-template-columns: 1fr; }
      .skills-grid { grid-template-columns: 1fr; }
      .brand { min-width: auto; }
    }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="brand" aria-label="Math Coach">
        <div class="brand-mark">M</div>
        <div class="brand-text">Math Coach</div>
      </div>
      <div class="search">
        <input id="conceptSearch" type="text" placeholder="Search topics, skills, and more" autocomplete="off" />
      </div>
      <div class="top-actions">
        <div class="top-pill">Grade 6</div>
      </div>
    </div>
  </div>
  <div class="subnav">
    <div class="subnav-inner" role="navigation" aria-label="Subjects">
      <span class="tab active">Math</span>
      <span class="tab muted">Language arts</span>
      <span class="tab muted">Science</span>
      <span class="tab muted">Social studies</span>
      <span class="tab muted">Spanish</span>
      <span class="tab muted">Recommendations</span>
      <span class="subnav-right">
        <button type="button" class="mini-btn" id="btnBackToSkills">Back to skills</button>
      </span>
    </div>
  </div>

  <div class="page">
    <section class="top-area" aria-label="Student and coach">
      <aside class="card card-pad" aria-label="Student and actions">
        <h3 class="side-title">Student</h3>
        <div class="student-row">
          <select id="studentSelect" aria-label="Select student">
            <option value="">No student selected</option>
          </select>
          <input type="text" id="newStudentName" placeholder="New student name" />
          <button type="button" class="btn btn-green" id="btnAddStudent">Add</button>
        </div>
        <div id="progressSummary"></div>

        <div class="selected-box" style="margin-top: 12px;">
          <span class="selected-label" id="selectedLabel">No concept selected</span>
          <div class="action-row">
            <button type="button" id="btnExplain" class="btn btn-blue" disabled>Explain this skill</button>
            <button type="button" id="btnQuiz" class="btn btn-red" disabled>Start 5-question quiz</button>
          </div>
          <button type="button" id="btnReanalyze" class="reanalyze" style="width:100%; margin-top: 10px;">Re-analyze book</button>
          <p class="sub" style="margin: 10px 0 0;">Tip: click a skill in the list to select it.</p>
        </div>
      </aside>

      <aside class="card coach-card" aria-label="Coach chat">
        <div class="chat-head">
          <div class="chat-title">Coach</div>
          <div id="quizStatus" aria-live="polite"></div>
        </div>
        <div id="log"></div>
        <div id="quizChoiceBox" style="display:none;">
          <h4 id="quizChoiceTitle">Multiple choice – choose one:</h4>
          <div id="quizChoiceQuestion" class="quiz-question"></div>
          <div id="quizChoiceOptions" class="quiz-options"></div>
          <button type="button" class="submit-quiz" id="submitQuizBtn">Submit answer</button>
        </div>
        <div id="readyBtnWrap" style="display:none;">
          <button type="button" class="ready-btn" id="readyBtn">I'm ready – start 5 questions</button>
        </div>
        <div class="input-row">
          <input type="text" id="msg" placeholder="Ask a question…" />
          <button type="button" id="send">Send</button>
        </div>
      </aside>
    </section>

    <main class="card" aria-label="Skills list" id="skillsPanel">
      <div class="card-pad" id="skillsHero">
        <h1 class="h1">Sixth grade math</h1>
        <p class="sub">Pick a skill to practice, or ask the coach in the chat panel.</p>
      </div>
      <div class="skills-head" id="conceptsBox">
        <h2>Skills</h2>
        <div class="skills-meta">
          <span class="sub" id="conceptsCount"></span>
        </div>
      </div>
      <div id="conceptsHint" class="hint" style="display:none;"></div>
      <ul id="conceptsList" class="skills-grid"></ul>
    </main>
  </div>
  <script>
    const log = document.getElementById('log');
    const msg = document.getElementById('msg');
    const send = document.getElementById('send');
    const selectedLabel = document.getElementById('selectedLabel');
    const btnExplain = document.getElementById('btnExplain');
    const btnQuiz = document.getElementById('btnQuiz');
    const conceptSearch = document.getElementById('conceptSearch');
    const btnBackToSkills = document.getElementById('btnBackToSkills');
    const skillsPanel = document.getElementById('skillsPanel');

    let selectedConcept = null;
    let conceptsData = [];
    let quizPhase = null;
    let quizConcept = null;
    let quizResults = [];
    let studentsList = [];
    let currentStudentId = null;
    const QUIZ_MC = 'Each question must be multiple choice with exactly 4 options (A) B) C) D)). State "(Multiple choice – choose one)" or "(Multiple choice – choose all that apply)".';

    const NUM_QUESTIONS = 5;
    const PASS_PERCENT = 60;
    const QUIZ_ABOUT_CONCEPT = "The question must be ONLY about the concept just explained (e.g. if the concept was 'Natural numbers', ask about natural numbers, counting, 1/2/3..., or that definition—not about 'Object', 'tangibility', or any other topic). Use only material from the book for this concept. Do not ask about unrelated topics.";
    const QUIZ_CONFIRM = "Always give clear confirmation: if correct say 'Correct!'; if incorrect say 'Incorrect.' and state the correct answer (e.g. 'The correct answer is C.'), then continue.";
    function getQuizPhaseInstruction(message, currentPhase) {
      if (!currentPhase || !quizConcept) return { instruction: null, nextPhase: currentPhase };
      const msg = (message || '').trim();
      const msgLower = msg.toLowerCase();
      let answerPart = msg;
      if (msgLower.indexOf('my answer:') === 0)
        answerPart = msg.slice(msg.indexOf(':') + 1).trim() || msg;
      const conceptLabel = typeof quizConcept === 'object' && quizConcept ? (quizConcept.chapter + ' → ' + quizConcept.concept) : String(quizConcept || '');
      const conceptName = (typeof quizConcept === 'object' && quizConcept ? quizConcept.concept : conceptLabel.split(' → ').pop()) || conceptLabel;
      const conceptLine = "The concept is: " + conceptLabel + ". " + QUIZ_ABOUT_CONCEPT + " Do NOT ask about 'Object', tangibility, or any other topic—only about this concept. ";
      if (currentPhase === 'explain') {
        if (msgLower === 'ready')
          return { instruction: "The student is ready. Ask question 1 of " + NUM_QUESTIONS + " about the concept '" + conceptName + "' only. " + conceptLine + "One question only. " + QUIZ_MC, nextPhase: 'q1' };
        return { instruction: null, nextPhase: 'explain' };
      }
      if (currentPhase.startsWith('q')) {
        const n = parseInt(currentPhase.slice(1), 10);
        if (n >= 1 && n < NUM_QUESTIONS)
          return { instruction: "The student's answer for this question was: " + answerPart + ". Evaluate and confirm: say 'Correct!' or 'Incorrect. The correct answer is X.' Then ask question " + (n + 1) + " of " + NUM_QUESTIONS + " only. " + conceptLine + "One question only. " + QUIZ_CONFIRM + " " + QUIZ_MC, nextPhase: 'q' + (n + 1) };
        if (n === NUM_QUESTIONS)
          return { instruction: "The student's answer for this question was: " + answerPart + ". Evaluate and confirm: say 'Correct!' or 'Incorrect. The correct answer is X.' Then give a short summary. The summary must refer ONLY to the concept that was quizzed (" + conceptLabel + "). Do not mention other topics (e.g. polygons, geometry). You must include a line: Score: N/" + NUM_QUESTIONS + " (M%). Base the score only on the 5 answers in this quiz. If M >= " + PASS_PERCENT + " say 'Concept marked as fully understood.' Otherwise say 'Concept not yet fully understood.' " + QUIZ_CONFIRM, nextPhase: 'done' };
      }
      return { instruction: null, nextPhase: currentPhase };
    }

    function append(role, text, className) {
      const p = document.createElement('div');
      p.className = className || role;
      if (role === 'coach' && typeof marked !== 'undefined') {
        try {
          p.innerHTML = marked.parse(text || '');
        } catch (e) {
          p.innerHTML = (text || '').replace(/\\n/g, '<br>').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
      } else {
        const escaped = (text || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\\n/g, '<br>');
        p.innerHTML = escaped;
      }
      log.appendChild(p);
      log.scrollTop = log.scrollHeight;
    }

    function setSelected(concept) {
      selectedConcept = concept;
      selectedLabel.textContent = concept ? ('Selected: ' + concept.chapter + ' → ' + concept.concept) : 'No concept selected';
      btnExplain.disabled = !concept;
      btnQuiz.disabled = !concept;
      document.querySelectorAll('#conceptsList .concept-item').forEach((li) => {
        li.classList.toggle('selected', concept && li.dataset.chapter === concept.chapter && li.dataset.concept === concept.concept);
      });
    }

    function setCoachMode(enabled) {
      document.body.classList.toggle('coach-mode', !!enabled);
    }

    if (btnBackToSkills && skillsPanel) {
      btnBackToSkills.addEventListener('click', () => {
        setCoachMode(false);
        skillsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }

    function applyConceptFilter() {
      const q = (conceptSearch && conceptSearch.value || '').trim().toLowerCase();
      const chapterRows = Array.from(document.querySelectorAll('#conceptsList .chapter-row'));
      chapterRows.forEach((row) => {
        const items = Array.from(row.querySelectorAll('.concept-item'));
        let anyVisible = false;
        items.forEach((li) => {
          const hay = (li.dataset.search || '').toLowerCase();
          const ok = !q || hay.includes(q);
          li.style.display = ok ? '' : 'none';
          if (ok) anyVisible = true;
        });
        row.style.display = anyVisible ? '' : 'none';
      });
    }

    async function loadConcepts(reanalyze) {
      const btnReanalyze = document.getElementById('btnReanalyze');
      const conceptsListEl = document.getElementById('conceptsList');
      const conceptsHint = document.getElementById('conceptsHint');
      const conceptsCount = document.getElementById('conceptsCount');
      if (reanalyze && btnReanalyze) {
        btnReanalyze.disabled = true;
        btnReanalyze.textContent = 'Re-analyzing…';
      }
      try {
        const url = reanalyze ? '/api/concepts?reanalyze=true' : '/api/concepts';
        const r = await fetch(url);
        const d = await r.json();
        conceptsData = d.concepts || [];
        if (conceptsCount) conceptsCount.textContent = conceptsData.length ? (conceptsData.length + ' skills') : '';
        conceptsListEl.innerHTML = '';
        const byChapter = {};
        conceptsData.forEach(c => {
          const ch = c.chapter || 'Other';
          if (!byChapter[ch]) byChapter[ch] = [];
          byChapter[ch].push(c);
        });
        const chapterKeys = Object.keys(byChapter).sort((a, b) => {
          const numA = parseInt(a.match(/\\d+/)?.[0] || '0', 10);
          const numB = parseInt(b.match(/\\d+/)?.[0] || '0', 10);
          if (numA !== numB) return numA - numB;
          return a.localeCompare(b);
        });
        const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('');
        const badgeColors = ['#3b82f6', '#a855f7', '#f97316', '#10b981', '#ef4444', '#0ea5e9', '#f59e0b', '#22c55e', '#6366f1'];
        chapterKeys.forEach((chapter, idx) => {
          const row = document.createElement('li');
          row.className = 'chapter-row';
          const header = document.createElement('div');
          header.className = 'chapter-header';
          const badge = document.createElement('div');
          badge.className = 'chapter-badge';
          badge.textContent = letters[idx] || String(idx + 1);
          badge.style.background = badgeColors[idx % badgeColors.length];

          const titleWrap = document.createElement('div');
          titleWrap.className = 'chapter-title-wrap';
          const title = document.createElement('div');
          title.className = 'chapter-title';
          // Display title without the "Chapter N:" prefix in the big heading when possible
          const m = String(chapter || '').match(/Chapter\\s*\\d+\\s*:\\s*(.+)/i);
          title.textContent = m ? m[1].trim() : chapter;
          const sub = document.createElement('div');
          sub.className = 'chapter-sub';
          sub.textContent = chapter;
          titleWrap.appendChild(title);
          titleWrap.appendChild(sub);

          header.appendChild(badge);
          header.appendChild(titleWrap);
          row.appendChild(header);
          const conceptsUl = document.createElement('ol');
          conceptsUl.className = 'chapter-concepts';
          byChapter[chapter].forEach(c => {
            const li = document.createElement('li');
            li.className = 'concept-item';
            li.dataset.chapter = c.chapter;
            li.dataset.concept = c.concept;
            li.dataset.search = (c.chapter || '') + ' ' + (c.concept || '') + ' ' + (c.status || '');
            const name = document.createElement('span');
            name.className = 'concept-name';
            name.textContent = c.concept || '';
            const status = document.createElement('span');
            status.className = 'concept-status';
            if (c.status) {
              status.textContent = c.status;
              const s = String(c.status).toLowerCase();
              if (s.includes('fully')) status.classList.add('mastered');
              else status.classList.add('partial');
            } else {
              status.textContent = 'practice';
            }
            li.appendChild(name);
            li.appendChild(status);
            li.addEventListener('click', (e) => { e.stopPropagation(); setSelected(c); });
            conceptsUl.appendChild(li);
          });
          row.appendChild(conceptsUl);
          conceptsListEl.appendChild(row);
        });
        if (conceptsHint) {
          if (conceptsData.length === 0 && d.message) {
            conceptsHint.textContent = d.message;
            conceptsHint.style.display = 'block';
          } else {
            conceptsHint.textContent = '';
            conceptsHint.style.display = 'none';
          }
        }
        setSelected(null);
        applyConceptFilter();
      } catch (e) {
        if (conceptsHint) conceptsHint.style.display = 'none';
        conceptsListEl.innerHTML = '<li class="error">Could not load concepts. Try Re-analyze book.</li>';
      }
      if (btnReanalyze) {
        btnReanalyze.disabled = false;
        btnReanalyze.textContent = 'Re-analyze book';
      }
    }

    document.getElementById('btnReanalyze').addEventListener('click', () => loadConcepts(true));

    async function loadStudents() {
      try {
        const r = await fetch('/api/students');
        const d = await r.json();
        studentsList = d.students || [];
        currentStudentId = d.current_student_id || null;
        const sel = document.getElementById('studentSelect');
        sel.innerHTML = '';
        sel.appendChild(new Option('No student selected', ''));
        studentsList.forEach(s => {
          const opt = new Option(s.name + (s.is_current ? ' ✓' : ''), s.id);
          if (s.is_current) sel.value = s.id;
          sel.appendChild(opt);
        });
        if (currentStudentId) sel.value = currentStudentId;
        await updateProgressSummary();
      } catch (e) {
        console.warn('Could not load students', e);
      }
    }

    async function updateProgressSummary() {
      const el = document.getElementById('progressSummary');
      if (!el) return;
      try {
        const r = await fetch('/api/students/current');
        const d = await r.json();
        if (!d.profile) {
          el.textContent = 'Select or add a student to track progress.';
          return;
        }
        const prog = d.progress || {};
        const status = prog.concept_status || {};
        const mastered = Object.values(status).filter(s => s === 'fully understood').length;
        const history = prog.quiz_history || [];
        const recent = history.slice(-5).reverse();
        let text = mastered + ' concept(s) mastered.';
        if (recent.length) text += ' Recent: ' + recent.map(q => q.concept.split(' → ').pop() + ' ' + (q.passed ? '✓' : '✗') + ' ' + q.score_pct + '%').join(', ');
        el.textContent = text;
      } catch (e) {
        el.textContent = '';
      }
    }

    document.getElementById('studentSelect').addEventListener('change', async () => {
      const id = document.getElementById('studentSelect').value;
      if (!id) return;
      try {
        await fetch('/api/students/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ student_id: id }) });
        currentStudentId = id;
        await loadStudents();
        loadConcepts(false);
      } catch (e) {
        console.warn('Select student failed', e);
      }
    });

    document.getElementById('btnAddStudent').addEventListener('click', async () => {
      const nameEl = document.getElementById('newStudentName');
      const name = (nameEl && nameEl.value || '').trim() || 'Student';
      try {
        await fetch('/api/students', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
        if (nameEl) nameEl.value = '';
        await loadStudents();
        loadConcepts(false);
      } catch (e) {
        console.warn('Add student failed', e);
      }
    });

    let history = [];

    function updateQuizStatus() {
      const el = document.getElementById('quizStatus');
      if (!el) return;
      if (!quizPhase || !quizConcept) {
        el.style.display = 'none';
        return;
      }
      el.style.display = 'block';
      if (quizPhase === 'done') {
        const correct = quizResults.filter(Boolean).length;
        el.textContent = 'Quiz complete · ' + correct + '/' + NUM_QUESTIONS + ' correct';
      } else if (quizPhase === 'explain') {
        el.textContent = 'Quiz · Read the explanation, then click Ready for 5 questions';
      } else if (quizPhase.startsWith('q')) {
        const n = parseInt(quizPhase.slice(1), 10);
        const correct = quizResults.filter(Boolean).length;
        const total = quizResults.length;
        el.textContent = 'Question ' + n + ' of ' + NUM_QUESTIONS + (total ? ' · ' + correct + ' correct so far' : '');
      } else {
        el.textContent = '';
      }
    }

    function hideQuizChoices() {
      document.getElementById('quizChoiceBox').style.display = 'none';
      document.getElementById('quizChoiceQuestion').innerHTML = '';
      document.getElementById('quizChoiceOptions').innerHTML = '';
    }

    function showQuizChoices(options, isMultiple, questionText) {
      const box = document.getElementById('quizChoiceBox');
      document.getElementById('quizChoiceTitle').textContent = isMultiple
        ? 'Multiple choice – select all that apply:'
        : 'Multiple choice – choose one:';
      const questionEl = document.getElementById('quizChoiceQuestion');
      if (questionText && questionText.trim()) {
        questionEl.style.display = 'block';
        questionEl.innerHTML = typeof marked !== 'undefined' ? marked.parse(questionText) : questionText.replace(/\\n/g, '<br>').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      } else {
        questionEl.style.display = 'none';
      }
      const container = document.getElementById('quizChoiceOptions');
      container.innerHTML = '';
      const name = 'quiz_opt_' + Math.random().toString(36).slice(2);
      options.forEach((opt) => {
        const [letter, text] = opt;
        const label = document.createElement('label');
        const input = document.createElement('input');
        input.type = isMultiple ? 'checkbox' : 'radio';
        input.name = name;
        input.value = letter;
        label.appendChild(input);
        label.appendChild(document.createTextNode(letter + ') ' + text));
        container.appendChild(label);
      });
      box.style.display = 'block';
    }

    function sendMessage(text) {
      if (!text || !text.trim()) return;
      hideQuizChoices();
      document.getElementById('readyBtnWrap').style.display = 'none';
      const trimmed = text.trim();
      msg.value = '';
      append('user', trimmed);
      history.push({ role: 'user', content: trimmed });
      const { instruction: phaseInstruction, nextPhase } = getQuizPhaseInstruction(trimmed, quizPhase);
      quizPhase = nextPhase;
      updateQuizStatus();
      send.disabled = true;
      btnExplain.disabled = true;
      btnQuiz.disabled = true;
      const body = { message: trimmed, history: history.slice(0, -1) };
      if (phaseInstruction) body.phase_instruction = phaseInstruction;
      fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(r => {
        if (!r.ok) return r.json().then(err => { throw new Error(err.detail || r.statusText); });
        return r.json();
      }).then(d => {
        if (d.last_answer_correct !== undefined && d.last_answer_correct !== null) {
          quizResults.push(d.last_answer_correct);
        }
        append('coach', d.reply);
        history.push({ role: 'assistant', content: d.reply });
        if (d.options && d.options.length === 4) {
          showQuizChoices(d.options, d.is_multiple, d.question_text || '');
        }
        if (quizPhase === 'explain') document.getElementById('readyBtnWrap').style.display = 'block';
        if (quizPhase === 'done' && quizConcept && quizResults.length === NUM_QUESTIONS) {
          const conceptLabel = typeof quizConcept === 'object' ? (quizConcept.chapter + ' → ' + quizConcept.concept) : String(quizConcept);
          const correct = quizResults.filter(Boolean).length;
          const scorePct = Math.round((correct / NUM_QUESTIONS) * 100);
          const passed = scorePct >= PASS_PERCENT;
          fetch('/api/students/current/quiz-result', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ concept: conceptLabel, score_pct: scorePct, passed })
          }).then(() => updateProgressSummary()).catch(() => {});
        }
        updateQuizStatus();
      }).catch(e => {
        append('coach', 'Error: ' + e.message, 'error');
      }).finally(() => {
        send.disabled = false;
        if (selectedConcept) { btnExplain.disabled = false; btnQuiz.disabled = false; }
      });
    }

    document.getElementById('readyBtn').addEventListener('click', () => sendMessage('READY'));

    document.getElementById('submitQuizBtn').addEventListener('click', () => {
      const box = document.getElementById('quizChoiceBox');
      if (box.style.display === 'none') return;
      const container = document.getElementById('quizChoiceOptions');
      const isMultiple = document.getElementById('quizChoiceTitle').textContent.includes('all that apply');
      let answer;
      if (isMultiple) {
        const selected = Array.from(container.querySelectorAll('input:checked')).map(el => el.value).sort();
        answer = selected.length ? 'My answer: ' + selected.join(' and ') : null;
      } else {
        const checked = container.querySelector('input:checked');
        answer = checked ? 'My answer: ' + checked.value : null;
      }
      if (answer) {
        hideQuizChoices();
        sendMessage(answer);
      }
    });

    async function doSend() {
      sendMessage((msg.value || '').trim());
    }

    btnExplain.addEventListener('click', () => {
      if (!selectedConcept) return;
      setCoachMode(true);
      sendMessage('Explain this concept from the book: ' + selectedConcept.chapter + ' → ' + selectedConcept.concept);
    });
    btnQuiz.addEventListener('click', () => {
      if (!selectedConcept) return;
      setCoachMode(true);
      quizConcept = selectedConcept;
      quizPhase = 'explain';
      quizResults = [];
      updateQuizStatus();
      sendMessage('Start quiz for concept: ' + selectedConcept.concept + '. Explain this concept from the book using the standard structure: highlight the concept name, then definition, 3–5 examples (describe any diagrams in words), real-world example if any, then **Important things to remember** (3–5 bullet points). After that, say: When ready for 5 questions, type READY.');
    });

    send.addEventListener('click', doSend);
    msg.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doSend(); } });
    if (conceptSearch) conceptSearch.addEventListener('input', applyConceptFilter);

    loadStudents().then(() => loadConcepts());
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
