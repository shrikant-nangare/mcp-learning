"""
Grade 6 Maths Olympiad Agent

An OpenAI-powered agent that acts as a coach for 6th grade math olympiad
problems: explains concepts, gives hints, solves step-by-step, and suggests
similar practice problems. Supports single-turn (ask) and multi-turn chat.
Can use a loaded PDF as reference to answer questions and ask questions from it.
"""

from openai import OpenAI

# Max PDF text length to send to the API (avoid token limits)
MAX_PDF_CONTEXT_CHARS = 80_000

SYSTEM_PROMPT = """You are a friendly, expert Maths Olympiad coach for Grade 6 students (ages 11–12).

Your role:
- Explain ideas clearly with simple language and short steps.
- When solving problems, show your work step by step so the student can follow.
- If the student is stuck, give a small hint first instead of the full answer.
- Cover typical olympiad areas: arithmetic, number theory (divisibility, factors, primes), basic algebra (equations, expressions), geometry (areas, angles, simple proofs), combinatorics (counting, simple probability), and logical reasoning.
- Use examples and, when helpful, small diagrams described in text (e.g. "draw a 3×4 grid").
- Encourage the student and praise good reasoning.
- If asked, suggest one or two similar practice problems at the same level.

Keep explanations concise but complete. Avoid jargon; if you use a term (e.g. "LCM", "prime"), briefly remind what it means when first used.

Formatting: Use Markdown so your answer can be rendered nicely: **bold**, lists with - or 1., and tables like:
| Col A | Col B |
|-------|-------|
| 1     | 2     |
Use Unicode for math symbols: × ÷ − ². Do not use LaTeX (no \\( \\), \\[ \\], \\times, \\boxed)."""

PDF_CONTEXT_INSTRUCTION = """
When reference material from a PDF is provided below, use it to:
- Answer the student's questions about the content.
- Ask the student questions based on the material (e.g. "From the PDF, can you explain...?" or "Try this problem from the handout.").
- Refer to specific sections or problems when helpful.
"""


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


def _build_input(
    system_prompt: str,
    history: list[dict],
    pdf_text: str | None = None,
) -> list[dict]:
    """Build API input from system prompt, optional PDF context, and conversation history."""
    prompt = system_prompt
    if pdf_text:
        trimmed = pdf_text.strip()
        if len(trimmed) > MAX_PDF_CONTEXT_CHARS:
            trimmed = trimmed[:MAX_PDF_CONTEXT_CHARS] + "\n\n[... text truncated ...]"
        prompt = prompt + PDF_CONTEXT_INSTRUCTION + "\n\n--- Reference material (PDF) ---\n\n" + trimmed + "\n\n--- End of reference ---"
    return [{"role": "system", "content": prompt}] + history


def get_reply(
    history: list[dict],
    *,
    client: OpenAI | None = None,
    model: str = "gpt-5.2",
    pdf_text: str | None = None,
) -> str:
    """
    Get the next assistant reply for the given conversation history.
    If pdf_text is provided, the coach can answer and ask questions from that content.
    Does not print anything. Used by the GUI.
    """
    if client is None:
        client = OpenAI()
    input_messages = _build_input(SYSTEM_PROMPT, history, pdf_text=pdf_text)
    response = client.responses.create(model=model, input=input_messages)
    return getattr(response, "output_text", None) or ""


def ask(
    message: str,
    *,
    client: OpenAI | None = None,
    model: str = "gpt-5.2",
    stream: bool = True,
) -> str:
    """
    Send a message to the Maths Olympiad agent and return the assistant's reply.

    message: The student's question or problem (e.g. a problem statement or "I'm stuck").
    client: Optional OpenAI client; uses default if not provided.
    model: Model to use.
    stream: If True, stream the response and print as it arrives; always returns full text.
    """
    if client is None:
        client = OpenAI()

    input_messages = _build_input(SYSTEM_PROMPT, [{"role": "user", "content": message}])

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

    response = client.responses.create(
        model=model,
        input=input_messages,
    )
    text = getattr(response, "output_text", None) or ""
    print(text)
    return text


def chat(
    *,
    client: OpenAI | None = None,
    model: str = "gpt-5.2",
    stream: bool = True,
) -> None:
    """
    Run an interactive chat session with the Maths Olympiad coach.
    Conversation history is kept so you can ask follow-ups.
    Type 'quit', 'exit', or 'q' to end the session.
    """
    if client is None:
        client = OpenAI()

    history: list[dict] = []

    print("=== Grade 6 Maths Olympiad Coach (chat) ===\n")
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
            response = client.responses.create(
                model=model,
                input=input_messages,
            )
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
