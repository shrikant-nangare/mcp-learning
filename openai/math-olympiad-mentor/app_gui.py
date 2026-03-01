"""
Grade 6 Maths Olympiad Coach – GUI app

Chat-style window: type your question, get the coach's reply.
Renders coach replies as HTML (tables, lists, bold) if tkhtmlview and markdown
are installed; otherwise falls back to plain text.
"""

import html as html_lib
import re
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont, scrolledtext, filedialog

from agent import get_reply, extract_pdf_text

# Optional: nice HTML rendering
try:
    from tkhtmlview import HTMLScrolledText
    import markdown
    HAS_HTML = True
except ImportError:
    HAS_HTML = False

# Base CSS for the chat content when using HTML
_CHAT_CSS = """
<style>
body { font-family: system-ui, sans-serif; font-size: 12pt; color: black; padding: 8px; }
.msg-user { margin: 10px 0; padding: 8px; background: #eff6ff; border-left: 3px solid #2563eb; }
.msg-coach { margin: 10px 0; padding: 8px; background: #f0fdf4; border-left: 3px solid #059669; }
.msg-label { font-weight: bold; margin-bottom: 4px; }
table { border-collapse: collapse; margin: 8px 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; }
th { background: #f3f4f6; }
ul, ol { margin: 6px 0; padding-left: 24px; }
p { margin: 6px 0; }
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
    """Convert markdown to HTML (tables, lists, bold, etc.)."""
    if not HAS_HTML or not text:
        return html_lib.escape(text)
    try:
        html = markdown.markdown(
            text.strip(),
            extensions=["tables", "nl2br"],
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


def run_gui() -> None:
    root = tk.Tk()
    root.title("Grade 6 Maths Olympiad Coach")
    root.geometry("700x560")
    root.minsize(400, 300)

    history: list[dict] = []
    display_messages: list[dict] = []
    update_queue: queue.Queue[str | None] = queue.Queue()
    loaded_pdf_text: str | None = None
    loaded_pdf_name: str | None = None

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
        nonlocal loaded_pdf_text, loaded_pdf_name
        path = filedialog.askopenfilename(
            title="Select a PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            loaded_pdf_text = extract_pdf_text(path)
            loaded_pdf_name = path.split("/")[-1].split("\\")[-1]
            update_pdf_status()
            msg = f"**Loaded PDF:** {loaded_pdf_name} ({len(loaded_pdf_text):,} characters). You can ask questions about it, or I can ask you questions from it."
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
        except Exception as e:
            err = f"Could not load PDF: {e}"
            display_messages.append({"role": "assistant", "content": err})
            if HAS_HTML:
                set_log_html(_build_chat_html(display_messages))
            else:
                log.config(state=tk.NORMAL)
                log.insert(tk.END, "Coach: ", "coach_label")
                log.insert(tk.END, err + "\n\n", "coach")
                log.config(state=tk.DISABLED)
            log.see(tk.END)

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
        fg="white",
        activebackground="#047857",
        activeforeground="white",
        relief=tk.FLAT,
        padx=12,
        pady=4,
        cursor="hand2",
    )
    load_pdf_btn.pack(side=tk.LEFT)
    pdf_status = tk.Label(btn_frame, text="No PDF loaded", font=base_font, fg="#6b7280", bg="#e5e7eb")
    pdf_status.pack(side=tk.LEFT, padx=(8, 0))

    def update_pdf_status() -> None:
        if loaded_pdf_name:
            pdf_status.config(text=f"PDF: {loaded_pdf_name}", fg="black")
        else:
            pdf_status.config(text="No PDF loaded", fg="#6b7280")

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

    def send() -> None:
        text = entry.get("1.0", tk.END).strip()
        if not text:
            return
        entry.delete("1.0", tk.END)
        entry.focus_set()

        history.append({"role": "user", "content": text})
        display_messages.append({"role": "user", "content": text})

        if HAS_HTML:
            display_messages.append({"role": "assistant", "content": "Thinking…"})
            set_log_html(_build_chat_html(display_messages))
            log.see(tk.END)
        else:
            log.config(state=tk.NORMAL)
            log.insert(tk.END, "You: ", "you_label")
            log.insert(tk.END, text + "\n\n", "you")
            log.insert(tk.END, "Coach: ", "coach_label")
            thinking_start = log.index(tk.END)
            log.insert(tk.END, "Thinking…\n\n", "thinking")
            thinking_end = log.index(tk.END)
            log.config(state=tk.DISABLED)
            log.see(tk.END)
            reply_range = [thinking_start, thinking_end]

        def worker() -> None:
            try:
                reply = get_reply(history, model="gpt-5.2", pdf_text=loaded_pdf_text)
                history.append({"role": "assistant", "content": reply})
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
                display_messages.pop()
                display_messages.append({"role": "assistant", "content": reply})
                set_log_html(_build_chat_html(display_messages))
                log.see(tk.END)
            else:
                start, end = reply_range
                log.config(state=tk.NORMAL)
                log.delete(start, end)
                plain = _latex_to_plain(reply.strip())
                log.insert(start, plain + "\n\n")
                log.config(state=tk.DISABLED)
                log.see(tk.END)
            entry.focus_set()

        root.after(100, poll_queue)

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
        fg="white",
        activebackground="#1d4ed8",
        activeforeground="white",
        relief=tk.FLAT,
        padx=16,
        pady=6,
        cursor="hand2",
    )
    btn.pack(side=tk.RIGHT)

    root.after(50, entry.focus_set)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
