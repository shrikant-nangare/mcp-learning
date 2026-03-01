# Maths Olympiad Mentor – Architecture & Flow

## 1. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER (Grade 6 student)                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 GUI (Tkinter)                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Start test / │  │ Load PDF     │  │ Chat log     │  │ Input + Send /   │ │
│  │ Just chat    │  │ (optional)   │  │ (messages)   │  │ I'm ready        │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
     │                        │                    │                    │
     │                        │                    │                    │
     ▼                        ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APP LAYER                                       │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ Mode                │  │ Test state          │  │ PDF context         │  │
│  │ • test | chat       │  │ • topic              │  │ • loaded_pdf_text   │  │
│  │                     │  │ • phase              │  │ • loaded_pdf_name   │  │
│  │                     │  │   (concept → q1..q5  │  │                     │  │
│  │                     │  │    → summary → done) │  │                     │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                                                               │
│  • Builds per-turn prompt (system + phase instruction + history)             │
│  • Sends request → receives reply → updates state & UI                        │
└─────────────────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AGENT (OpenAI)                                  │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │ System prompt       │  │ PDF text (optional) │  │ Conversation        │  │
│  │ (coach persona +    │  │ injected into       │  │ history + current   │  │
│  │  formatting rules)  │  │ context             │  │ phase instruction   │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OpenAI API (e.g. gpt-5.2)                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component responsibilities

| Component      | Responsibility |
|----------------|----------------|
| **GUI**        | User input, show messages, “Start test” / “Just chat”, topic choice, “I’m ready”, Load PDF, progress (Concept → Q1…Q5 → Summary). |
| **App state**  | `mode` (test \| chat), `topic`, `phase`, optional `loaded_pdf_text`. |
| **App logic**  | On each user message: compute current phase, build phase-specific instruction, call agent with (history + phase instruction + optional PDF), then update phase and UI. |
| **Agent**      | Single `get_reply(history, pdf_text=..., phase_instruction=...)` that builds API input (system + PDF + history + phase instruction) and returns coach reply. |
| **OpenAI API** | Produces coach text (explanation, one question, feedback, or summary). |

---

## 3. State machine (test mode)

```
                    ┌─────────────┐
                    │   START     │
                    └──────┬──────┘
                           │ user: "Start test" + topic
                           ▼
                    ┌─────────────┐
                    │  CONCEPT    │  Coach explains topic; says "Type READY"
                    └──────┬──────┘
                           │ user: "READY" / "I'm ready"
                           ▼
                    ┌─────────────┐
                    │  READY_WAIT │  (transient) → next: ask Q1
                    └──────┬──────┘
                           │
                           ▼
    ┌──────────────► ┌─────────────┐
    │                │    Q1       │  Coach asks question 1 only
    │                └──────┬──────┘
    │                        │ user: answer
    │                        ▼
    │                ┌─────────────┐
    │                │    Q2       │  Coach evaluates, asks question 2 only
    │                └──────┬──────┘
    │                        │
    │         ... same for Q3, Q4, Q5 ...
    │                        │
    │                        ▼
    │                ┌─────────────┐
    │                │    Q5       │  Coach evaluates, asks question 5 only
    │                └──────┬──────┘
    │                        │ user: answer
    │                        ▼
    │                ┌─────────────┐
    │                │  SUMMARY    │  Coach evaluates + "You got X/5" + encouragement
    │                └──────┬──────┘
    │                        │
    │                        ▼
    │                ┌─────────────┐
    └────────────────│    DONE     │  Option: "Start another test" or "Just chat"
                     └─────────────┘
```

---

## 4. User flow (sequence)

```
  User           GUI              App state           Agent              OpenAI
   │               │                    │                │                  │
   │  Start test   │                    │                │                  │
   │  Topic: "Divisibility"              │                │                  │
   │──────────────►│  phase=concept      │                │                  │
   │               │  topic=Divisibility │                │                  │
   │               │  build: system + "Explain topic, then ask READY"         │
   │               │─────────────────────────────────────►│─────────────────►│
   │               │                    │                │◄─────────────────│
   │               │  show explanation  │                │                  │
   │◄──────────────│  "Type READY when ready"             │                  │
   │               │                    │                │                  │
   │  READY        │                    │                │                  │
   │──────────────►│  phase=q1          │                │                  │
   │               │  build: history + "Ask question 1 of 5 only"            │
   │               │─────────────────────────────────────►│─────────────────►│
   │               │                    │                │◄─────────────────│
   │◄──────────────│  show Q1           │                │                  │
   │               │                    │                │                  │
   │  Answer 1     │                    │                │                  │
   │──────────────►│  phase=q2          │                │                  │
   │               │  build: history + "Evaluate, then ask Q2 only"          │
   │               │─────────────────────────────────────►│─────────────────►│
   │               │                    │                │◄─────────────────│
   │◄──────────────│  show feedback + Q2│                │                  │
   │               │                    │                │                  │
   │  ...          │  ...               │  ...           │  ...              │
   │               │                    │                │                  │
   │  Answer 5     │  phase=summary     │                │                  │
   │──────────────►│  build: "Evaluate, then summary X/5, no more questions" │
   │               │─────────────────────────────────────►│─────────────────►│
   │◄──────────────│  show summary      │  phase=done    │                  │
   │               │                    │                │                  │
   │  Start another test / Just chat    │                │                  │
   │──────────────►│  reset or switch mode               │                  │
```

---

## 5. Flow diagram (Mermaid)

### 5.1 Main user flow

```mermaid
flowchart TD
    A([Start]) --> B{Choose mode}
    B -->|Start test| C[Select topic]
    B -->|Just chat| Z[Free-form chat]
    C --> D[Coach explains concept]
    D --> E[Coach: Type READY for 5 questions]
    E --> F[User: READY]
    F --> G[Coach asks Q1]
    G --> H[User answers]
    H --> I[Coach: feedback + Q2]
    I --> J[User answers]
    J --> K[Coach: feedback + Q3]
    K --> L[User answers]
    L --> M[Coach: feedback + Q4]
    M --> N[User answers]
    N --> O[Coach: feedback + Q5]
    O --> P[User answers]
    P --> Q[Coach: summary X/5 + encouragement]
    Q --> R{Next?}
    R -->|Another test| C
    R -->|Just chat| Z
    R -->|Done| S([End])
    Z --> S
```

### 5.2 Phase → prompt injection

```mermaid
flowchart LR
    subgraph Phases
        P0[concept]
        P1[ready_wait]
        P2[q1]
        P3[q2]
        P4[q3]
        P5[q4]
        P6[q5]
        P7[summary]
        P8[done]
    end
    subgraph Instructions
        I0["Explain topic. End with: Type READY."]
        I1["Ask question 1 of 5 only."]
        I2["Evaluate. Ask question 2 of 5 only."]
        I3["Evaluate. Ask question 3 of 5 only."]
        I4["Evaluate. Ask question 4 of 5 only."]
        I5["Evaluate. Ask question 5 of 5 only."]
        I6["Evaluate. Summary X/5. No more questions."]
    end
    P0 --> I0
    P1 --> I1
    P2 --> I2
    P3 --> I3
    P4 --> I4
    P5 --> I5
    P6 --> I6
```

### 5.3 Data flow (test mode, one turn)

```mermaid
flowchart LR
    subgraph Inputs
        U[User message]
        S[App state: phase, topic]
        H[Conversation history]
        PDF[PDF text optional]
    end
    subgraph Build
        PI[Phase instruction]
        SP[System prompt]
        IN[API input]
    end
    subgraph API
        O[OpenAI]
        R[Coach reply]
    end
    U --> H
    S --> PI
    SP --> IN
    H --> IN
    PI --> IN
    PDF --> IN
    IN --> O
    O --> R
    R --> H
    R --> UI[Update UI]
    S --> UI
```

---

## 6. File / module map (proposed)

```
openai/math-olympiad-mentor/
├── agent.py              # get_reply(..., pdf_text, phase_instruction), extract_pdf_text
├── app_gui.py            # GUI, state (mode, phase, topic), send() + phase logic
├── state.py              # (optional) Phase enum, next_phase(), get_phase_instruction()
├── requirements.txt
└── docs/
    └── architecture-and-flow.md   # this file
```

---

## 7. Summary

| Item | Description |
|------|-------------|
| **Architecture** | GUI → App state & logic → Agent (builds prompt) → OpenAI API. |
| **Flow** | Start test → Topic → Concept → READY → Q1 → answer → … → Q5 → answer → Summary → Done / Another test. |
| **State** | `mode`, `topic`, `phase` (concept \| q1..q5 \| summary \| done). |
| **Key mechanism** | App injects a **phase instruction** every turn so the coach does exactly one thing (explain, ask one question, evaluate + next question, or summary). |
| **PDF** | Optional; if present, injected into context so concept/questions can be from the PDF. |
